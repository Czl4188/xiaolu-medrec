"""提问归一：把口语问题翻译成检索条件。

位置在「提问」与「规则检索」之间：模型只做语言理解（纠错、指代、归一、分诊），
不回答、不推断、不补事实。任何异常或低置信度都返回 None，调用方按原句走规则，
行为与接入前完全一致。

对应《小鹿-提问归一提示词 v1》：
  · 输出契约逐项校验，intent / time_scope / confidence 三项不过就降级；
  · keywords 只能来自用户原话，且尽量命中运行时词典；
  · 不把病历内容发给模型 —— 这里只传用户这一句话。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time

from app.agent import engine, llm
from app.core.config import settings

logger = logging.getLogger("app.normalize")

INTENTS = {
    "medication", "diagnosis", "visit", "follow_up", "allergy",
    "trend", "smalltalk", "out_of_scope", "emergency", "unclear",
}
KINDS = {"list", "value", "yes_no", "negation", "time"}
SCOPES = {"latest", "nth", "range", "all"}

# 与提示词第 4 节保持一致；只作为词典兜底，实际值会并入用户档案里的真实取值
BASE_TIMINGS = ["饭前", "饭后", "睡前", "空腹", "随餐", "早上", "晚上", "必要时"]
BASE_FREQS = ["每天一次", "每天两次", "每天三次", "每天四次", "隔天一次", "每周一次", "按需"]

MAX_KEYWORDS = 8
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def build_vocab(records) -> dict:
    """从本人档案里收集药名、医院、诊断，作为归一词典。"""
    meds: list[str] = []
    hospitals: list[str] = []
    conditions: list[str] = []

    def add(bucket: list[str], value: str) -> None:
        value = str(value or "").strip()
        if 2 <= len(value) <= 40 and value not in bucket:
            bucket.append(value)

    for record in records or []:
        add(hospitals, getattr(record, "hospital", ""))
        add(conditions, getattr(record, "summary", ""))
        for item in getattr(record, "conditions", None) or []:
            add(conditions, item)
        for med in getattr(record, "meds", None) or []:
            if isinstance(med, dict):
                add(meds, med.get("name"))

    return {
        "meds": meds[:120],
        "hospitals": hospitals[:40],
        "conditions": conditions[:80],
        "timings": BASE_TIMINGS,
        "freqs": BASE_FREQS,
    }


def _vocab_block(vocab: dict) -> str:
    return (
        "MED_NAMES = " + ("、".join(vocab.get("meds") or []) or "（暂无）") + "\n"
        "HOSPITALS = " + ("、".join(vocab.get("hospitals") or []) or "（暂无）") + "\n"
        "TIMINGS = " + "、".join(vocab.get("timings") or BASE_TIMINGS) + "\n"
        "FREQS = " + "、".join(vocab.get("freqs") or BASE_FREQS)
    )


def _known(vocab: dict) -> set[str]:
    known: set[str] = set()
    for key in ("meds", "hospitals", "conditions"):
        known.update(str(x).strip() for x in vocab.get(key) or [] if str(x).strip())
    return known


def _clean_keywords(raw, question: str, vocab: dict) -> list[str]:
    """关键词只接受「用户原话里出现过」的词，防止模型自己造词。"""
    known = _known(vocab)
    out: list[str] = []
    for item in raw or []:
        if not isinstance(item, str):
            continue
        word = item.strip()[:20]
        if not (2 <= len(word) <= 20):
            continue
        if word in out:
            continue
        # 命中词典，或者确实出现在原话里（同义词、药名简称都允许）
        if word in known or word in question:
            out.append(word)
        else:
            logger.info("丢弃词典与原话都没有的关键词：%s", word)
    return out[:MAX_KEYWORDS]


def _clean_scope(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip()
    if kind not in SCOPES:
        return None

    if kind == "nth":
        try:
            nth = int(raw.get("nth") or 0)
        except (TypeError, ValueError):
            return None
        if not (1 <= nth <= 99):
            return None
        return {"type": "nth", "nth": nth}

    if kind == "range":
        start = str(raw.get("from") or "").strip()
        end = str(raw.get("to") or "").strip()
        if not (_DATE.match(start) and _DATE.match(end)) or start > end:
            return None
        return {"type": "range", "from": start, "to": end}

    return {"type": kind}


def validate(data: dict, question: str, vocab: dict) -> dict | None:
    """按输出契约逐项校验；任何硬项不过就返回 None，调用方降级。"""
    if not isinstance(data, dict):
        return None

    intent = str(data.get("intent") or "").strip()
    if intent not in INTENTS:
        return None

    scope = _clean_scope(data.get("time_scope"))
    if scope is None:
        return None

    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    if confidence < settings.normalize_min_confidence:
        return None

    kind = data.get("question_kind")
    kind = kind if kind in KINDS else None

    hospital = str(data.get("hospital") or "").strip()
    if hospital and hospital not in (vocab.get("hospitals") or []):
        logger.info("丢弃词典外的医院：%s", hospital)
        hospital = ""
    hospital = hospital or None

    clarify = str(data.get("need_clarify") or "").strip()[:60] or None
    rewritten = str(data.get("rewritten") or "").strip()[:40]

    return {
        "intent": intent,
        "question_kind": kind,
        "time_scope": scope,
        "hospital": hospital,
        "keywords": _clean_keywords(data.get("keywords"), question, vocab),
        "keywords_raw": [str(x)[:20] for x in (data.get("keywords_raw") or []) if isinstance(x, str)][:MAX_KEYWORDS],
        "need_clarify": clarify,
        "rewritten": rewritten,
        "confidence": round(confidence, 3),
    }


def _log(question: str, cond: dict | None, started: float, reason: str = "") -> None:
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:8]
    payload = {
        "q_head": question[:20],
        "q_hash": digest,
        "intent": (cond or {}).get("intent", ""),
        "confidence": (cond or {}).get("confidence", 0),
        "fallback": cond is None,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "reason": reason,
    }
    logger.info("normalize %s", json.dumps(payload, ensure_ascii=False))


async def normalize(question: str, user_id: str, vocab: dict,
                    run_id: str | None = None) -> dict | None:
    """成功返回检索条件；失败、超时、低置信度都返回 None。"""
    if not engine.is_ready():
        return None
    text = str(question or "").strip()
    if not text:
        return None

    started = time.monotonic()
    messages = [
        {"role": "system", "content": engine.system_for("route", _vocab_block(vocab))},
        {"role": "user", "content": text[:100]},
    ]
    try:
        raw = await llm.complete(
            messages,
            user_id=user_id,
            run_id=run_id,
            temperature=0,
            response_format="json_object",
            max_tokens=220,
            timeout=settings.normalize_timeout,
            model=settings.normalize_model or settings.engine_model,
            retries=0,
        )
    except llm.LLMError as exc:
        _log(text, None, started, f"llm:{exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        _log(text, None, started, f"error:{exc}")
        return None

    data = engine.loads_json(raw)
    if not data:
        _log(text, None, started, "bad_json")
        return None

    cond = validate(data, text, vocab)
    if cond is None:
        _log(text, None, started, "invalid_contract")
        return None
    _log(text, cond, started)
    return cond
