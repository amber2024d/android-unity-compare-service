import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.models import (
    BatchCompareRequest,
    PairCompareRequest,
    PairStatus,
    TaskStatus,
    TaskType,
    UnityCheckRequest,
    VersionRef,
    VersionStatus,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class TaskStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY,
                  type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  package_name TEXT NOT NULL,
                  payload TEXT NOT NULL,
                  error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  started_at TEXT,
                  finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS versions (
                  id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                  position INTEGER NOT NULL,
                  version_code TEXT,
                  version_name TEXT,
                  status TEXT NOT NULL,
                  package_path TEXT,
                  dump_path TEXT,
                  error TEXT,
                  UNIQUE(task_id, position)
                );
                CREATE TABLE IF NOT EXISTS pairs (
                  id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                  old_version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
                  new_version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
                  position INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  error TEXT,
                  UNIQUE(task_id, position)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                  id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                  pair_id TEXT REFERENCES pairs(id) ON DELETE CASCADE,
                  name TEXT NOT NULL,
                  object_key TEXT NOT NULL,
                  content_type TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_versions_task ON versions(task_id, position);
                CREATE INDEX IF NOT EXISTS idx_pairs_task ON pairs(task_id, position);
                """
            )

    def create_unity_check(self, request: UnityCheckRequest) -> str:
        task_id = self._insert_task(TaskType.UNITY_CHECK, request.package_name, request.model_dump(mode="json", by_alias=True))
        self._insert_versions(task_id, [VersionRef(versionCode=request.version_code, versionName=request.version_name)])
        return task_id

    def create_pair_compare(self, request: PairCompareRequest) -> str:
        versions = [request.old_version, request.new_version]
        task_id = self._insert_task(TaskType.PAIR_COMPARE, request.package_name, request.model_dump(mode="json", by_alias=True))
        version_ids = self._insert_versions(task_id, versions)
        self._insert_pair(task_id, version_ids[0], version_ids[1], 0)
        return task_id

    def create_batch_compare(self, request: BatchCompareRequest) -> str:
        versions = sort_versions(request.versions)
        payload = request.model_dump(mode="json", by_alias=True)
        payload["versions"] = [version.model_dump(mode="json", by_alias=True) for version in versions]
        task_id = self._insert_task(TaskType.BATCH_COMPARE, request.package_name, payload)
        version_ids = self._insert_versions(task_id, versions)
        for index in range(len(version_ids) - 1):
            self._insert_pair(task_id, version_ids[index], version_ids[index + 1], index)
        return task_id

    def claim_tasks(self, limit: int) -> list[str]:
        with self.connect() as conn:
            now = utc_now()
            rows = conn.execute(
                """
                UPDATE tasks
                SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE id IN (
                  SELECT id FROM tasks WHERE status = ? ORDER BY created_at LIMIT ?
                )
                RETURNING id
                """,
                (TaskStatus.RUNNING, now, now, TaskStatus.QUEUED, limit),
            ).fetchall()
            return [row["id"] for row in rows]

    def cancel_task(self, task_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            if row["status"] in {TaskStatus.SUCCEEDED, TaskStatus.PARTIAL_FAILED, TaskStatus.FAILED}:
                return row["status"]
            if row["status"] != TaskStatus.CANCELLED:
                now = utc_now()
                conn.execute(
                    "UPDATE tasks SET status = ?, error = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                    (TaskStatus.CANCELLED, "cancelled by user", now, now, task_id),
                )
            return TaskStatus.CANCELLED

    def retry_task(self, task_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT type, payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        if row["type"] == TaskType.UNITY_CHECK:
            return self.create_unity_check(UnityCheckRequest.model_validate(payload))
        if row["type"] == TaskType.PAIR_COMPARE:
            return self.create_pair_compare(PairCompareRequest.model_validate(payload))
        return self.create_batch_compare(BatchCompareRequest.model_validate(payload))

    def running_task_ids(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id FROM tasks WHERE status = ?", (TaskStatus.RUNNING,)).fetchall()
            return {row["id"] for row in rows}

    def fail_stale_running_tasks(self, error: str) -> list[str]:
        with self.connect() as conn:
            now = utc_now()
            rows = conn.execute(
                "UPDATE tasks SET status = ?, error = ?, updated_at = ?, finished_at = ? WHERE status = ? RETURNING id",
                (TaskStatus.FAILED, error, now, now, TaskStatus.RUNNING),
            ).fetchall()
            return [row["id"] for row in rows]

    def mark_task(self, task_id: str, status: TaskStatus, error: str | None = None) -> None:
        finished = utc_now() if status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.PARTIAL_FAILED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        } else None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, error = ?, updated_at = ?, finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (status, error, utc_now(), finished, task_id),
            )

    def mark_version(self, version_id: str, status: VersionStatus, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE versions SET status = ?, error = ? WHERE id = ?", (status, error, version_id))
            conn.execute("UPDATE tasks SET updated_at = ? WHERE id = (SELECT task_id FROM versions WHERE id = ?)", (utc_now(), version_id))

    def set_version_paths(self, version_id: str, package_path: Path | None = None, dump_path: Path | None = None) -> None:
        with self.connect() as conn:
            if package_path is not None:
                conn.execute("UPDATE versions SET package_path = ? WHERE id = ?", (str(package_path), version_id))
            if dump_path is not None:
                conn.execute("UPDATE versions SET dump_path = ? WHERE id = ?", (str(dump_path), version_id))
            conn.execute("UPDATE tasks SET updated_at = ? WHERE id = (SELECT task_id FROM versions WHERE id = ?)", (utc_now(), version_id))

    def mark_pair(self, pair_id: str, status: PairStatus, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE pairs SET status = ?, error = ? WHERE id = ?", (status, error, pair_id))
            conn.execute("UPDATE tasks SET updated_at = ? WHERE id = (SELECT task_id FROM pairs WHERE id = ?)", (utc_now(), pair_id))

    def add_artifact(self, task_id: str, pair_id: str | None, name: str, object_key: str, content_type: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, task_id, pair_id, name, object_key, content_type) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), task_id, pair_id, name, object_key, content_type),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                return None
            versions = conn.execute("SELECT * FROM versions WHERE task_id = ? ORDER BY position", (task_id,)).fetchall()
            pairs = conn.execute(
                """
                SELECT p.*, ov.version_code AS old_code, ov.version_name AS old_name,
                       nv.version_code AS new_code, nv.version_name AS new_name
                FROM pairs p
                JOIN versions ov ON ov.id = p.old_version_id
                JOIN versions nv ON nv.id = p.new_version_id
                WHERE p.task_id = ?
                ORDER BY p.position
                """,
                (task_id,),
            ).fetchall()
            artifacts = conn.execute("SELECT * FROM artifacts WHERE task_id = ?", (task_id,)).fetchall()
        return task_response(dict(task), [dict(row) for row in versions], [dict(row) for row in pairs], [dict(row) for row in artifacts])

    def _insert_task(self, task_type: TaskType, package_name: str, payload: dict[str, Any]) -> str:
        task_id = str(uuid4())
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, type, status, package_name, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, task_type, TaskStatus.QUEUED, package_name, json.dumps(payload, ensure_ascii=False), now, now),
            )
        return task_id

    def _insert_versions(self, task_id: str, versions: list[VersionRef]) -> list[str]:
        ids = [str(uuid4()) for _ in versions]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO versions (id, task_id, position, version_code, version_name, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (version_id, task_id, index, version.version_code, version.version_name, VersionStatus.DOWNLOAD_PENDING)
                    for index, (version_id, version) in enumerate(zip(ids, versions, strict=True))
                ],
            )
        return ids

    def _insert_pair(self, task_id: str, old_version_id: str, new_version_id: str, position: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pairs (id, task_id, old_version_id, new_version_id, position, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), task_id, old_version_id, new_version_id, position, PairStatus.PENDING),
            )


def sort_versions(versions: list[VersionRef]) -> list[VersionRef]:
    try:
        return sorted(versions, key=lambda version: int(version.version_code or ""))
    except ValueError:
        return versions


def task_response(
    task: dict[str, Any],
    versions: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = json.loads(task["payload"])
    artifact_map: dict[str | None, list[dict[str, str]]] = {}
    for artifact in artifacts:
        artifact_map.setdefault(artifact["pair_id"], []).append({
            "name": artifact["name"],
            "objectKey": artifact["object_key"],
            "contentType": artifact["content_type"],
        })

    return {
        "taskId": task["id"],
        "type": task["type"],
        "status": task["status"],
        "packageName": task["package_name"],
        "appName": payload.get("appName"),
        "progress": {
            "versionsTotal": len(versions),
            "versionsDownloaded": sum(1 for row in versions if row["status"] in {
                VersionStatus.DOWNLOAD_SUCCEEDED,
                VersionStatus.DUMP_RUNNING,
                VersionStatus.UNITY_DUMPABLE,
                VersionStatus.UNITY_UNSUPPORTED,
                VersionStatus.CLEANED,
            }),
            "versionsDumped": sum(1 for row in versions if row["status"] in {VersionStatus.UNITY_DUMPABLE, VersionStatus.UNITY_UNSUPPORTED, VersionStatus.CLEANED}),
            "comparisonsTotal": len(pairs),
            "comparisonsCompleted": sum(1 for row in pairs if row["status"] == PairStatus.SUCCEEDED),
            "comparisonsFailed": sum(1 for row in pairs if row["status"] == PairStatus.FAILED),
        },
        "versions": [
            {
                "id": row["id"],
                "versionCode": row["version_code"],
                "versionName": row["version_name"],
                "status": row["status"],
                "packagePath": row["package_path"],
                "dumpPath": row["dump_path"],
                "error": row["error"],
            }
            for row in versions
        ],
        "comparisons": [
            {
                "pairId": row["id"],
                "oldVersionId": row["old_version_id"],
                "newVersionId": row["new_version_id"],
                "oldVersion": row["old_name"] or row["old_code"] or "unknown",
                "newVersion": row["new_name"] or row["new_code"] or "unknown",
                "status": row["status"],
                "artifacts": artifact_map.get(row["id"], []),
                "error": row["error"],
            }
            for row in pairs
        ],
        "artifacts": artifact_map.get(None, []),
        "error": task["error"],
        "createdAt": task["created_at"],
        "updatedAt": task["updated_at"],
        "startedAt": task["started_at"],
        "finishedAt": task["finished_at"],
    }
