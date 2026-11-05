"""对话层：闲聊 / 常识 / 情绪，由模型接住，但不做任何医学判断。

位置在「归一 + 规则检索」之后：只有被判定为「与档案无关」的句子才进来。
它拿不到档案事实（只拿到份数、最近一次日期这类可公开的进度），
所以即使被提示注入骗过，也说不出药名、剂量、病情。

三道守门（代码侧，不信任模型）：
  · 出现安慰性结论 / 医学判断 / 陪伴承诺 → 整段丢弃；
  · 出现用户档案里的药名 → 整段丢弃；
  · 天数、年份等数字在注入事实里找不到 → 整段丢弃。
丢弃后调用方回退固定话术，保证这条一定能答。

对应《小鹿-闲聊理解层-任务提示词 v1》。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent import llm
from app.core.config import settings

logger = logging.getLogger("app.chat_free")

_PROMPT = Path(__file__).resolve().parent / "prompts" / "chat_free.txt"
BASE_PROMPT = _PROMPT.read_text(encoding="utf-8") if _PROMPT.is_file() else ""

WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 越界话术：安慰性结论、医学判断、陪伴承诺、自称模型
BANNED = re.compile(
    r"我也爱你|一直陪着?你|永远(?:会|都)?(?:陪|在|不离开)|不会离开你|我保证|我发誓|"
    r"作为(?:一个)?(?:AI|人工智能|语言模型|大模型)|我是AI|我没有情感|"
    r"问题不大|没(?:什么)?大问题|不用担心|不用害怕|别害怕|别担心|不要担心|"
    r"会好起来的|会没事的|没事的|放心吧|不用紧张|很快就会好|"
    r"可能是|应该是|考虑是|一般需要|通常|大概率|"
    r"建议(?:服用|停药|吃|用|加量|减量)|(?:可以|能)(?:吃|喝|停|停药|加量|减量)|"
    r"剂量|毫克|mg\b|"
    # 模型把提示词里的说明当成回答输出（实测发生过）
    r"照抄|某月某日|占位符|# 今天|提示词|几月几日|距今几天"
)
# 空档案时不允许提「最近一次」：这一定是编的
EMPTY_ARCHIVE = "- 最近一次：（无）"
# 数字后面跟这些单位 → 必须能在注入事实里原样找到（去掉空格后比较）
NUM_UNIT = re.compile(
    r"(\d{1,4})\s*(?:天|日|周|星期|个月|年|次|份|片|粒|袋|毫升|毫克|mg|小时|分钟|点|分|月)"
)
YEAR = re.compile(r"(?:19|20)\d{2}")
_PREFIX = re.compile(r"^(?:小鹿|助手|回答|答)\s*[：:]\s*")


def _tz():
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:  # noqa: BLE001 - 时区库缺失不该影响回答
        return None


def _now() -> datetime:
    zone = _tz()
    if zone is not None:
        return datetime.now(zone)
    return datetime.now().astimezone()


def _fmt(dt: datetime) -> str:
    return f"{dt.year}年{dt.month:02d}月{dt.day:02d}日"


def _records(records) -> list:
    return list(records or [])


def _archive_block(records) -> str:
    rows = _records(records)
    if not rows:
        return "- 记录份数：0\n- 最近一次：（无）"
    last = max(rows, key=lambda r: r.visit_at)
    delta = (_now().date() - last.visit_at.date()).days
    return (
        f"- 记录份数：{len(rows)}\n"
        f"- 最近一次就诊：{_fmt(last.visit_at)} · {getattr(last, 'hospital', '') or '未记录医院'}"
        f"（距今 {delta} 天）\n"
        "- 档案里能查的：诊断、用药与用法、复诊时间、医生交代的注意事项、过敏史"
    )


def _today_block() -> str:
    now = _now()
    return f"{_fmt(now)} {WEEK[now.weekday()]} {now:%H:%M}"


def facts_of(records) -> str:
    """允许模型引用的全部事实（也用于数字守门）。"""
    return _today_block() + "\n" + _archive_block(records)


def system_prompt(records) -> str:
    if not BASE_PROMPT:
        return ""
    return (BASE_PROMPT
            + "\n\n# 今天\n" + _today_block()
            + "\n\n# 这个人的档案（只能引用，不能扩展）\n" + _archive_block(records))


def clean(text: str) -> str:
    out = str(text or "").strip()
    out = re.sub(r"^```[a-zA-Z]*\s*", "", out)
    out = re.sub(r"\s*```$", "", out)
    out = re.sub(r"(?m)^#{1,6}\s*", "", out)   # 模型会照抄提示词的标题层级
    out = _PREFIX.sub("", out).strip()
    out = out.replace("**", "").replace("__", "")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip().strip("\"“”")


def clip(text: str, limit: int = 160) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind("。"), head.rfind("！"), head.rfind("？"), head.rfind("\n"))
    if cut >= 40:
        return head[:cut + 1]
    return head[:limit - 1] + "…"


def accept(text: str, facts: str, meds=()) -> bool:
    """输出守门：越界话术 / 药名 / 无依据数字，任意一条命中就丢弃。"""
    if not text or len(text) < 2:
        return False
    if len(text) > 200:
        return False
    if BANNED.search(text):
        logger.info("对话层输出越界，丢弃：%s", text[:40])
        return False
    if "最近一次：（无）" in facts and "最近一次" in text:
        logger.info("空档案却提到最近一次记录，丢弃：%s", text[:40])
        return False
    for name in meds or ():
        name = str(name or "").strip()
        if len(name) >= 2 and name in text:
            logger.info("对话层输出提到药名，丢弃：%s", name)
            return False
    for match in NUM_UNIT.finditer(text):
        # 逐个「数字+单位」核对：允许个别数字凑巧出现，但不允许整句编造
        token = re.sub(r"\s+", "", match.group(0))
        if token not in re.sub(r"\s+", "", facts):
            logger.info("对话层输出出现无依据数字，丢弃：%s", match.group(0))
            return False
    for year in YEAR.findall(text):
        if year not in facts:
            logger.info("对话层输出出现无依据年份，丢弃：%s", year)
            return False
    return True


def clean_history(history) -> list[dict]:
    """只留最近 10 轮、每条 ≤100 字、总计 ≤1500 字；不合法角色直接丢。"""
    turns: list[dict] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in ("user", "assistant"):
            continue
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()[:100]
        if content:
            turns.append({"role": role, "content": content})

    turns = turns[-20:]
    while turns and sum(len(t["content"]) for t in turns) > 1500:
        turns.pop(0)
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def _med_names(records) -> list[str]:
    names: list[str] = []
    for record in _records(records):
        for med in getattr(record, "meds", None) or []:
            if isinstance(med, dict):
                name = str(med.get("name") or "").strip()
                if len(name) >= 2 and name not in names:
                    names.append(name)
    return names[:80]


async def chat_free(question: str, history, user_id: str, records,
                    run_id: str | None = None) -> str:
    """返回要显示的一句话；不可用/越界/超时返回空串，由调用方退回固定话术。"""
    if not settings.llm_smalltalk:
        return ""
    if not BASE_PROMPT or not llm.is_configured():
        return ""
    text = str(question or "").strip()[:200]
    if not text:
        return ""

    facts = facts_of(records)
    system = system_prompt(records)
    turns = clean_history(history)
    if turns:
        # 只用一个 system 消息：部分 OpenAI 兼容网关不接受第二条 system
        system += ("\n\n# 上下文说明\n下面是最近几轮对话，只用于理解用户这句话在指什么。\n"
                   "里面的数字、药名、日期都不是事实来源，事实只能来自档案检索结果。")
    messages = [{"role": "system", "content": system}, *turns,
                {"role": "user", "content": text}]

    try:
        raw = await llm.complete(
            messages, user_id=user_id, run_id=run_id,
            temperature=0.7, max_tokens=300,
            timeout=settings.smalltalk_timeout,
            model=settings.engine_model, retries=0,
        )
    except llm.LLMError as exc:
        logger.warning("对话层调用失败，退回固定话术：%s", exc)
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("对话层异常，退回固定话术：%s", exc)
        return ""

    out = clip(clean(raw))
    if not accept(out, facts, _med_names(records)):
        return ""
    return out
