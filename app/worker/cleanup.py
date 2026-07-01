import shutil
import time
from pathlib import Path


def remove_expired_work_dirs(work_dir: Path, ttl_hours: float) -> int:
    if not work_dir.exists():
        return 0
    cutoff = time.time() - ttl_hours * 3600
    removed = 0
    for child in work_dir.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def remove_orphan_work_dirs(work_dir: Path, running_task_ids: set[str]) -> int:
    if not work_dir.exists():
        return 0
    removed = 0
    for child in work_dir.iterdir():
        if child.is_dir() and child.name not in running_task_ids:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
