import pytest
from fastapi.testclient import TestClient
from zipfile import ZipFile

from app.aps.client import ApsClient
from app.config import get_settings
from app.db import TaskStore
from app.main import app
from app.unity.compare import compare_dummy_dirs
from app.unity.dumper import extract_unity_inputs, looks_like_unity_package
from app.worker.executor import TaskExecutor


@pytest.fixture(autouse=True)
def clear_openai_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


def client(tmp_path):
    get_settings.cache_clear()
    settings = get_settings()
    settings.data_dir = tmp_path / "data"
    settings.work_dir = tmp_path / "work"
    settings.db_path = tmp_path / "data" / "tasks.sqlite"
    settings.auth_enabled = False
    settings.ensure_directories()
    return TestClient(app)


def test_health_and_discover(tmp_path):
    c = client(tmp_path)
    assert c.get("/health").json() == {"status": "ok"}
    body = c.get("/discover").json()
    assert body["name"] == "Android Unity Compare Service"
    assert "/api/v1/comparisons" in body["auth"]["api_key_endpoints"]
    assert "OPENAI_API_KEY" in body["config"]["variables"]


def test_create_and_get_batch_task(tmp_path):
    c = client(tmp_path)
    response = c.post(
        "/api/v1/batch-comparisons",
        json={
            "packageName": "com.example.game",
            "versions": [
                {"versionCode": "102", "versionName": "1.0.2"},
                {"versionCode": "100", "versionName": "1.0.0"},
                {"versionCode": "101", "versionName": "1.0.1"},
            ],
        },
    )
    assert response.status_code == 202
    task = c.get(f"/api/v1/tasks/{response.json()['taskId']}").json()
    assert task["status"] == "queued"
    assert task["progress"] == {
        "versionsTotal": 3,
        "versionsDownloaded": 0,
        "versionsDumped": 0,
        "comparisonsTotal": 2,
        "comparisonsCompleted": 0,
        "comparisonsFailed": 0,
    }
    assert task["comparisons"][0]["oldVersion"] == "1.0.0"
    assert task["comparisons"][1]["newVersion"] == "1.0.2"


def test_api_key_gate(tmp_path):
    c = client(tmp_path)
    settings = get_settings()
    settings.auth_enabled = True
    settings.api_keys = "secret"
    try:
        assert c.get("/api/v1/tasks/missing").status_code == 401
        assert c.get("/api/v1/tasks/missing", headers={"X-API-Key": "secret"}).status_code == 404
    finally:
        get_settings.cache_clear()


def test_worker_executor_marks_task_done(tmp_path, monkeypatch):
    c = client(tmp_path)
    task_id = c.post(
        "/api/v1/comparisons",
        json={
            "packageName": "com.example.game",
            "oldVersion": {"versionCode": "100", "versionName": "1.0.0"},
            "newVersion": {"versionCode": "101", "versionName": "1.0.1"},
        },
    ).json()["taskId"]

    settings = get_settings()
    store = TaskStore(settings.task_db_path)
    assert store.claim_tasks(1) == [task_id]
    monkeypatch.setattr("app.worker.executor.dump_package", fake_dump_package)
    TaskExecutor(settings, store, FakeApsClient(unity=True)).run(task_id)

    task = c.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "succeeded"
    assert task["progress"]["versionsDumped"] == 2
    assert task["progress"]["comparisonsCompleted"] == 1


def test_worker_executor_fails_pair_for_non_unity_package(tmp_path):
    c = client(tmp_path)
    task_id = c.post(
        "/api/v1/comparisons",
        json={
            "packageName": "com.example.game",
            "oldVersion": {"versionCode": "100", "versionName": "1.0.0"},
            "newVersion": {"versionCode": "101", "versionName": "1.0.1"},
        },
    ).json()["taskId"]

    settings = get_settings()
    store = TaskStore(settings.task_db_path)
    assert store.claim_tasks(1) == [task_id]
    TaskExecutor(settings, store, FakeApsClient(unity=False)).run(task_id)

    task = c.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "failed"
    assert task["progress"]["comparisonsFailed"] == 1
    assert task["versions"][0]["status"] == "unity_unsupported"


def test_aps_client_downloads_202_file_url(tmp_path):
    import asyncio

    settings = get_settings()
    settings.aps_base_url = "http://aps.local"
    settings.aps_job_poll_seconds = 0
    target = tmp_path / "app.apk"

    asyncio.run(
        ApsClient(settings)._download_response(
            FakeAsyncClient(),
            "http://aps.local/api/v1/android/apps/pkg/download",
            target,
            headers={},
            params={},
        )
    )

    assert target.exists()
    assert target.stat().st_size > 0


def test_unity_detector_reads_nested_xapk(tmp_path):
    xapk = tmp_path / "game.xapk"
    nested = tmp_path / "base.apk"
    with ZipFile(nested, "w") as archive:
        archive.writestr("lib/arm64-v8a/libil2cpp.so", b"lib")
        archive.writestr("assets/bin/Data/Managed/Metadata/global-metadata.dat", b"metadata")
    with ZipFile(xapk, "w") as archive:
        archive.write(nested, "base.apk")

    assert looks_like_unity_package(xapk)
    libil2cpp, metadata = extract_unity_inputs(xapk, tmp_path / "inputs")
    assert libil2cpp.read_bytes() == b"lib"
    assert metadata.read_bytes() == b"metadata"


def test_compare_report_keeps_monitor_content_contract(tmp_path, monkeypatch):
    old_dir = tmp_path / "old" / "DummyDll"
    new_dir = tmp_path / "new" / "DummyDll"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    for name in ["Assembly-CSharp.dll", "Sdk.dll", "Removed.dll"]:
        (old_dir / name).write_bytes(b"old")
    for name in ["Assembly-CSharp.dll", "Sdk.dll", "Added.dll"]:
        (new_dir / name).write_bytes(b"new")
    analyzer = tmp_path / "DllAnalyzer"
    analyzer.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr("app.unity.compare.analyze_dll", fake_analyze_dll)
    artifacts = compare_dummy_dirs(
        old_dir,
        new_dir,
        tmp_path / "reports",
        metadata={"package_name": "com.example.game", "old_version_name": "1.0.0", "new_version_name": "1.0.1"},
        dll_analyzer_path=analyzer,
    )

    report = artifacts.report
    assert set(report) == {
        "timestamp",
        "old_directory",
        "new_directory",
        "app_name",
        "old_version_name",
        "new_version_name",
        "overall_statistics",
        "summary",
        "dll_comparisons",
        "detailed_game_logic_changes",
    }
    assert set(report["summary"]) == {
        "added_dlls",
        "removed_dlls",
        "changed_dlls",
        "unchanged_dlls",
        "version_only_changes",
        "content_changes",
    }
    assert "analysis_failed_dll_count" not in report["overall_statistics"]
    assert report["summary"]["added_dlls"] == ["Added.dll"]
    assert report["summary"]["removed_dlls"] == ["Removed.dll"]
    assert report["summary"]["content_changes"] == ["Assembly-CSharp.dll"]
    assert report["summary"]["version_only_changes"] == ["Sdk.dll"]
    assert artifacts.json_path.exists()
    assert artifacts.html_path.exists()


def test_html_report_includes_ai_analysis_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setattr("app.unity.report.httpx.post", fake_openai_post)
    old_dir = tmp_path / "old" / "DummyDll"
    new_dir = tmp_path / "new" / "DummyDll"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    for folder in (old_dir, new_dir):
        (folder / "Sdk.dll").write_bytes(b"dll")
    analyzer = tmp_path / "DllAnalyzer"
    analyzer.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr("app.unity.compare.analyze_dll", fake_analyze_dll)
    artifacts = compare_dummy_dirs(
        old_dir,
        new_dir,
        tmp_path / "reports",
        metadata={"package_name": "com.example.game", "old_version_name": "1.0.0", "new_version_name": "1.0.1"},
        dll_analyzer_path=analyzer,
    )

    html = artifacts.html_path.read_text(encoding="utf-8")
    assert "data-markdown=\"### **AI 智能分析**" in html
    assert "AI 分析生成失败" not in html


class FakeApsClient:
    def __init__(self, unity: bool):
        self.unity = unity

    async def download(self, package_name, version, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(target, "w") as archive:
            if self.unity:
                archive.writestr("lib/arm64-v8a/libil2cpp.so", b"lib")
                archive.writestr("assets/bin/Data/Managed/Metadata/global-metadata.dat", b"metadata")
            else:
                archive.writestr("classes.dex", b"dex")
        return target


def fake_dump_package(package_path, output_dir, **kwargs):
    dummy = output_dir / "DummyDll"
    dummy.mkdir(parents=True, exist_ok=True)
    return dummy


def fake_analyze_dll(dll_path, analyzer, timeout_seconds):
    if dll_path.name == "Assembly-CSharp.dll":
        methods = ["Game.Player::Move"]
        if dll_path.parent.parent.name == "new":
            methods.append("Game.Player::Jump")
        return {
            "AssemblyName": "Assembly-CSharp",
            "Version": "1.0.0.0",
            "Classes": [
                {
                    "FullName": "Game.Player",
                    "Namespace": "Game",
                    "Name": "Player",
                    "Methods": methods,
                    "Fields": [],
                    "Properties": [],
                    "Attributes": {},
                }
            ],
            "SdkVersions": {},
        }
    if dll_path.name == "Sdk.dll":
        version = "1.0.0" if dll_path.parent.parent.name == "old" else "1.1.0"
        return {"AssemblyName": "Sdk", "Version": version, "Classes": [], "SdkVersions": {}}
    return {"AssemblyName": dll_path.stem, "Version": "1.0.0", "Classes": [], "SdkVersions": {}}


def fake_openai_post(*args, **kwargs):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "### **AI 智能分析**\n\n测试分析"}}]}

    return Response()


class FakeAsyncClient:
    def __init__(self):
        self.status_calls = 0

    def stream(self, method, url, params=None, headers=None):
        if url.endswith("/download"):
            return FakeStreamResponse(202, b'{"statusUrl": "/jobs/1"}')
        return FakeStreamResponse(200, unity_zip_bytes())

    async def get(self, url, headers=None):
        self.status_calls += 1
        return FakeJsonResponse({"status": "succeeded", "fileUrl": "/files/1.apk"})


class FakeStreamResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(self.status_code)

    async def aiter_bytes(self):
        yield self.body


class FakeJsonResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


def unity_zip_bytes():
    from io import BytesIO

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("lib/arm64-v8a/libil2cpp.so", b"lib")
        archive.writestr("assets/bin/Data/Managed/Metadata/global-metadata.dat", b"metadata")
    return buffer.getvalue()
