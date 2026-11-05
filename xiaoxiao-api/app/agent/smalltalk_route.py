"""闲聊分诊：用现有正则判断「这句话该不该进对话层」。

原则：不新增判定逻辑，只复用 `answer.py` 的正则对象。
今天能答的问题，明天一定还能答 —— 只要命中任一档案分支或带症状，
就原样交给 `answer.py`，闲聊层不碰。

`is_smalltalk()` 只在两种情况下被调用：
  · 归一（TASK=route）失败/超时，没有 intent 可用；
  · 需要给「急症 / 危机」做双保险。
"""

from __future__ import annotations

import re

# 自伤 / 自杀：比普通急症更优先，话术不同（12356 + 120，不接档案入口）
CRISIS = re.compile(
    r"想死|不想活|活够了|活着没(?:有)?意思|活着太累|不如死了|"
    r"自杀|轻生|结束(?:自己的)?生命|自残|伤害自己|跳楼|割腕|上吊|"
    r"一了百了|安眠药.*(?:吞|吃一把|全吃)"
)

# 症状词：出现且是第一人称/近指，就不当闲聊，交给规则分支
SYMPTOM = re.compile(
    r"疼|痛|晕|吐|痒|发烧|发热|失眠|乏力|胸闷|恶心|麻|肿|"
    r"不舒服|难受|拉肚子|便秘|咳嗽|喘不上气"
)
_OWN = re.compile(r"我|自己|这个|最近")

# 个体化医学问题：即便归一是 out_of_scope，也不交给对话层自由作答
MED_ASK = re.compile(
    r"能不能|可不可以|要不要|该不该|需不需要|严重|要紧|怎么治|如何治疗|"
    r"吃什么|吃啥|喝什么|忌口|是不是|会不会|有没有事|怎么回事|"
    r"什么病|啥病|预后|癌变|传染|多久能好|能不能好|"
    r"哪个好|哪种好|哪个更|有什么区别"
)
# 问的是小鹿自己怎么用：这类问题即使带「我」也可以直接答
APP_Q = re.compile(
    r"小鹿|App|app|应用|软件|导出|备份|账号|登录|注册|主题|提醒|日历|"
    r"怎么用|怎么弄|在哪|哪里找|怎么设置"
)


# 需要排除的分支：命中任意一个就说明是档案/医疗问题
_BRANCHES = (
    "EMERGENCY", "ADVICE", "MED_Q", "DOSE_Q", "FOLLOW_Q", "ALLERGY_Q",
    "NOTE_Q", "DIAGNOSIS_Q", "COUNT_Q", "LAST_Q", "DOCTOR_Q",
    "ADHERENCE", "IDENTITY", "CLOCK",
)


def is_crisis(question: str) -> bool:
    return bool(CRISIS.search(str(question or "")))


def simple_answerable(question: str) -> bool:
    """True = 可以不查档案、由对话层直接给一个简单答案。

    用于归一把问题判成 out_of_scope 之后：科普、常识、App 用法可以答；
    只要沾上个体化医学判断、用法用量、症状，就仍然走固定拒答。
    """
    text = str(question or "").strip()
    if not text or len(re.sub(r"[\s\W_]+", "", text)) <= 1:
        return False
    if CRISIS.search(text) or MED_ASK.search(text) or SYMPTOM.search(text):
        return False

    from app.agent import answer as A

    for name in ("EMERGENCY", "ADVICE", "MED_Q", "DOSE_Q", "DIAGNOSIS_Q", "ALLERGY_Q", "ADHERENCE"):
        pattern = getattr(A, name, None)
        if pattern is not None and pattern.search(text):
            return False
    if APP_Q.search(text):
        return True
    # 带「我/自己」的问题默认涉及本人情况，不自由作答
    return not _OWN.search(text)


def is_smalltalk(question: str, vocab=()) -> bool:
    """True = 与档案无关的闲聊/常识/情绪，可以进对话层。

    vocab 是本人档案里的诊断/药名/医院：句中提到这些词，一定不是闲聊，
    必须交给规则检索 —— 否则「我做过胃镜吗」这类问题会被当成聊天。
    """
    text = str(question or "").strip()
    if not text:
        return True
    # 纯标点 / 单字：交给对话层用一句话引导
    if len(re.sub(r"[\s\W_]+", "", text)) <= 1:
        return True
    if CRISIS.search(text):
        return False

    for term in vocab or ():
        term = str(term or "").strip()
        if len(term) >= 2 and term in text:
            return False

    from app.agent import answer as A

    for name in _BRANCHES:
        pattern = getattr(A, name, None)
        if pattern is not None and pattern.search(text):
            return False
    if SYMPTOM.search(text) and _OWN.search(text):
        return False
    return True


def _field(record, key: str):
    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


def symptom_only(question: str, vocab=()) -> bool:
    """只有症状、没有可检索的对象：answer.py 只会回「未查询到」，不如直说。

    命中任一档案分支、或提到档案里的实体，就返回 False，交给规则检索。
    """
    text = str(question or "").strip()
    if not text or CRISIS.search(text):
        return False
    for term in vocab or ():
        term = str(term or "").strip()
        if len(term) >= 2 and term in text:
            return False

    from app.agent import answer as A

    for name in _BRANCHES:
        pattern = getattr(A, name, None)
        if pattern is not None and pattern.search(text):
            return False
    return bool(SYMPTOM.search(text))


def vocab_of(records) -> list[str]:
    """档案里的实体词：诊断、疾病/事件、药名、医院。"""
    terms: list[str] = []

    def add(value) -> None:
        value = str(value or "").strip()
        if len(value) >= 2 and value not in terms:
            terms.append(value)

    for record in records or ():
        add(_field(record, "summary"))
        add(_field(record, "hospital"))
        for item in _field(record, "conditions") or []:
            add(item)
        for med in _field(record, "meds") or []:
            if isinstance(med, dict):
                add(med.get("name"))
    return terms[:200]
