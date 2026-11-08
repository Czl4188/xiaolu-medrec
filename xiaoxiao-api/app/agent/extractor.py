"""转写文本 → 结构化病历字段。

设计原则（对应整改报告 P0-01 / P0-02）：
1. **每种药独立绑定**自己的剂量/频次/服法/疗程。做法是以药名为锚点，
   把句子切成互不重叠的片段，只在片段内找剂量，绝不把第一种药的剂量
   复制给第二种。
2. **否定语境不产出事实**：「不用吃」「已停药」进 discontinued，
   「没有过敏」「否认过敏」进 allergy_denied，都不混进当前用药/过敏史。
3. 抽不到就留空（holes 如实返回），绝不按常识补。
"""

import re
from datetime import UTC, datetime

from app.agent import kb as drug_kb

MED_SUFFIX = "(?:片|胶囊|颗粒|口服液|糖浆|软膏|乳膏|滴眼液|喷雾剂|注射液|散|丸|凝胶|贴)"

# 真实问诊里医生经常只说药名、不带剂型（「阿莫西林 0.5g」）。
# 没有词典就只能漏抽，这里补一份常用药名，按长度从长到短匹配，避免截断。
COMMON_DRUGS = [
    "阿莫西林克拉维酸钾", "复方甘草片", "单硝酸异山梨酯", "厄贝沙坦氢氯噻嗪",
    "阿莫西林", "头孢克肟", "头孢呋辛", "头孢地尼", "阿奇霉素", "克拉霉素",
    "左氧氟沙星", "莫西沙星", "甲硝唑", "奥硝唑", "布洛芬", "对乙酰氨基酚",
    "双氯芬酸", "塞来昔布", "奥美拉唑", "雷贝拉唑", "埃索美拉唑", "兰索拉唑",
    "铝碳酸镁", "莫沙必利", "多潘立酮", "蒙脱石", "果胶铋", "硫糖铝",
    "二甲双胍", "格列美脲", "阿卡波糖", "胰岛素", "氨氯地平", "硝苯地平",
    "厄贝沙坦", "缬沙坦", "美托洛尔", "比索洛尔", "阿托伐他汀", "瑞舒伐他汀",
    "阿司匹林", "氯吡格雷", "华法林", "利伐沙班", "氯雷他定", "西替利嗪",
    "孟鲁司特", "沙丁胺醇", "布地奈德", "泼尼松", "地塞米松", "甲泼尼龙",
    "左甲状腺素", "碳酸钙", "叶酸", "别嘌醇", "非布司他", "坦索罗辛",
]
_COMMON_RE = "|".join(sorted(COMMON_DRUGS, key=len, reverse=True))
MED_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z]{2,14}" + MED_SUFFIX + r"|" + _COMMON_RE + r")")

# 明确否定：said 之后出现的药不吃
NEG_MED = re.compile(
    r"(?:不用|不要|不必|不能|别再|停了|停用|已经停|不需要)(?:再)?(?:吃|用|服用|服)?"
    r"|(?<![人区个特级类分性差告离])别(?:再)?(?:吃|用|服用|服)"
)
DENY_ALLERGY = re.compile(r"(?:没有|没|无|否认|不|未)[^。；;]{0,6}(?:过敏|不耐受)")

# 疾病名（含常见慢病与「××炎/病/症」构词）
DISEASE = re.compile(
    r"慢性?[\u4e00-\u9fa5]{0,6}(?:炎|病|症)|急性[\u4e00-\u9fa5]{0,6}(?:炎|病|症)|"
    r"[\u4e00-\u9fa5]{2,6}(?:炎|溃疡|结石|感染|综合征|骨折|增生|囊肿|息肉|栓塞|衰竭)|"
    r"高血压|糖尿病|冠心病|高血脂|高尿酸|痛风|贫血|甲亢|甲减|抑郁|焦虑|哮喘|慢阻肺|"
    r"幽门螺杆菌阳性|乙肝|丙肝|肝硬化|脂肪肝|肾结石|胆囊炎|阑尾炎|偏头痛"
)

# 医疗事件（检查、处置、手术）
EVENT = re.compile(
    r"胃镜|肠镜|气管镜|喉镜|CT|核磁|磁共振|B超|彩超|心电图|活检|穿刺|"
    r"手术|住院|出院|化疗|放疗|透析|支架|搭桥|输血|输液|疫苗|接种|孕检|体检"
)

CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

LEAD = re.compile(
    r"^(?:今天|昨天|前天|那天|当天|上午|下午|中午|晚上|早上|这次|上次|最近|当时|后来|然后|"
    r"前几天|上个月|这个月|本月|上周|上上周|本周|周末|又|就|还|我们|我|他|她|家属)?"
    r"(?:去|到|在|从|回|来)?(?:了|过)?"
)
LEAD2 = re.compile(
    r"^(?:医生|大夫|主任|护士|专家|检查结果|结果|报告)?"
    r"(?:说|讲|表示|认为|提示|诊断|考虑)?(?:还是|是|为|有|有点|可能|大概|应该|就是|：|:)?"
)
MED_LEAD = re.compile(
    r"^(?:医生|大夫|主任|护士|说|讲|表示|让我|叫我|嘱咐我|叮嘱我|又开|开了|开|给了|给|用了|用|"
    r"吃了|吃|服用|服|加上|加|还有|和|及|与|配|先|再|同时|另外|"
    r"不用吃|不用|不要吃|不要|不必吃|不必|不能吃|不能|别再吃|别再|停了|停用|已经停了|不需要吃|不需要)+"
)
NOT_DRUG = re.compile(
    r"^(?:饭前|饭后|餐前|餐后|睡前|空腹|早晨|早上|晚上|每天|每日|一次|两次|三次|"
    r"一片|两片|半片|服用|吃|喝|没有|不用)"
)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def cn_num(value: str) -> str:
    if not value:
        return ""
    if value.isdigit():
        return value
    if value == "十":
        return "10"
    if len(value) == 2 and value[0] == "十":
        return str(10 + CN_DIGITS.get(value[1], 0))
    if len(value) == 2 and value[1] == "十":
        return str(CN_DIGITS.get(value[0], 0) * 10)
    if len(value) == 3 and value[1] == "十":
        return str(CN_DIGITS.get(value[0], 0) * 10 + CN_DIGITS.get(value[2], 0))
    return str(CN_DIGITS.get(value, ""))


def strip_lead(text: str) -> str:
    s = clean(text)
    for _ in range(6):
        prev = s
        s = LEAD.sub("", s)
        s = LEAD2.sub("", s)
        s = re.sub(r"^[的了着过和与及、，,。；;：:\s]+", "", s)
        if s == prev or len(s) <= 3:
            break
    return s.strip()


def strip_med(text: str) -> str:
    s = clean(text)
    for _ in range(4):
        prev = s
        s = MED_LEAD.sub("", s)
        if s == prev or len(s) <= 3:
            break
    return s.strip()


def _sentences(text: str) -> list[str]:
    return [clean(x) for x in re.split(r"[。；;!?！？\n]", text) if clean(x)]


def _clause_before(text: str, index: int, span: int = 10) -> str:
    return text[max(0, index - span):index]


def _parse_med_window(window: str, before: str = "") -> dict:
    """只在这一味药自己的片段里找剂量/频次/服法/疗程。

    「每天吃两次阿莫西林 0.5g」的频次在药名前，所以窗口找不到时
    只回看同一分句的前文，不跨句借别的药的用法。
    """
    dose = re.search(r"(\d+(?:\.\d+)?\s*(?:mg|毫克|g|克|μg|ug|ml|毫升|单位|iu|片|粒))", window, re.I)
    timing = re.search(r"(饭前|饭后|餐前|餐后|睡前|空腹|早晨|早上|晚上|每晚|早晚)", window)
    if not timing and before:
        timing = re.search(r"(饭前|饭后|餐前|餐后|睡前|空腹|早晨|早上|晚上|每晚|早晚)", before)
    freq = re.search(r"((?:每日|每天|一日)\s*(?:吃|服)?\s*(?:[一二两三四1-4]\s*次)?|每晚|每早|一日三次|每日三次)", window)
    if not freq and before:
        freq = re.search(r"((?:每日|每天|一日)\s*(?:吃|服)?\s*(?:[一二两三四1-4]\s*次)?|每晚|每早|一日三次|每日三次)", before)
    days = (
        re.search(r"(?:吃|服|连服|共|吃满|用)\s*(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)", window)
        or re.search(r"(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)", window)
    )
    return {
        "dose": clean(dose.group(1)) if dose else "",
        "freq": clean(freq.group(1)) if freq else "",
        "timing": timing.group(1).replace("每晚", "睡前") if timing else "",
        "days": cn_num(days.group(1)) if days else "",
    }


def canonical(hit) -> str:
    """命中词的对外药名。

    商品名/旧称换成通用名（波立维 → 氯吡格雷）；
    本来就是通用名时保留原文粒度（「奥美拉唑肠溶胶囊」不缩成「奥美拉唑」）。
    """
    if drug_kb.is_alias(getattr(hit, "key", "")):
        return hit.entry.generic
    return strip_med(hit.group(1))


def _extract_meds(sentences: list[str]) -> tuple[list[dict], list[str]]:
    """以药名为锚点切窗，逐药独立解析；否定的药进 discontinued。"""
    meds: list[dict] = []
    discontinued: list[str] = []
    seen: set[str] = set()

    for sentence in sentences:
        # 词典优先（含商品名与别名），词典没命中的句子再退回旧的规则匹配
        hits = drug_kb.scan(sentence) or list(MED_RE.finditer(sentence))
        if not hits:
            continue
        for idx, hit in enumerate(hits):
            raw_name = hit.group(1)
            after = sentence[hit.end():hit.end() + 6]
            if after.startswith(("过敏", "不耐受")):
                continue  # 「青霉素过敏」里的青霉素是过敏原，不是服用的药
            # 检验/生化名目里含药名片段，但不是药：尿素氮、葡萄糖-6-磷酸脱氢酶
            if after.startswith(("氮", "酶")) or after.startswith(("-6-", "－6－", "-6－")):
                continue
            name = canonical(hit)
            if not name or len(name) < 2 or name in seen or NOT_DRUG.match(name):
                continue

            # 这一味药的片段：从本药结尾到下一味药的开头
            end = hits[idx + 1].start() if idx + 1 < len(hits) else len(sentence)
            window = sentence[hit.end():end]
            # 否定词常常在药名之前（「医生说不用吃阿莫西林」）。
            # 只看最后一个逗号之后的前文，避免把上一分句的否定带过来。
            before = _clause_before(sentence, hit.start(), 8)
            before = before.split("，")[-1].split(",")[-1]

            seen.add(name)

            # 否定/停药：可能写在前文，也可能被正则吞进药名本身
            neg_zone = before + window[:12]
            if NEG_MED.search(raw_name) or NEG_MED.search(neg_zone):
                discontinued.append(name)
                continue

            fields = _parse_med_window(window, before)
            meds.append({"name": name, **fields})

        # 句尾的疗程通常管这一整句的所有药
        tail = sentence[hits[-1].end():]
        tail_days = (
            re.search(r"(?:吃|服|连服|共|吃满|用)\s*(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)", tail)
            or re.search(r"(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)", tail)
        )
        if tail_days:
            value = cn_num(tail_days.group(1))
            if value:
                for med in meds:
                    if med["name"] in {canonical(h) for h in hits} and not med["days"]:
                        med["days"] = value

    return meds, discontinued


def _clean_condition(item: str) -> str:
    item = strip_lead(clean(item))
    item = re.sub(
        r"^(?:还|也|又|就|曾经|以前|去年|前年|今年|上个月|做过|做了|有|患|患有|查出|发现|结果|是|说|讲|表示)+",
        "",
        item,
    )
    return item.strip()


def _extract_conditions(positive: str) -> list[str]:
    found: list[str] = []
    for pattern in (DISEASE, EVENT):
        for m in pattern.finditer(positive):
            item = _clean_condition(m.group(0))
            # 「什么病」「啥病」这类疑问不算疾病
            if not item or len(item) < 2 or re.match(r"^(?:什么|啥|哪种|这种)", item):
                continue
            if item not in found:
                found.append(item)
    # 去掉被更长条目包含的短项（「慢性胃炎」与「慢性非萎缩性胃炎」只留后者）
    found = [x for x in found if not any(x != y and x in y for y in found)]
    return found[:12]


def _parse_visit_date(text: str) -> datetime | None:
    """从原文里取就诊日期；没有年份时按最近一次出现推断。

    只认「年月日 / - / . / 月日」这类明确写法，不把「每天两次」当日期。
    """
    now = datetime.now(UTC).astimezone()
    today = now.date()
    m = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?", text)
    year = int(m.group(1)) if m else None
    if not m:
        m = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if not m:
        return None

    month = int(m.group(1)) if year is None else int(m.group(2))
    day = int(m.group(2)) if year is None else int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None

    if year is None:
        year = today.year
        try:
            candidate = today.replace(year=year, month=month, day=day)
        except ValueError:
            return None
        if (candidate - today).days > 14:
            year -= 1
    try:
        return datetime(year, month, day, 9, 0, tzinfo=now.tzinfo)
    except ValueError:
        return None


def extract(text: str, visit_at: datetime | None = None) -> dict:
    raw = clean(text)
    hint = _parse_visit_date(raw)
    # 否定整句用来判断「过敏否认」；其余抽取用原文，避免误伤「不用吃阿莫西林」这种仍需记录停药的句子
    sentences = _sentences(raw)

    # —— 医院 ——
    hospital = ""
    hosp_src = re.sub(
        r"(?:20\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*[日号]?|"
        r"20\d{2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}|"
        r"今天|昨天|前天|上午|下午|中午|晚上|早上|上周|上上周|本周|周末",
        " ",
        raw,
    )
    m = re.search(r"([\u4e00-\u9fa5]{2,18}(?:医院|卫生院|诊所|门诊部|卫生服务中心))", hosp_src)
    if m:
        candidate = strip_lead(m.group(1))
        if not re.match(r"^(?:没|未|不|别|无|哪)", candidate) and candidate != "医院":
            hospital = candidate

    # —— 诊断 ——
    summary = ""
    m = re.search(r"(?:诊断|考虑|判断|结论|说是|应该是|确诊)(?:为|是|：|:)?\s*([^。；;，,\n]{2,24})", raw)
    if not m:
        m = re.search(r"说(?:还是|是|可能|大概|有点)([^。；;，,\n]{2,24})", raw)
    if m:
        summary = strip_lead(clean(m.group(1)))
        if re.match(r"^(?:什么|啥|哪种|这种)", summary):
            summary = ""
    if not summary:
        dm = DISEASE.search(raw)
        if dm:
            cand = _clean_condition(dm.group(0))
            # 「没说是啥病」里的「啥病」不是诊断
            if not re.match(r"^(?:什么|啥|哪种|这种)", cand) and cand not in ("病", "症"):
                summary = cand
    summary = re.split(r"[，,。；;]", summary)[0][:24]

    # —— 用药（逐药独立绑定）——
    meds, discontinued = _extract_meds(sentences)

    # —— 复诊 ——
    follow_up = ""
    fm = re.search(
        r"((?:[一二三四五六两\d]+)\s*(?:周|个星期|个月|月|天)\s*(?:后|以后|之后)[^。；;]{0,6}(?:复诊|复查|再来|随访))",
        raw,
    )
    if not fm:
        fb = re.search(r"((?:复诊|复查|随访)[^。；;]{0,12})", raw)
        if fb:
            prefix = raw[max(0, fb.start() - 6):fb.start()]
            if not re.search(r"[没未不别无]", prefix):
                fm = fb
    if fm:
        value = clean(fm.group(1))
        tail = re.sub(r"^.*?(?:复诊|复查|随访)[：:，,\s]*", "", value)
        if len(tail) >= 2 or re.search(r"[一二三四五六两\d]+\s*(?:周|天|个月|月)", value):
            follow_up = value

    # —— 过敏 / 过敏否认 ——
    allergy = ""
    allergy_denied = bool(DENY_ALLERGY.search(raw))
    if not allergy_denied:
        am = re.search(r"((?:对)?[\u4e00-\u9fa5A-Za-z]{1,10}(?:过敏|不耐受))", raw)
        if am:
            allergy = strip_lead(clean(am.group(1)))
            for _ in range(4):
                prev = allergy
                allergy = re.sub(r"^(?:患者|病人|本人|家属|孩子|老人|对|有|他|她|我|也|还|说)+", "", allergy)
                if allergy == prev:
                    break

    # —— 注意事项 ——
    note_re = re.compile(
        r"注意|忌口|避免|少吃|少喝|多喝|休息|不要|别吃|别喝|定期|戒烟|戒酒|低盐|低脂|清淡|按时"
    )
    notes = []
    for sentence in sentences:
        if note_re.search(sentence):
            cleaned = re.sub(
                r"^(?:医生|大夫|主任)?(?:嘱咐|交代|叮嘱|建议|提醒|说|让|要求|记得)+", "", sentence
            ).strip()
            if cleaned:
                notes.append(cleaned)
    notes = notes[:4]

    # —— 具体疾病与医疗事件（整改要求：进档案，而不只躺在原文里）——
    conditions = _extract_conditions(raw)
    if summary and summary not in conditions:
        conditions.insert(0, summary)
    # 并入 summary 后再去一次重：被更长条目包含的短项没有信息量
    conditions = [x for x in conditions if not any(x != y and x in y for y in conditions)]

    holes = []
    if not hospital:
        holes.append("医院")
    if not summary:
        holes.append("诊断")
    if not meds:
        holes.append("用药")
    if not follow_up:
        holes.append("复诊")

    return {
        "visit_at": visit_at or hint or datetime.now(UTC),
        "visit_hint": hint,
        "hospital": hospital,
        "summary": summary,
        "meds": meds,
        "follow_up": follow_up,
        "allergy": allergy,
        "allergy_denied": allergy_denied,
        "conditions": conditions,
        "discontinued": discontinued,
        "notes": notes,
        "holes": holes,
        "transcript": raw,
    }
