"""千问理解引擎：一段提示词，三个任务（transcribe / summarize / answer）。

设计要点（对应《小鹿-千问理解引擎提示词 v1》）：

1. 三段共用同一份 system 文本，只靠 `TASK = xxx` 切换，规则不会各写一套。
2. 模型只负责"看懂"，不负责"补全"：所有字段缺就留空，抽不出就退回规则。
3. 代码侧守门优先于模型自觉：
   · 药名带连接词 / 一药多名 → 丢弃；
   · evidence 不在原文 → 这一条丢弃；
   · 答案出现越界词、或数字不在 facts 里 → 整段丢弃，退回规则答案。
4. 任何异常都返回 None，调用方必须能退回旧逻辑。

提示词放在 `app/agent/prompts/lu_engine.txt`，随包发布，不依赖仓库根目录。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.agent import llm
from app.core.config import settings

logger = logging.getLogger("app.engine")

_PROMPT = Path(__file__).resolve().parent / "prompts" / "lu_engine.txt"
BASE_PROMPT = _PROMPT.read_text(encoding="utf-8") if _PROMPT.is_file() else ""

# 药名里出现这些词，说明模型把两味药并成了一条
BAD_NAME = re.compile(r"(和|及|与|还有|再加|一个|两个|开点|我给你|点)|(片|胶囊|颗粒).*(片|胶囊|颗粒)")
# 答案里一旦出现这些词，说明模型开始"给意见"了
OUT_OF_SCOPE = re.compile(
    r"可能是|应该是|考虑是|一般需要|通常|大概率|问题不大|不用担心|建议服用|建议停药"
)
UNIT_TOKEN = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|g|ml|片|粒|袋|支|单位)", re.I)
# 疗程必须有「吃/服/用 + 数字 + 天/周」的原话；「两周后复诊」不算疗程
DAYS_OK = re.compile(r"(?:吃|服|用|连服|共|吃满)\s*(?:\d+|[一二三四五六七八九十两]+)\s*(?:天|日|周|个星期|个月)")

_ALLOWED_TIMING = {"饭前", "饭后", "随餐", "空腹", "睡前", "早上", "晚上", "必要时"}
_ALLOWED_STATUS = {"positive", "denied", "unknown", "family"}
_ALLOWED_KIND = {"drug", "food", "other"}


def compact(text: str) -> str:
    return re.sub(r"[\s，。、；：,.;:！？!?（）()【】\[\]「」\"'“”‘’]", "", str(text or ""))


def is_ready() -> bool:
    return bool(BASE_PROMPT) and llm.is_configured()


def system_for(task: str, extra: str = "") -> str:
    tail = f"\n\nTASK = {task}"
    if extra:
        tail += "\n" + extra
    return BASE_PROMPT + tail


def _loads_json(raw: str) -> dict | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _s(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def accept_med(med: dict, transcript: str) -> bool:
    """药名守门：单药名、长度合理、evidence 能在原文里逐字找到。"""
    name = compact(med.get("name"))
    if not (2 <= len(name) <= 20):
        return False
    if BAD_NAME.search(name):
        return False
    src = compact(transcript)
    evidence = compact(med.get("evidence"))
    if evidence:
        return evidence in src
    # 没有 evidence 时，至少药名本身要在原文出现
    return name in src


def accept_allergen(item: dict, transcript: str) -> bool:
    if item.get("status") in ("denied", "unknown", "family"):
        return True
    return compact(item.get("substance")) in compact(transcript)


def check_answer(text: str, facts: str) -> bool:
    """答案守门：不许给意见，不许出现 facts 里没有的剂量。"""
    if not text or len(text) < 4:
        return False
    if OUT_OF_SCOPE.search(text):
        return False
    for token in UNIT_TOKEN.findall(text):
        if compact(token) not in compact(facts):
            return False
    return True


# ————————————————— 结构化 —————————————————
def _normalize_meds(raw_meds, transcript: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw_meds or []:
        if not isinstance(item, dict):
            continue
        if not accept_med(item, transcript):
            logger.info("丢弃药名未过守门：%s", item.get("name"))
            continue
        name = _s(item.get("name"), 40)
        if name in seen:
            continue
        seen.add(name)
        timing = _s(item.get("timing"), 12)
        days = re.sub(r"\D", "", _s(item.get("days"), 8))
        # 守门：原文没有「吃两周/用三天」这类疗程原话，就不能凭空给天数
        if days:
            evidence = _s(item.get("evidence"), 120)
            if not (DAYS_OK.search(evidence) or DAYS_OK.search(transcript)):
                logger.info("丢弃没有原文依据的疗程：%s %s 天", name, days)
                days = ""
        out.append({
            "name": name,
            "dose": _s(item.get("dose"), 24),
            "freq": _s(item.get("freq"), 24),
            "timing": timing if timing in _ALLOWED_TIMING else "",
            "days": days,
            "evidence": _s(item.get("evidence"), 120),
        })
    return out


def _normalize_allergens(raw_items, transcript: str) -> list[dict]:
    out: list[dict] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        substance = _s(item.get("substance"), 24)
        if not substance or not accept_allergen(item, transcript):
            continue
        status = _s(item.get("status"), 12) or "positive"
        kind = _s(item.get("kind"), 8) or "drug"
        out.append({
            "substance": substance,
            "kind": kind if kind in _ALLOWED_KIND else "other",
            "status": status if status in _ALLOWED_STATUS else "unknown",
            "reaction": _s(item.get("reaction"), 24),
            "evidence": _s(item.get("evidence"), 120),
        })
    return out


def _allergy_text(allergens: list[dict]) -> tuple[str, bool]:
    positive = [a for a in allergens if a["status"] == "positive"]
    denied = any(a["status"] == "denied" for a in allergens)
    if positive:
        parts = []
        for a in positive:
            parts.append(f"{a['substance']}（{a['reaction']}）" if a.get("reaction") else a["substance"])
        return "、".join(parts)[:160], False
    return "", denied


def _normalize_record(data: dict, transcript: str) -> dict:
    record = data.get("record") if isinstance(data.get("record"), dict) else data

    meds = _normalize_meds(record.get("meds"), transcript)
    allergens = _normalize_allergens(record.get("allergens"), transcript)
    allergy, allergy_denied = _allergy_text(allergens)

    discontinued: list[str] = []
    for item in record.get("discontinued") or []:
        name = _s(item.get("name") if isinstance(item, dict) else item, 40)
        if name and name not in discontinued:
            discontinued.append(name)

    notes = [_s(x, 120) for x in (record.get("notes") or []) if _s(x, 120)][:8]
    conditions = [_s(x, 40) for x in (record.get("conditions") or []) if _s(x, 40)][:12]

    holes = [_s(x, 12) for x in (record.get("holes") or []) if _s(x, 12)]
    if not holes:
        for key, label in (("hospital", "医院"), ("summary", "诊断"),
                           ("follow_up", "复诊")):
            if not record.get(key):
                holes.append(label)
        if not meds:
            holes.append("用药")

    self_check = [_s(x, 200) for x in (data.get("self_check") or []) if _s(x, 200)][:10]
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "hospital": _s(record.get("hospital"), 160),
        "summary": _s(record.get("summary"), 240),
        "meds": meds,
        "follow_up": _s(record.get("follow_up"), 160),
        "allergy": allergy,
        "allergy_denied": allergy_denied,
        "allergens": allergens,
        "conditions": conditions,
        "discontinued": discontinued,
        "notes": notes,
        "holes": holes,
        "visit_date": _s(record.get("visit_date"), 10),
        "reading": _s(data.get("reading"), 800),
        "self_check": self_check,
        "confidence": max(0.0, min(1.0, confidence)),
    }


async def summarize(transcript: str, user_id: str, run_id: str | None = None) -> dict | None:
    """TASK=summarize：先 reading 再 record 再 self_check。失败返回 None。"""
    if not is_ready() or not transcript.strip():
        return None
    messages = [
        {"role": "system", "content": system_for("summarize")},
        {"role": "user", "content": transcript[:6000]},
    ]
    try:
        raw = await llm.complete(
            messages, user_id=user_id, run_id=run_id,
            temperature=0, response_format="json_object",
            max_tokens=1500, timeout=45,
            model=settings.engine_model,
        )
    except llm.LLMError as exc:
        logger.warning("summarize 失败，退回规则抽取：%s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize 异常，退回规则抽取：%s", exc)
        return None

    data = _loads_json(raw)
    if not data:
        logger.warning("summarize 返回不是 JSON，丢弃")
        return None
    try:
        return _normalize_record(data, transcript)
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize 结果解析失败：%s", exc)
        return None


async def answer(question: str, facts: str, user_id: str, run_id: str | None = None) -> str | None:
    """TASK=answer：只按 facts 作答，带 [R] 引用。失败或越界返回 None。"""
    if not is_ready() or not facts.strip():
        return None
    messages = [
        {"role": "system", "content": system_for("answer")},
        {"role": "user", "content": f"【问题】{question}\n\n【档案】\n{facts}"},
    ]
    try:
        text = await llm.complete(
            messages, user_id=user_id, run_id=run_id,
            temperature=0, max_tokens=900, timeout=45,
            model=settings.engine_model,
        )
    except llm.LLMError as exc:
        logger.warning("answer 失败，退回规则答案：%s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("answer 异常，退回规则答案：%s", exc)
        return None

    text = str(text or "").strip()
    if not check_answer(text, facts):
        logger.warning("answer 未过守门，丢弃（%s 字）", len(text))
        return None
    return text


def parse_transcribe(raw: str) -> dict:
    """解析 TASK=transcribe 的输出；不是 JSON 就把整段当文本。"""
    data = _loads_json(raw)
    if not data:
        return {"text": str(raw or "").strip(), "uncertain": [], "segments": []}

    segments = []
    for item in data.get("segments") or []:
        if not isinstance(item, dict):
            continue
        text = _s(item.get("text"), 500)
        if not text:
            continue
        segments.append({
            "speaker": _s(item.get("speaker"), 12) or "说话人",
            "text": text,
            "uncertain": [_s(x, 40) for x in (item.get("uncertain") or []) if _s(x, 40)],
        })

    uncertain: list[str] = []
    for seg in segments:
        uncertain.extend(seg["uncertain"])

    full = _s(data.get("full_text"), 12000)
    if not full and segments:
        full = "\n".join(f"{s['speaker']}：{s['text']}" for s in segments)
    return {"text": full, "uncertain": uncertain[:20], "segments": segments,
            "notes": _s(data.get("asr_notes"), 200)}
