"""用模型校对规则抽取的结果（重点是药名、剂量、频次）。

分工：
  · 规则抽取决定「有哪几个药、出现在原文哪里」，它不会被模型推翻；
  · 模型只做「把药名写对、把漏掉的剂量补上」。

安全边界：模型返回的每一个字段都要能在原文里找到依据，找不到就保留规则值；
药品数量、顺序必须与规则结果一致，否则整次校对直接放弃。
"""

import difflib
import json
import logging
import re

from app.agent import llm
from app.agent.extractor import COMMON_DRUGS

logger = logging.getLogger("app.refine")

SYSTEM_PROMPT = (
    "你是病历用药信息的校对器。只输出 JSON 数组，不要解释，不要 Markdown 代码块。\n"
    "输入包含【原文】和【规则抽取】。你的任务是校对药名与用法，不是重新抽取。\n"
    "硬性规则：\n"
    "1. 数组长度与顺序必须和【规则抽取】完全一致，不得新增、删除、合并药品；\n"
    "2. 每个字段只有在【原文】里能找到依据时才修改，找不到就原样返回规则值；\n"
    "3. name 用原文里的规范药名，可补全剂型（如把「阿莫西林」写成「阿莫西林胶囊」），不得编造品牌；\n"
    "4. dose 只填规格或单次剂量（如 0.5g、20mg、100ml），「2 粒」「1 片」不算剂量；\n"
    "5. freq 写「每天两次」这类频次；timing 只写 饭前/饭后/睡前/空腹/随餐 之一；days 只写数字；\n"
    "6. 原文没有的字段一律留空字符串。"
)

_FIELDS = ("name", "dose", "freq", "timing", "days")
_LIMITS = {"name": 40, "dose": 24, "freq": 24, "timing": 12, "days": 6}


def _compact(text: str) -> str:
    """去掉空白与标点，便于做「原文里有没有」的判断。"""
    return re.sub(r"[\s，。、；：,.;:！？!?（）()【】\[\]「」\"'“”‘’]", "", str(text or ""))


def _parse_array(raw: str) -> list[dict] | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    return [x for x in data if isinstance(x, dict)]


_KNOWN = {_compact(x) for x in COMMON_DRUGS}


def _ok_name(rule_name: str, cand: str, src: str) -> bool:
    c = _compact(cand)
    if not c or len(c) > _LIMITS["name"]:
        return False
    if c in src:
        return True
    r = _compact(rule_name)
    # 规则结果只是候选名的子串（少写了剂型）也算有依据
    if r and r in c and r in src:
        return True
    # 同音/错字：候选是已知药名，且和规则结果高度相似（奥美啦唑 → 奥美拉唑）
    if c in _KNOWN and r:
        base = next((x for x in _KNOWN if x in c or c in x), c)
        return difflib.SequenceMatcher(None, r, base).ratio() >= 0.55
    return False


def _ok_value(field: str, cand: str, src: str) -> bool:
    c = _compact(cand)
    if not c or len(c) > _LIMITS[field]:
        return False
    if c in src:
        return True
    if field == "days":
        return c.isdigit() and c in src
    # 剂量：数字 + 单位必须在原文出现，避免模型自己换算
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(mg|g|ml|毫升|毫克|克|ug|μg|iu|万单位)", c, re.I)
    if field == "dose" and m:
        return m.group(1) in src and m.group(2).lower() in src.lower()
    return False


def _merge(rule: dict, cand: dict, src: str) -> dict:
    out = {k: str(rule.get(k) or "") for k in _FIELDS}
    for key in _FIELDS:
        val = str(cand.get(key) or "").strip()
        if key == "name":
            if _ok_name(out["name"], val, src):
                out["name"] = val
            continue
        if _ok_value(key, val, src):
            out[key] = val
    return out


async def refine_extract(transcript: str, result: dict, user_id: str) -> dict:
    """校对规则抽取的用药字段；任何异常都返回原结果。"""
    meds = result.get("meds") or []
    if not meds or not llm.is_configured():
        return result

    src = _compact(transcript)
    payload = [{k: str(m.get(k) or "") for k in _FIELDS} for m in meds]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "【原文】\n" + transcript[:3000] + "\n\n【规则抽取】\n"
            + json.dumps(payload, ensure_ascii=False),
        },
    ]

    started = __import__("time").monotonic()
    try:
        raw = await llm.complete(messages, user_id=user_id)
    except llm.LLMError as exc:
        logger.warning("药名校对失败，保留规则结果: %s", exc)
        return result
    except Exception as exc:  # 模型层任何异常都不该影响抽取
        logger.warning("药名校对异常，保留规则结果: %s", exc)
        return result

    cands = _parse_array(raw)
    if not cands or len(cands) != len(meds):
        logger.info("药名校对结果数量不符，丢弃（%s vs %s）", len(cands or []), len(meds))
        return result

    merged = [_merge(rule, cand, src) for rule, cand in zip(meds, cands)]
    changed = sum(1 for a, b in zip(meds, merged) if any(a.get(k) != b.get(k) for k in _FIELDS))
    logger.info("药名校对完成：%s 条，改动 %s 条，耗时 %.1fs",
                len(merged), changed, __import__("time").monotonic() - started)

    out = dict(result)
    out["meds"] = merged
    return out
