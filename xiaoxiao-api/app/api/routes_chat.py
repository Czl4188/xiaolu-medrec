"""对话作答：先检索规则给结论，再让模型把结论说人话。

顺序很关键 —— 先有「结论 + 来源」，模型只做措辞。这样模型即使胡说，
也改不了「答什么」和「依据哪一次就诊」。
"""

import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent import answer as answer_agent
from app.agent import chat_free as chat_free_agent
from app.agent import engine
from app.agent import normalize as normalize_agent
from app.agent import smalltalk_route
from app.api.deps import current_user, rate_limit
from app.core.config import settings
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

# 情绪危机：比普通急症更靠前，话术单独一套（不接档案入口、不问细节）
CRISIS_ANSWER = (
    "这句话我认真对待。\n\n"
    "请现在拨打 12356（心理援助热线）或 120，也可以立刻找一个身边的人陪着你。"
)

# 只有症状、没有具体对象：不判断、不猜原因，给就医边界 + 档案入口
SYMPTOM_ANSWER = (
    "这种不舒服我不做判断，也不替你猜原因。\n\n"
    "如果一直不缓解、越来越重，或者出现胸痛、呼吸困难、意识不清，尽快去医院或拨 120。\n\n"
    "要不要我先翻出医生上次交代的注意事项？"
)


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


def _route_only(cond: dict | None, records, question: str = "") -> dict | None:
    """不检索的分支：危机 / 急症 / 越界 / 闲聊 / 说不清。

    顺序固定：危机 → 急症 → 越界 → 闲聊 → 澄清。
    急症与危机先于一切，任何情况下都不会被闲聊层接走。
    """
    if question and smalltalk_route.is_crisis(question):
        return {"answer": CRISIS_ANSWER, "citations": [], "refused": False, "mode": "crisis"}

    vocab = smalltalk_route.vocab_of(records)

    if not cond:
        # 只有症状、没有可检索对象：规则检索只会回「未查询到」，这里直说边界
        if question and smalltalk_route.symptom_only(question, vocab):
            return {"answer": SYMPTOM_ANSWER, "citations": [], "refused": True, "mode": "symptom"}
        # 归一失败/超时：用正则兜一层，避免「我爱你」掉进「未查询到」
        if (question and settings.llm_smalltalk
                and smalltalk_route.is_smalltalk(question, vocab)):
            return {"answer": SMALLTALK_ANSWER, "citations": [], "refused": False,
                    "mode": "smalltalk"}
        return None

    intent = cond.get("intent")
    if intent == "emergency":
        return {"answer": EMERGENCY_ANSWER, "citations": [], "refused": True, "mode": "emergency"}
    if intent == "out_of_scope":
        return {"answer": OUT_OF_SCOPE_ANSWER, "citations": [], "refused": True,
                "mode": "out_of_scope"}
    if question and smalltalk_route.symptom_only(question, vocab):
        return {"answer": SYMPTOM_ANSWER, "citations": [], "refused": True, "mode": "symptom"}
    if intent == "smalltalk":
        # 模型说是闲聊，但句子里有症状词/档案实体：不接，交给规则分支
        if (question and not smalltalk_route.is_smalltalk(
                question, vocab)):
            return None
        return {"answer": SMALLTALK_ANSWER, "citations": [], "refused": False, "mode": "smalltalk"}
    if intent == "unclear":
        tip = cond.get("need_clarify") or "你是想问哪一次？"
        return {"answer": f"{tip}\n\n{_recent_lines(records)}", "citations": [],
                "refused": True, "mode": "clarify"}
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


async def _smalltalk(question: str, result: dict, records, user_id: str,
                     run_id: str, db: Session, history=None) -> str:
    """闲聊层：模型说一句人话；不可用/越界/超时一律退回固定话术。"""
    fallback = result["answer"]
    if not settings.llm_smalltalk:
        return fallback

    started = time.monotonic()
    turns = list(history or [])
    text = await chat_free_agent.chat_free(question, turns, user_id, records, run_id)
    ms = int((time.monotonic() - started) * 1000)
    if text:
        repo.add_tool_call(db, run_id, "chat_free", {"history": len(turns)},
                           {"chars": len(text)}, duration_ms=ms)
        return text

    repo.add_tool_call(db, run_id, "chat_free", {"history": len(turns)},
                       {"fallback": True}, error="对话层未返回可用结果", duration_ms=ms)
    return fallback


def _echo(text: str, question: str) -> bool:
    """模型把问题原样复述一遍：这不是答案。"""
    def flat(value: str) -> str:
        return re.sub(r"[\s，。？！、,.?!~]+", "", value or "")

    body = flat(text)
    return bool(body) and body == flat(question)


async def _compose(question: str, result: dict, records, user_id: str,
                   run_id: str, db: Session, cond: dict | None = None,
                   history=None) -> str:
    """理解引擎 TASK=answer 作答；拒答/无来源不调模型，失败立刻退回规则答案。"""
    if result.get("mode") == "smalltalk":
        return await _smalltalk(question, result, records, user_id, run_id, db, history)

    if result.get("refused") or not result.get("citations"):
        return result["answer"]

    facts = _facts_block(result, records)
    started = time.monotonic()
    text = await engine.answer(question, facts, user_id, run_id,
                               intent=(cond or {}).get("intent", ""))
    if text and _echo(text, question):
        logger.warning("engine answer 只复述了问题，退回规则答案：%s", question[:40])
        text = ""
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

    routed = _route_only(cond, records, body.question)
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
        "mode": (routed or {}).get("mode", ""),
        "turns": len(body.history or []),
    })
    return run, result, records, cond


@router.post("/sync", response_model=AnswerOut, dependencies=[Depends(rate_limit("chat", 30))])
async def chat_sync(
    body: ChatIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AnswerOut:
    run, result, records, cond = await _prepare(db, user, body)
    text = await _compose(body.question, result, records, user.id, run.id, db, cond,
                          [t.model_dump() for t in body.history])
    repo.add_step(db, run.id, 2, "compose_answer", detail={"refused": result["refused"]})
    repo.finish_run(db, run.id, "ok", {"answer": text, "refused": result["refused"]})
    return AnswerOut(
        answer=text,
        citations=result["citations"],
        refused=result["refused"],
        kind=result.get("mode", ""),
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
            "kind": result.get("mode", ""),
        })

        text = await _compose(body.question, result, records, user.id, run.id, db, cond,
                              [t.model_dump() for t in body.history])

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
            "kind": result.get("mode", ""),
            "answer": text,
            "disclaimer": answer_agent.DISCLAIMER,
        })

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
