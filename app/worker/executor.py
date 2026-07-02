import asyncio
import json
from pathlib import Path

from app.aps.client import ApsClient
from app.config import Settings
from app.db import TaskStore
from app.models import PairStatus, TaskStatus, VersionRef, VersionStatus
from app.storage import build_report_storage
from app.unity.compare import compare_dummy_dirs
from app.unity.dumper import DumperNotConfigured, dump_package, looks_like_unity_package


class TaskExecutor:
    def __init__(self, settings: Settings, store: TaskStore, aps_client: ApsClient | None = None):
        self.settings = settings
        self.store = store
        self.aps_client = aps_client or ApsClient(settings)
        self.report_storage = build_report_storage(settings)

    def run(self, task_id: str) -> None:
        asyncio.run(self._run(task_id))

    async def _run(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task is None:
            return
        if self._is_cancelled(task_id):
            self._cleanup(task_id, failed=True)
            return

        try:
            await self._process_versions(task)
            if self._is_cancelled(task_id):
                self._cleanup(task_id, failed=True)
                return

            task = self.store.get_task(task_id)
            versions = {version["id"]: version for version in task["versions"]}
            if not task["comparisons"]:
                self._finish_unity_check(task_id, task["versions"])
            else:
                await self._finish_pairs(task_id, task["comparisons"], versions)
            if self._is_cancelled(task_id):
                self._cleanup(task_id, failed=True)
                return

            status = self._task_status(self.store.get_task(task_id))
            self.store.mark_task(task_id, status)
            self._cleanup(task_id, failed=status != TaskStatus.SUCCEEDED)
        except Exception as exc:
            if self._is_cancelled(task_id):
                self._cleanup(task_id, failed=True)
                return
            self.store.mark_task(task_id, TaskStatus.FAILED, str(exc))
            self._cleanup(task_id, failed=True)
            raise

    async def _process_versions(self, task: dict) -> None:
        download_semaphore = asyncio.Semaphore(self.settings.download_concurrency)
        dump_semaphore = asyncio.Semaphore(self.settings.dump_concurrency)

        async def process(version: dict) -> None:
            async with download_semaphore:
                target = await self._download_version(task, version)
            if target is None:
                return
            async with dump_semaphore:
                await asyncio.to_thread(self._check_and_dump, task, version, target)

        await asyncio.gather(*(process(version) for version in task["versions"]))

    async def _download_version(self, task: dict, version: dict) -> Path | None:
        target = Path(self.settings.work_dir) / task["taskId"] / "packages" / version["id"]
        self.store.mark_version(version["id"], VersionStatus.DOWNLOAD_RUNNING)
        try:
            target = await self.aps_client.download(
                task["packageName"],
                VersionRef(versionCode=version["versionCode"], versionName=version["versionName"]),
                target,
            )
            self.store.set_version_paths(version["id"], package_path=target)
            self.store.mark_version(version["id"], VersionStatus.DOWNLOAD_SUCCEEDED)
            return target
        except Exception as exc:
            self.store.mark_version(version["id"], VersionStatus.FAILED, str(exc))
            return None

    def _check_and_dump(self, task: dict, version: dict, target: Path) -> None:
        self.store.mark_version(version["id"], VersionStatus.DUMP_RUNNING)
        try:
            if not looks_like_unity_package(target):
                self.store.mark_version(version["id"], VersionStatus.UNITY_UNSUPPORTED, "包缺少 libil2cpp.so 或 global-metadata.dat")
                return
            dump_path = Path(self.settings.work_dir) / task["taskId"] / "dumps" / version["id"]
            try:
                dummy_dll = dump_package(
                    target,
                    dump_path,
                    il2cpp_dumper_path=self.settings.il2cpp_dumper_path,
                    timeout_seconds=self.settings.il2cpp_dumper_timeout_seconds,
                )
                self.store.set_version_paths(version["id"], dump_path=dummy_dll)
            except DumperNotConfigured:
                # ponytail: no bundled dumper yet; keep phase-1 worker useful until lib/product is added.
                self.store.set_version_paths(version["id"], dump_path=dump_path)
            self.store.mark_version(version["id"], VersionStatus.UNITY_DUMPABLE)
        except Exception as exc:
            self.store.mark_version(version["id"], VersionStatus.FAILED, str(exc))

    def _finish_unity_check(self, task_id: str, versions: list[dict]) -> None:
        version = versions[0]
        if version["status"] == VersionStatus.UNITY_DUMPABLE:
            package_name = self.store.get_task(task_id)["packageName"]
            report_path = Path(self.settings.work_dir) / task_id / "reports" / "unity-check.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "packageName": package_name,
                        "versionCode": version["versionCode"],
                        "versionName": version["versionName"],
                        "status": version["status"],
                        "dumpPath": version["dumpPath"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            object_key = f"{self.settings.report_storage_prefix}/{package_name}/{task_id}/unity-check.json"
            self.report_storage.upload_file(report_path, object_key, "application/json")
            self.store.add_artifact(task_id, None, "unity-check.json", object_key, "application/json")

    async def _finish_pairs(self, task_id: str, pairs: list[dict], versions: dict[str, dict]) -> None:
        semaphore = asyncio.Semaphore(self.settings.compare_concurrency)

        async def run_pair(pair: dict) -> None:
            async with semaphore:
                await asyncio.to_thread(self._finish_pair, task_id, pair, versions)

        await asyncio.gather(*(run_pair(pair) for pair in pairs))

    def _finish_pair(self, task_id: str, pair: dict, versions: dict[str, dict]) -> None:
        if self._is_cancelled(task_id):
            return
        old = versions[pair["oldVersionId"]]
        new = versions[pair["newVersionId"]]
        if old["status"] != VersionStatus.UNITY_DUMPABLE or new["status"] != VersionStatus.UNITY_DUMPABLE:
            self.store.mark_pair(pair["pairId"], PairStatus.FAILED, "pair 两端必须都是可 dump Unity 包")
            return
        self.store.mark_pair(pair["pairId"], PairStatus.COMPARING)
        try:
            package_name = self.store.get_task(task_id)["packageName"]
            report_dir = Path(self.settings.work_dir) / task_id / "reports" / pair["pairId"]
            artifacts = compare_dummy_dirs(
                Path(old["dumpPath"]),
                Path(new["dumpPath"]),
                report_dir,
                metadata={
                    "package_name": package_name,
                    "old_version_name": old["versionName"] or old["versionCode"],
                    "new_version_name": new["versionName"] or new["versionCode"],
                },
                dll_analyzer_path=self.settings.dll_analyzer_path,
                timeout_seconds=self.settings.dll_analyzer_timeout_seconds,
            )
            if self._is_cancelled(task_id):
                return
            self.store.mark_pair(pair["pairId"], PairStatus.UPLOADING)
            for source, content_type in ((artifacts.json_path, "application/json"), (artifacts.html_path, "text/html")):
                object_key = self._persist_artifact(package_name, task_id, pair["pairId"], source, content_type)
                self.store.add_artifact(task_id, pair["pairId"], source.name, object_key, content_type)
            self.store.mark_pair(pair["pairId"], PairStatus.SUCCEEDED)
        except Exception as exc:
            self.store.mark_pair(pair["pairId"], PairStatus.FAILED, str(exc))

    def _persist_artifact(self, package_name: str, task_id: str, pair_id: str, source: Path, content_type: str) -> str:
        object_key = f"{self.settings.report_storage_prefix}/{package_name}/{task_id}/{pair_id}/{source.name}"
        self.report_storage.upload_file(source, object_key, content_type)
        return object_key

    @staticmethod
    def _task_status(task: dict) -> TaskStatus:
        pairs = task["comparisons"]
        if not pairs:
            return TaskStatus.SUCCEEDED if task["versions"][0]["status"] == VersionStatus.UNITY_DUMPABLE else TaskStatus.FAILED
        failed = sum(1 for pair in pairs if pair["status"] == PairStatus.FAILED)
        if failed == 0:
            return TaskStatus.SUCCEEDED
        if failed == len(pairs):
            return TaskStatus.FAILED
        return TaskStatus.PARTIAL_FAILED

    def _cleanup(self, task_id: str, failed: bool) -> None:
        if failed and self.settings.keep_failed_work_dir:
            return
        task_dir = Path(self.settings.work_dir) / task_id
        if task_dir.exists():
            import shutil

            shutil.rmtree(task_dir, ignore_errors=True)

    def _is_cancelled(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        return bool(task and task["status"] == TaskStatus.CANCELLED)
