"""动作识别：判断「这句话是不是想用小鹿的某个功能」。

只识别、不执行，也拿不到档案数据 —— 输入只有用户这句话 + 最近 3 轮。
任何失败（开关关、超时、JSON 坏、低置信、动作不在白名单）都返回 null，
调用方照常走问答，用户无感。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.agent import engine, llm
from app.core.config import settings

logger = logging.getLogger("app.action")

_PROMPT = Path(__file__).resolve().parent / "prompts" / "nl_action.txt"
PROMPT = _PROMPT.read_text(encoding="utf-8") if _PROMPT.is_file() else ""

# 与前端 ACTIONS 注册表一一对应；不在表里的一律丢弃
ACTIONS = {
    "scan_camera", "scan_album", "record_start", "record_rec", "record_read",
    "geo_hospital", "calendar_add", "record_edit", "record_delete",
    "avatar_change", "password_change", "phone_change",
    "export_data", "import_data", "print_pdf", "theme_change",
    "logout", "account_delete",
}
NULL = {"action": None, "confidence": 0.0, "say": ""}
MIN_CONFIDENCE = 0.6
MAX_SAY = 20


def _turns(history) -> list[dict]:
    """只留最近 3 轮，去掉角色与长度不合法的。"""
    out: list[dict] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in ("user", "assistant"):
            continue
        text = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()[:100]
        if text:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out[-6:]


def sanitize(data: dict) -> dict:
    """三道校验：白名单、置信度、确认语长度。任一不过就当 null。"""
    if not isinstance(data, dict):
        return dict(NULL)

    action = str(data.get("action") or "").strip()
    if action not in ACTIONS:
        return dict(NULL)

    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        return dict(NULL)
    if confidence < MIN_CONFIDENCE:
        return dict(NULL)

    say = str(data.get("say") or "").strip()[:MAX_SAY]
    return {"action": action, "confidence": round(min(1.0, confidence), 3), "say": say}


async def route_action(question: str, history, user_id: str,
                       run_id: str | None = None) -> dict:
    text = str(question or "").strip()[:200]
    if not text or not settings.nl_action:
        return dict(NULL)
    if not PROMPT or not llm.is_configured():
        return dict(NULL)

    messages = [{"role": "system", "content": PROMPT}]
    messages.extend(_turns(history))
    messages.append({"role": "user", "content": text})

    try:
        raw = await llm.complete(
            messages, user_id=user_id, run_id=run_id,
            temperature=0, response_format="json_object", max_tokens=80,
            timeout=settings.nl_action_timeout,
            model=settings.engine_model, retries=0,
        )
    except llm.LLMError as exc:
        logger.info("动作识别失败，按 null 处理：%s", exc)
        return dict(NULL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("动作识别异常，按 null 处理：%s", exc)
        return dict(NULL)

    data = engine.loads_json(raw)
    if not data:
        logger.info("动作识别返回不是 JSON，按 null 处理")
        return dict(NULL)

    result = sanitize(data)
    if result["action"]:
        logger.info("动作识别：%s（%.2f）← %s", result["action"], result["confidence"], text[:30])
    else:
        logger.info("动作识别为 null ← %s", text[:30])
    return result
