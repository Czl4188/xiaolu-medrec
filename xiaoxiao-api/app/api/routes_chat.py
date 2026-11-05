"""对话作答：先检索规则给结论，再让模型把结论说人话。

顺序很关键 —— 先有「结论 + 来源」，模型只做措辞。这样模型即使胡说，
也改不了「答什么」和「依据哪一次就诊」。
"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent import answer as answer_agent
from app.agent import engine
from app.api.deps import current_user, rate_limit
from app.db import repo
from app.db.models import User
from app.db.session import get_db
from app.schemas import AnswerOut, ChatIn

logger = logging.getLogger("app.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


def _records_payload(records) -> list[dict]:
    return [
        {
            "id": r.id,
            "client_id": r.client_id,
            "visit_at": r.visit_at,
            "hospital": r.hospital,
            "summary": r.summary,
            "meds": r.meds or [],
            "follow_up": r.follow_up,
            "allergy": r.allergy,
            "notes": r.notes or [],
            "conditions": r.conditions or [],
            "discontinued": r.discontinued or [],
            "allergy_denied": bool(r.allergy_denied),
            "transcript": r.transcript,
        }
        for r in records
    ]


def _facts_block(result: dict, records) -> str:
    """把规则命中的记录折成带 [R1] 标号的事实块，交给 TASK=answer。

    只给命中记录，不给全部档案 —— 模型最常见的错就是拿别的记录凑答案。
    """
    by_id = {r.id: r for r in records}
    chosen = [by_id[c["record_id"]] for c in result.get("citations") or []
              if c.get("record_id") in by_id]
    if not chosen:
        chosen = list(records)[:2]

    lines: list[str] = []
    for i, r in enumerate(chosen[:4], 1):
        day = answer_agent.fmt_date(r.visit_at)
        lines.append(f"[R{i}] {day} {r.hospital or '未记录医院'}")
        lines.append(f"诊断：{r.summary or '未写明'}")
        meds = r.meds or []
        if meds:
            bits = []
            for m in meds:
                parts = [m.get("name", "")]
                for key in ("dose", "freq", "timing"):
                    if m.get(key):
                        parts.append(m[key])
                if m.get("days"):
                    parts.append(f"{m['days']} 天")
                bits.append("·".join(p for p in parts if p))
            lines.append("用药：" + "；".join(bits))
        if r.follow_up:
            lines.append(f"复诊：{r.follow_up}")
        if r.allergy:
            lines.append(f"过敏：{r.allergy}")
        elif r.allergy_denied:
            lines.append("过敏：记录写「未发现过敏」")
        notes = r.notes or []
        if notes:
            lines.append("注意事项：" + "；".join(notes))
        lines.append("")
    return "\n".join(lines).strip()


async def _compose(question: str, result: dict, records, user_id: str,
                   run_id: str, db: Session) -> str:
    """理解引擎 TASK=answer 作答；拒答/无来源不调模型，失败立刻退回规则答案。"""
    if result.get("refused") or not result.get("citations"):
        return result["answer"]

    facts = _facts_block(result, records)
    started = time.monotonic()
    text = await engine.answer(question, facts, user_id, run_id)
    if text:
        # 模型答案与规则答案的引用是否一致：不一致只记日志，不阻断
        if "[R" not in text:
            logger.warning("engine answer 缺少 [R] 引用，记录 divergence：%s", question[:40])
        repo.add_tool_call(db, run_id, "engine.answer",
                           {"chars": len(facts)}, {"chars": len(text)},
                           duration_ms=int((time.monotonic() - started) * 1000))
        return text

    repo.add_tool_call(db, run_id, "engine.answer", {"chars": len(facts)},
                       {"fallback": True}, error="engine 未返回可用的答案",
                       duration_ms=int((time.monotonic() - started) * 1000))
    return result["answer"]


def _prepare(db: Session, user: User, body: ChatIn):
    records = repo.list_records(db, user.id)
    result = answer_agent.answer(body.question, _records_payload(records))
    run, _ = repo.create_run(
        db, user.id, "chat", {"question": body.question},
        idempotency_key=body.idempotency_key or None,
    )
    repo.add_step(db, run.id, 1, "retrieve_records", detail={"count": len(records)})
    return run, result, records


@router.post("/sync", response_model=AnswerOut, dependencies=[Depends(rate_limit("chat", 30))])
async def chat_sync(
    body: ChatIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AnswerOut:
    run, result, records = _prepare(db, user, body)
    text = await _compose(body.question, result, records, user.id, run.id, db)
    repo.add_step(db, run.id, 2, "compose_answer", detail={"refused": result["refused"]})
    repo.finish_run(db, run.id, "ok", {"answer": text, "refused": result["refused"]})
    return AnswerOut(
        answer=text,
        citations=result["citations"],
        refused=result["refused"],
    )


@router.post("", dependencies=[Depends(rate_limit("chat", 30))])
async def chat_stream(
    body: ChatIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    run, result, records = _prepare(db, user, body)

    async def events():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

        yield sse("meta", {
            "run_id": run.id,
            "citations": [
                {
                    "record_id": c["record_id"],
                    "client_id": c.get("client_id", ""),
                    "visit_at": c["visit_at"].isoformat(),
                    "hospital": c.get("hospital", ""),
                }
                for c in result["citations"]
            ],
            "refused": result["refused"],
        })

        text = await _compose(body.question, result, records, user.id, run.id, db)

        # 注意：这是「按句切片推送」，不是 token 级流式。
        # 换真流式只需把这里换成读取模型 SSE；传输格式不用变。
        buffer = ""
        for ch in text:
            buffer += ch
            if ch in "。！？\n；" or len(buffer) >= 48:
                yield sse("delta", {"text": buffer})
                buffer = ""
                await asyncio.sleep(0)
        if buffer:
            yield sse("delta", {"text": buffer})

        repo.add_step(db, run.id, 2, "compose_answer",
                      detail={"refused": result["refused"], "chars": len(text)})
        repo.finish_run(db, run.id, "ok", {"refused": result["refused"], "chars": len(text)})
        yield sse("done", {
            "run_id": run.id,
            "refused": result["refused"],
            "answer": text,
            "disclaimer": answer_agent.DISCLAIMER,
        })

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
