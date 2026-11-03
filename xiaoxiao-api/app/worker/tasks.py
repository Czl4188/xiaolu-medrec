"""Worker 任务实现。

任务必须幂等：同一条 job 可能因为 Worker 崩溃被重新投递。
这里用「先看 run 是否已完成」来兜底。
"""

import hashlib
import logging
import time

from sqlalchemy.orm import Session

from app.core.logging import run_id_ctx
from app.db import repo

logger = logging.getLogger("app.worker")


def handle_record_reindex(payload: dict, db: Session) -> None:
    """把一条记录切块并生成占位向量。

    真实 RAG 到这里应该：分块 → embedding 模型 → 写入 pgvector。
    现在只做确定性的可验证工作（分块统计 + 摘要哈希），保证队列、
    重试、幂等这套骨架是真跑通的。
    """
    record_id = payload["record_id"]
    user_id = payload["user_id"]
    run_id = payload.get("run_id")
    if run_id:
        run_id_ctx.set(run_id)

    run = repo.get_run(db, user_id, run_id) if run_id else None
    if run and run.status == "ok":
        logger.info("run 已完成，跳过重复执行 run_id=%s", run_id)
        return

    record = repo.get_record(db, user_id, record_id)
    if not record:
        raise ValueError(f"记录不存在或不属于该用户: {record_id}")

    started = time.monotonic()
    if run_id:
        repo.add_step(db, run_id, 1, "load_record", detail={"chars": len(record.transcript or "")})

    text = record.transcript or ""
    chunks = [text[i:i + 400] for i in range(0, len(text), 400)] or [""]
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]

    if run_id:
        repo.add_tool_call(db, run_id, "embedder.stub",
                           {"chunks": len(chunks)},
                           {"digest": digest},
                           duration_ms=int((time.monotonic() - started) * 1000))
        repo.add_step(db, run_id, 2, "chunk_and_embed",
                      detail={"chunks": len(chunks), "digest": digest})
        repo.finish_run(db, run_id, "ok", {"chunks": len(chunks), "digest": digest})

    logger.info("reindex 完成 record=%s chunks=%s", record_id, len(chunks))


HANDLERS = {
    "record.reindex": handle_record_reindex,
}


def dispatch(kind: str, payload: dict, db: Session) -> None:
    handler = HANDLERS.get(kind)
    if handler is None:
        raise ValueError(f"未知任务类型: {kind}")
    handler(payload, db)
