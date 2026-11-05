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
from app.agent import normalize as normalize_agent
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


EMERGENCY_ANSWER = (
    "这属于可能需要紧急处理的情况，我不能通过病历问答帮你判断。\n\n"
    "请立即拨打 120（或当地急救电话），或尽快到最近的急诊。\n"
    "如果出现胸痛、呼吸困难、意识不清、大出血，不要自行服药、不要等待观察。"
)

OUT_OF_SCOPE_ANSWER = (
    "这条我不能答。\n\n"
    "能不能吃、要不要停药、严不严重，这类判断属于诊疗，不在记录工具的范围内。\n\n"
    "我能告诉你的是：医生当时怎么交代的、开了哪些药、什么时候复诊。"
)

SMALLTALK_ANSWER = "我在。想问档案里的哪件事？比如「上次开了什么药」「什么时候复诊」。"


def _recent_lines(records) -> str:
    rows = list(records)[:3]
    if not rows:
        return "你的档案里还没有记录。"

    def field(r, key):
        return r.get(key) if isinstance(r, dict) else getattr(r, key, None)

    return "最近三次：\n" + "\n".join(
        f"- {answer_agent.fmt_date(field(r, 'visit_at'))} {field(r, 'hospital') or '未记录医院'}"
        for r in rows
    )


def _route_only(cond: dict | None, records) -> dict | None:
    """闲聊 / 求建议 / 急症 / 说不清：不检索、不调 chat，固定话术。"""
    if not cond:
        return None
    intent = cond.get("intent")
    if intent == "emergency":
        return {"answer": EMERGENCY_ANSWER, "citations": [], "refused": True}
    if intent == "out_of_scope":
        return {"answer": OUT_OF_SCOPE_ANSWER, "citations": [], "refused": True}
    if intent == "smalltalk":
        return {"answer": SMALLTALK_ANSWER, "citations": [], "refused": False}
    if intent == "unclear":
        tip = cond.get("need_clarify") or "你是想问哪一次？"
        return {"answer": f"{tip}\n\n{_recent_lines(records)}", "citations": [], "refused": True}
    return None


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
                   run_id: str, db: Session, cond: dict | None = None) -> str:
    """理解引擎 TASK=answer 作答；拒答/无来源不调模型，失败立刻退回规则答案。"""
    if result.get("refused") or not result.get("citations"):
        return result["answer"]

    facts = _facts_block(result, records)
    started = time.monotonic()
    text = await engine.answer(question, facts, user_id, run_id,
                               intent=(cond or {}).get("intent", ""))
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


async def _prepare(db: Session, user: User, body: ChatIn):
    records = repo.list_records(db, user.id)

    # 先归一：模型只译不答；失败/超时/低置信度都返回 None，退回原句检索
    vocab = normalize_agent.build_vocab(records)
    cond = await normalize_agent.normalize(body.question, user.id, vocab)

    routed = _route_only(cond, records)
    if routed is not None:
        result = routed
    else:
        result = answer_agent.answer(body.question, _records_payload(records), cond)

    run, _ = repo.create_run(
        db, user.id, "chat", {"question": body.question},
        idempotency_key=body.idempotency_key or None,
    )
    repo.add_step(db, run.id, 1, "retrieve_records", detail={
        "count": len(records),
        "intent": (cond or {}).get("intent", ""),
        "confidence": (cond or {}).get("confidence", 0),
        "normalize_fallback": cond is None,
        "routed": routed is not None,
    })
    return run, result, records, cond


@router.post("/sync", response_model=AnswerOut, dependencies=[Depends(rate_limit("chat", 30))])
async def chat_sync(
    body: ChatIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AnswerOut:
    run, result, records, cond = await _prepare(db, user, body)
    text = await _compose(body.question, result, records, user.id, run.id, db, cond)
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
    run, result, records, cond = await _prepare(db, user, body)

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

        text = await _compose(body.question, result, records, user.id, run.id, db, cond)

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
