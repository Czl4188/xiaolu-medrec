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
from app.agent import llm
from app.api.deps import current_user, rate_limit
from app.db import repo
from app.db.models import User
from app.db.session import get_db
from app.schemas import AnswerOut, ChatIn

logger = logging.getLogger("app.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

SYSTEM_PROMPT = (
    "你是「小鹿」，一个病历记录整理助手。规则：\n"
    "1. 只能使用给你的【事实】作答，不得补充事实之外的任何医学信息；\n"
    "2. 不得给出诊断、用药建议、剂量调整或停药意见；用户问这类问题时，说明超出范围并建议问医生；\n"
    "3. 不得改变【事实】里的日期、药名、剂量、疗程；\n"
    "4. 保持简体中文，语气克制，不要寒暄，不要用「作为AI」这类话；\n"
    "5. 只输出正文，不要输出 markdown 标题；\n"
    "6. 如果【问题】问的正是【事实】里记录过的内容（同一次就诊、同一个药、同一个复诊安排），"
    "就直接复述记录里的原文信息（日期、医院、诊断、药名、用法、复诊、注意事项），不要回答「没有记录」；\n"
    "7. 复述时保留原记录里的数字与单位，不要换算、不要补全记录里没有的剂量。"
)


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


async def _polish(question: str, facts: str, user_id: str, run_id: str, db: Session) -> str:
    """让模型把事实说顺；失败就用原答案，绝不因为模型挂了而答不出来。"""
    if not llm.is_configured():
        return facts
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"【事实】\n{facts}\n\n【问题】\n{question}"},
    ]
    started = time.monotonic()
    try:
        polished = await llm.complete(messages, user_id=user_id, run_id=run_id)
        repo.add_tool_call(db, run_id, "llm.complete",
                           {"model": "primary", "chars": len(facts)},
                           {"chars": len(polished)},
                           duration_ms=int((time.monotonic() - started) * 1000))
        return polished or facts
    except llm.LLMError as exc:
        logger.warning("模型不可用，回退规则答案: %s", exc)
        repo.add_tool_call(db, run_id, "llm.complete", {"model": "primary"},
                           {}, error=str(exc)[:300],
                           duration_ms=int((time.monotonic() - started) * 1000))
        return facts


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
    run, result, _ = _prepare(db, user, body)
    text = await _polish(body.question, result["answer"], user.id, run.id, db)
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
    run, result, _ = _prepare(db, user, body)

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

        text = await _polish(body.question, result["answer"], user.id, run.id, db)

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
