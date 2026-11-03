"""Worker 进程入口。

与 API 分开部署：API 挂了不影响正在跑的任务，Worker 扩容也不影响接口延迟。
启动：python -m app.worker.run
"""

import logging
import os
import signal
import socket
import sys
import time

from app.core.config import settings
from app.core.logging import setup_logging
from app.db import repo
from app.db.models import Job
from app.db.session import SessionLocal
from app.worker.tasks import dispatch

logger = logging.getLogger("app.worker")

_running = True


def _stop(signum, _frame):
    global _running
    logger.info("收到信号 %s，处理完当前任务后退出", signum)
    _running = False


def main() -> int:
    setup_logging("INFO")
    worker_id = settings.worker_id or f"{socket.gethostname()}-{os.getpid()}"

    if settings.database_url.startswith("sqlite"):
        logger.warning("当前使用 SQLite：多 Worker 并发抢占不安全，生产请用 PostgreSQL")
    else:
        logger.info("数据库: %s", settings.database_url.split("@")[-1])

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("worker 启动 id=%s", worker_id)
    idle = 0
    while _running:
        with SessionLocal() as db:
            job: Job | None = repo.claim_job(db, worker_id)
            if job is None:
                idle += 1
                if idle % 30 == 0:
                    logger.info("队列空闲，继续等待")
                time.sleep(settings.job_poll_interval)
                continue

            idle = 0
            try:
                dispatch(job.kind, job.payload or {}, db)
                repo.complete_job(db, job.id)
                logger.info("任务完成 job=%s kind=%s attempts=%s", job.id, job.kind, job.attempts)
            except Exception as exc:
                db.rollback()
                state = repo.fail_job(db, job, f"{type(exc).__name__}: {exc}")
                logger.exception("任务失败 job=%s kind=%s → %s", job.id, job.kind, state)

    logger.info("worker 已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
