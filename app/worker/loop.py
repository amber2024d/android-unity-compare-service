from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import logging
import time

from app.config import Settings, get_settings
from app.db import TaskStore
from app.models import TaskStatus
from app.worker.cleanup import remove_expired_work_dirs, remove_orphan_work_dirs
from app.worker.executor import TaskExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_task(settings: Settings, store: TaskStore, task_id: str) -> None:
    try:
        TaskExecutor(settings, store).run(task_id)
    except Exception as exc:
        task = store.get_task(task_id)
        if task and task["status"] == TaskStatus.RUNNING:
            store.mark_task(task_id, TaskStatus.FAILED, str(exc))
        raise


def run_forever() -> None:
    settings = get_settings()
    settings.ensure_directories()
    store = TaskStore(settings.task_db_path)
    logger.info("compare worker started")
    # 单 worker 架构下，启动时仍处于 running 的任务必然是上次进程中断的孤儿，标记失败后可通过 retry 重新提交。
    stale = store.fail_stale_running_tasks("worker 重启导致任务中断，可调用 retry 重新提交")
    if stale:
        logger.info("marked %s stale running tasks as failed: %s", len(stale), ", ".join(stale))
    removed = remove_orphan_work_dirs(settings.work_dir, store.running_task_ids())
    if removed:
        logger.info("removed %s orphan work dirs", removed)
    running: dict[Future, str] = {}

    with ThreadPoolExecutor(max_workers=settings.task_concurrency) as pool:
        while True:
            remove_expired_work_dirs(settings.work_dir, settings.work_dir_ttl_hours, protected_names=set(running.values()))

            capacity = settings.task_concurrency - len(running)
            for task_id in store.claim_tasks(capacity):
                logger.info("running task %s", task_id)
                future = pool.submit(run_task, settings, store, task_id)
                running[future] = task_id

            if not running:
                time.sleep(settings.worker_poll_seconds)
                continue

            done, _ = wait(running, timeout=settings.worker_poll_seconds, return_when=FIRST_COMPLETED)
            for future in done:
                task_id = running.pop(future)
                try:
                    future.result()
                except Exception:
                    logger.exception("task %s failed", task_id)


if __name__ == "__main__":
    run_forever()
