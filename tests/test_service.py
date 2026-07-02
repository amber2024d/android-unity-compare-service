import pytest
import http.server
import ast
import re
import socketserver
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from fastapi.testclient import TestClient
from zipfile import ZipFile

from app.aps.client import ApsClient
from app.config import get_settings
from app.db import TaskStore
from app.main import app
from app.models import PairCompareRequest, TaskStatus
from app.auth.service import AuthService
from app.unity.compare import compare_dummy_dirs
from app.unity.dumper import extract_unity_inputs, looks_like_unity_package
from app.worker.cleanup import remove_expired_work_dirs, remove_orphan_work_dirs
from app.worker.executor import TaskExecutor


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    assert "REPORT_STORAGE_BACKEND" in body["config"]["variables"]
    assert "REPORT_S3_BUCKET" in body["config"]["variables"]
    assert "OPENAI_API_KEY" in body["config"]["variables"]


def test_discover_lists_routes_and_env_example(tmp_path):
    c = client(tmp_path)
    body = c.get("/discover").json()
    normalize = str.maketrans({"_": ""})
    described_paths = {
        endpoint["path"].translate(normalize).lower()
        for group in body["endpoints"].values()
        for endpoint in group.values()
    }
    actual_paths = {
        route.path.translate(normalize).lower()
        for route in app.routes
        if hasattr(route, "path") and route.path not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    }
    env_keys = {
        line.split("=", 1)[0]
        for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }

    assert actual_paths <= described_paths
    assert env_keys <= set(body["config"]["variables"])
    assert body["config"]["variables"]["APS_BASE_URL"]["default"] == ""
    assert body["config"]["variables"]["APS_API_KEY"]["default"] == ""


def test_env_example_settings_and_compose_are_aligned():
    settings_keys = settings_env_keys()
    env_keys = env_example_keys()
    compose_keys = set(re.findall(r"^\s{6}([A-Z0-9_]+):", (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"), re.M))

    assert settings_keys <= env_keys
    assert env_keys <= settings_keys | {"HOST_PORT", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "WEB_CONCURRENCY", "GUNICORN_TIMEOUT_SECONDS"}
    assert env_keys == compose_keys | {"HOST_PORT"}
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "APS_BASE_URL: ${APS_BASE_URL:-}" in compose
    assert "DATA_DIR: /app/data" in compose
    assert "IL2CPP_DUMPER_PATH: /app/lib/product/Il2CppDumper/linux/Il2CppDumper" in compose


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


def test_cancel_queued_task_and_retry(tmp_path):
    c = client(tmp_path)
    task_id = c.post(
        "/api/v1/comparisons",
        json={
            "packageName": "com.example.game",
            "oldVersion": {"versionCode": "100"},
            "newVersion": {"versionCode": "101"},
        },
    ).json()["taskId"]

    cancelled = c.post(f"/api/v1/tasks/{task_id}/cancel")
    retried = c.post(f"/api/v1/tasks/{task_id}/retry")

    assert cancelled.status_code == 200
    assert cancelled.json() == {"taskId": task_id, "status": "cancelled"}
    assert c.get(f"/api/v1/tasks/{task_id}").json()["status"] == "cancelled"
    assert retried.status_code == 202
    assert retried.json()["retryOf"] == task_id
    assert c.get(f"/api/v1/tasks/{retried.json()['taskId']}").json()["status"] == "queued"


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


def test_admin_can_create_and_revoke_api_keys(tmp_path):
    c = client(tmp_path)
    settings = get_settings()
    settings.auth_enabled = True
    svc = AuthService(settings.auth_db_path, session_ttl_hours=settings.session_ttl_hours)
    svc.register_or_check_admin("ou_admin", "Admin", "admin@example.com")
    session = svc.create_session("ou_admin")
    cookie = {"Cookie": f"auc_session={session.id}"}

    assert c.get("/admin", follow_redirects=False).status_code == 302
    assert c.get("/admin", headers=cookie).status_code == 200
    created = c.post("/admin/api-keys", json={"name": "ci"}, headers=cookie)
    assert created.status_code == 201
    raw_key = created.json()["key"]
    assert raw_key.startswith("auc_")
    assert c.get("/api/v1/tasks/missing", headers={"X-API-Key": raw_key}).status_code == 404

    key_id = created.json()["id"]
    assert c.post(f"/admin/api-keys/{key_id}/revoke", headers=cookie).status_code == 200
    assert c.get("/api/v1/tasks/missing", headers={"X-API-Key": raw_key}).status_code == 401


def test_feishu_oauth_callback_creates_admin_session(tmp_path, monkeypatch):
    c = client(tmp_path)
    settings = get_settings()
    settings.auth_enabled = True
    svc = AuthService(settings.auth_db_path, session_ttl_hours=settings.session_ttl_hours)
    state = svc.create_oauth_state("/admin")

    async def fake_exchange_code(settings, code, redirect_uri):
        return "token"

    async def fake_fetch_user_info(settings, access_token):
        class User:
            open_id = "ou_admin"
            name = "Admin"
            email = "admin@example.com"

        return User()

    monkeypatch.setattr("app.auth.feishu.exchange_code", fake_exchange_code)
    monkeypatch.setattr("app.auth.feishu.fetch_user_info", fake_fetch_user_info)

    response = c.get(f"/auth/callback?code=ok&state={state}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/admin"
    assert "auc_session=" in response.headers["set-cookie"]
    assert svc.get_admin().open_id == "ou_admin"


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
    monkeypatch.setattr("app.worker.executor.build_report_storage", lambda _settings: FakeReportStorage())
    TaskExecutor(settings, store, FakeApsClient(unity=True)).run(task_id)

    task = c.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "succeeded"
    assert task["progress"]["versionsDumped"] == 2
    assert task["progress"]["comparisonsCompleted"] == 1


def test_worker_stops_after_running_task_is_cancelled(tmp_path, monkeypatch):
    c = client(tmp_path)
    task_id = c.post(
        "/api/v1/comparisons",
        json={
            "packageName": "com.example.game",
            "oldVersion": {"versionCode": "100"},
            "newVersion": {"versionCode": "101"},
        },
    ).json()["taskId"]
    settings = get_settings()
    store = TaskStore(settings.task_db_path)
    assert store.claim_tasks(1) == [task_id]
    executor = TaskExecutor(settings, store, FakeApsClient(unity=True))

    async def cancel_after_versions(task):
        store.cancel_task(task["taskId"])

    async def fail_if_pairs_run(*args):
        raise AssertionError("cancelled task should not compare pairs")

    monkeypatch.setattr(executor, "_process_versions", cancel_after_versions)
    monkeypatch.setattr(executor, "_finish_pairs", fail_if_pairs_run)

    executor.run(task_id)

    assert c.get(f"/api/v1/tasks/{task_id}").json()["status"] == "cancelled"


def test_task_query_adds_report_signed_urls(tmp_path, monkeypatch):
    c = client(tmp_path)
    settings = get_settings()
    store = TaskStore(settings.task_db_path)
    task_id = store.create_pair_compare(
        PairCompareRequest(
            packageName="com.example.game",
            oldVersion={"versionCode": "100"},
            newVersion={"versionCode": "101"},
        )
    )
    pair_id = store.get_task(task_id)["comparisons"][0]["pairId"]
    store.add_artifact(task_id, pair_id, "report.html", "unity-compare-reports/com.example.game/report.html", "text/html")
    monkeypatch.setattr("app.api.routes.build_report_storage", lambda _settings: FakeReportStorage())

    artifact = c.get(f"/api/v1/tasks/{task_id}").json()["comparisons"][0]["artifacts"][0]

    assert artifact["objectKey"] == "unity-compare-reports/com.example.game/report.html"
    assert artifact["url"] == "https://signed.local/report.html?ttl=3600"


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


def test_worker_uses_compare_concurrency(tmp_path, monkeypatch):
    import asyncio

    c = client(tmp_path)
    task_id = c.post(
        "/api/v1/batch-comparisons",
        json={
            "packageName": "com.example.game",
            "versions": [
                {"versionCode": "100", "versionName": "1.0.0"},
                {"versionCode": "101", "versionName": "1.0.1"},
                {"versionCode": "102", "versionName": "1.0.2"},
            ],
        },
    ).json()["taskId"]
    settings = get_settings()
    settings.compare_concurrency = 2
    store = TaskStore(settings.task_db_path)
    task = store.get_task(task_id)
    executor = TaskExecutor(settings, store, FakeApsClient(unity=True))
    lock = threading.Lock()
    active = 0
    max_active = 0
    seen = []

    def fake_finish_pair(task_id, pair, versions):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            seen.append(pair["pairId"])
            active -= 1

    monkeypatch.setattr(executor, "_finish_pair", fake_finish_pair)
    asyncio.run(executor._finish_pairs(task_id, task["comparisons"], {}))

    assert max_active == 2
    assert sorted(seen) == sorted(pair["pairId"] for pair in task["comparisons"])


def test_worker_uses_download_and_dump_concurrency(tmp_path, monkeypatch):
    import asyncio

    c = client(tmp_path)
    task_id = c.post(
        "/api/v1/batch-comparisons",
        json={
            "packageName": "com.example.game",
            "versions": [
                {"versionCode": "100"},
                {"versionCode": "101"},
                {"versionCode": "102"},
            ],
        },
    ).json()["taskId"]
    settings = get_settings()
    settings.download_concurrency = 2
    settings.dump_concurrency = 1
    store = TaskStore(settings.task_db_path)
    task = store.get_task(task_id)
    executor = TaskExecutor(settings, store, FakeApsClient(unity=True))
    lock = threading.Lock()
    active_downloads = 0
    max_downloads = 0
    active_dumps = 0
    max_dumps = 0

    async def fake_download_version(task, version):
        nonlocal active_downloads, max_downloads
        with lock:
            active_downloads += 1
            max_downloads = max(max_downloads, active_downloads)
        await asyncio.sleep(0.02)
        with lock:
            active_downloads -= 1
        return settings.work_dir / task["taskId"] / "packages" / f"{version['id']}.apk"

    def fake_check_and_dump(task, version, target):
        nonlocal active_dumps, max_dumps
        with lock:
            active_dumps += 1
            max_dumps = max(max_dumps, active_dumps)
        time.sleep(0.02)
        with lock:
            active_dumps -= 1

    monkeypatch.setattr(executor, "_download_version", fake_download_version)
    monkeypatch.setattr(executor, "_check_and_dump", fake_check_and_dump)

    asyncio.run(executor._process_versions(task))

    assert max_downloads == 2
    assert max_dumps == 1


def test_startup_cleanup_removes_non_running_work_dirs(tmp_path):
    c = client(tmp_path)
    task_id = c.post(
        "/api/v1/comparisons",
        json={
            "packageName": "com.example.game",
            "oldVersion": {"versionCode": "100"},
            "newVersion": {"versionCode": "101"},
        },
    ).json()["taskId"]
    settings = get_settings()
    store = TaskStore(settings.task_db_path)
    assert store.claim_tasks(1) == [task_id]
    running_dir = settings.work_dir / task_id
    orphan_dir = settings.work_dir / "orphan-task"
    running_dir.mkdir(parents=True)
    orphan_dir.mkdir(parents=True)

    assert remove_orphan_work_dirs(settings.work_dir, store.running_task_ids()) == 1

    assert running_dir.exists()
    assert not orphan_dir.exists()


def test_ttl_cleanup_keeps_running_work_dirs(tmp_path):
    work_dir = tmp_path / "work"
    running_dir = work_dir / "running"
    expired_dir = work_dir / "expired"
    running_dir.mkdir(parents=True)
    expired_dir.mkdir()
    old = time.time() - 7200
    import os

    os.utime(running_dir, (old, old))
    os.utime(expired_dir, (old, old))

    assert remove_expired_work_dirs(work_dir, 1, protected_names={"running"}) == 1
    assert running_dir.exists()
    assert not expired_dir.exists()


def test_worker_loop_runs_tasks_concurrently(tmp_path, monkeypatch):
    c = client(tmp_path)
    task_ids = [
        c.post(
            "/api/v1/unity-checks",
            json={"packageName": "com.example.game", "versionCode": str(index)},
        ).json()["taskId"]
        for index in range(2)
    ]
    settings = get_settings()
    settings.task_concurrency = 2
    settings.worker_poll_seconds = 0.01
    real_sleep = time.sleep
    lock = threading.Lock()
    active = 0
    max_active = 0
    done = 0

    def fake_run_task(settings, store, task_id):
        nonlocal active, max_active, done
        with lock:
            active += 1
            max_active = max(max_active, active)
        real_sleep(0.05)
        store.mark_task(task_id, TaskStatus.SUCCEEDED)
        with lock:
            active -= 1
            done += 1

    def stop_after_done(seconds):
        if done >= len(task_ids):
            raise RuntimeError("stop loop")
        real_sleep(seconds)

    monkeypatch.setattr("app.worker.loop.run_task", fake_run_task)
    monkeypatch.setattr("app.worker.loop.time.sleep", stop_after_done)

    with pytest.raises(RuntimeError, match="stop loop"):
        import app.worker.loop

        app.worker.loop.run_forever()

    assert max_active == 2


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


def test_pair_compare_smoke_with_fake_aps_server(tmp_path, monkeypatch):
    c = client(tmp_path)
    settings = get_settings()
    settings.aps_api_key = "secret"
    settings.aps_job_poll_seconds = 0
    analyzer = tmp_path / "DllAnalyzer"
    analyzer.write_text("#!/bin/sh\n", encoding="utf-8")
    settings.dll_analyzer_path = analyzer
    server = FakeApsServer()
    settings.aps_base_url = server.url
    monkeypatch.setattr("app.worker.executor.dump_package", fake_dump_package)
    monkeypatch.setattr("app.worker.executor.build_report_storage", lambda _settings: FakeReportStorage())
    try:
        task_id = c.post(
            "/api/v1/comparisons",
            json={
                "packageName": "com.example.game",
                "oldVersion": {"versionCode": "100", "versionName": "1.0.0"},
                "newVersion": {"versionCode": "101", "versionName": "1.0.1"},
            },
        ).json()["taskId"]
        store = TaskStore(settings.task_db_path)
        assert store.claim_tasks(1) == [task_id]

        TaskExecutor(settings, store).run(task_id)

        task = c.get(f"/api/v1/tasks/{task_id}").json()
        assert task["status"] == "succeeded"
        assert task["progress"]["versionsDownloaded"] == 2
        assert task["progress"]["versionsDumped"] == 2
        assert task["progress"]["comparisonsCompleted"] == 1
        assert task["comparisons"][0]["status"] == "succeeded"
        assert sorted(item["name"] for item in task["comparisons"][0]["artifacts"]) == ["report.html", "report.json"]
        assert server.download_requests == 2
        assert sorted(server.download_versions) == ["100", "101"]
        assert server.status_requests >= 2
        assert server.file_requests >= 2
    finally:
        server.close()


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


class FakeApsServer:
    def __init__(self):
        self.download_requests = 0
        self.download_versions = []
        self.status_requests = 0
        self.file_requests = 0
        self._lock = threading.Lock()

        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer secret":
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.path.startswith("/api/v1/android/apps/com.example.game/download"):
                    with owner._lock:
                        owner.download_requests += 1
                        owner.download_versions.append(parse_qs(urlparse(self.path).query)["versionCode"][0])
                    body = b'{"jobId":"1","status":"queued","statusUrl":"/api/v1/android/downloads/1","fileUrl":"/api/v1/android/downloads/1/file"}'
                    self.send_response(202)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/api/v1/android/downloads/1":
                    with owner._lock:
                        owner.status_requests += 1
                    body = b'{"jobId":"1","status":"succeeded","fileUrl":"/api/v1/android/downloads/1/file"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/api/v1/android/downloads/1/file":
                    with owner._lock:
                        owner.file_requests += 1
                    body = unity_zip_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                return

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)


def fake_dump_package(package_path, output_dir, **kwargs):
    dummy = output_dir / "DummyDll"
    dummy.mkdir(parents=True, exist_ok=True)
    return dummy


class FakeReportStorage:
    def __init__(self):
        self.uploads = []

    def upload_file(self, local_path, key, content_type):
        self.uploads.append((local_path, key, content_type))

    def signed_url(self, key, expires_in, filename):
        return f"https://signed.local/{filename}?ttl={expires_in}"


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


def env_example_keys():
    return {
        line.split("=", 1)[0]
        for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def settings_env_keys():
    tree = ast.parse((PROJECT_ROOT / "app" / "config.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                item.target.id.upper()
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
    return set()
