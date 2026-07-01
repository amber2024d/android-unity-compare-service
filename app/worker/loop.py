import logging
import time

from app.config import get_settings
from app.db import TaskStore
from app.worker.cleanup import remove_expired_work_dirs, remove_orphan_work_dirs
from app.worker.executor import TaskExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_forever() -> None:
    settings = get_settings()
    settings.ensure_directories()
    store = TaskStore(settings.task_db_path)
    executor = TaskExecutor(settings, store)
    logger.info("compare worker started")
    removed = remove_orphan_work_dirs(settings.work_dir, store.running_task_ids())
    if removed:
        logger.info("removed %s orphan work dirs", removed)

    while True:
        remove_expired_work_dirs(settings.work_dir, settings.work_dir_ttl_hours)
        task_ids = store.claim_tasks(settings.task_concurrency)
        if not task_ids:
            time.sleep(settings.worker_poll_seconds)
            continue
        for task_id in task_ids:
            logger.info("running task %s", task_id)
            executor.run(task_id)


if __name__ == "__main__":
    run_forever()
