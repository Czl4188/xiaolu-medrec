"""基于档案作答。

本轮（严格复审）重点：
  · P0-02 按日期 / 医院 / 第几次查询，条件必须取交集，不允许拿最近记录顶替；
  · P0-03 过敏史跨记录汇总，最近一次空白不得掩盖既往阳性；
  · P1-01 引用与答案必须指向同一条记录；
  · 急症、诊疗建议、服药打卡仍然独立拒答。

模型只负责把结论说顺，不参与「答什么」。
"""

import re
from datetime import UTC, datetime

DISCLAIMER = "本内容仅为病历档案整理，不能替代医生诊疗意见。"

# —— 急症：最高优先级，先于一切 ——
EMERGENCY = re.compile(
    r"胸痛|胸口疼|心口疼|胸闷|呼吸困难|喘不上气|窒息|昏迷|意识不清|晕倒|晕厥|抽搐|"
    r"大出血|吐血|咯血|便血|黑便|剧烈头痛|疼得受不了|高烧不退|持续高热|"
    r"半身不遂|嘴歪|说话不清|自杀|想死|服毒|过量服药"
)

ADVICE = re.compile(
    r"能不能(吃|喝|用|做)|可不可以(吃|喝|用)|可以.*(吃|喝).*吗|能吃吗|能喝吗|"
    r"要不要(停药|继续吃|停|手术|复查)|该不该(吃|停|做)|需不需要(吃|停|做)|"
    r"(可以|能)(停|停掉|不吃|减量|加量)|严重吗|要紧吗|怎么治|如何治疗|吃什么药好"
)
IDENTITY = re.compile(r"你叫什么|你是谁|你的名字|你是什么|你能做什么|你会什么|介绍一下你|自我介绍")
CLOCK = re.compile(r"几点|现在时间|今天几号|今天日期|现在是哪天|今天星期几")
ADHERENCE = re.compile(r"吃药了吗|服药了吗|吃过了吗|有没有漏|漏吃|忘记吃|按时吃了吗|今天吃了没")

DIAGNOSIS_Q = re.compile(r"得了什么病|什么病|诊断|为什么去医院|为什么看|就诊原因|什么问题|怎么了|病情")
MED_Q = re.compile(r"开了什么药|开的什么药|什么药|用药|药单|几种药|吃哪些药|吃什么药")
DOSE_Q = re.compile(r"剂量|多少毫克|吃几片|怎么吃|饭前|饭后|服法|一天几次|频次|疗程|吃几天")
FOLLOW_Q = re.compile(r"复诊|复查|随访|什么时候再去")
ALLERGY_Q = re.compile(r"过敏|禁忌")
NOTE_Q = re.compile(r"注意|忌口|交代|医嘱|忌什么")
# 「医生说了什么 / 怎么嘱咐的」：问的是这次就诊的整段内容，不该拒答
DOCTOR_Q = re.compile(r"医生说了|医生说的|医生怎么|嘱咐|说了什么|说了啥|怎么说的|原话|讲了什么")
COUNT_Q = re.compile(r"几次|多少次|一共.*次|看过.*回")
LAST_Q = re.compile(r"上次|上回|最近一次|什么时候看|哪天看|时间线|就诊历史|最近.*医院")
# 问的是「全部 / 都用过 / 一共」，要跨记录汇总，不能只答最近一份
HISTORY_Q = re.compile(r"所有|全部|都(?:有|用|吃|开|记)?|一直|曾经|历史|历次|一共|哪些药|用过什么|吃过什么|开过什么")
# 明确指向某一次，只答那一次
SINGLE_Q = re.compile(r"上次|上回|最近|这次|那次|刚刚|刚才|今天")

# 具体临床事件/疾病：用户问到了、档案里却没有，就不能拿别的记录顶上
CLINICAL = re.compile(
    r"支架|搭桥|化疗|放疗|透析|手术|住院|输液|胃镜|肠镜|核磁|CT|B超|活检|"
    r"骨折|哮喘|糖尿病|高血压|冠心病|甲亢|甲减|抑郁|怀孕|孕检|疫苗|输血"
)

WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# —— 结构化查询：日期 / 医院 / 第几次 ——
DATE_ISO = re.compile(r"(?<!\d)(20\d{2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)")
DATE_CN = re.compile(r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
HOSPITAL_TOKEN = re.compile(r"([\u4e00-\u9fa5]{2,14}(?:医院|卫生院|诊所|门诊部|卫生服务中心))")
ORDINAL = re.compile(r"第\s*([一二两三四五六七八九十\d]+)\s*次")
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

HOSPITAL_STOP = (
    "为什么", "什么", "哪家", "哪个", "怎么", "上次", "最近", "这次", "那次",
    "去", "到", "在", "从", "回", "来", "当地", "附近", "一家", "这家", "那家",
)


def fmt_date(dt: datetime) -> str:
    return f"{dt.year}年{dt.month:02d}月{dt.day:02d}日"


def cite_of(rec: dict) -> dict:
    return {
        "record_id": rec["id"],
        "client_id": rec.get("client_id", ""),
        "visit_at": rec["visit_at"],
        "hospital": rec.get("hospital", ""),
    }


def _sorted(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: r["visit_at"], reverse=True)


def _med_line(med: dict) -> str:
    bits = [med.get("name", "")]
    for key in ("dose", "freq", "timing"):
        if med.get(key):
            bits.append(med[key])
    if med.get("days"):
        bits.append(f"{med['days']} 天")
    return " · ".join(b for b in bits if b)


def _all_conditions(records: list[dict]) -> list[str]:
    out: list[str] = []
    for rec in records:
        for item in (rec.get("conditions") or []):
            if item and item not in out:
                out.append(item)
        summary = rec.get("summary")
        if summary and summary not in out:
            out.append(summary)
    return out


def _blob(records: list[dict]) -> str:
    parts = []
    for r in records:
        parts.extend([
            r.get("hospital", ""), r.get("summary", ""), r.get("transcript", ""),
            r.get("follow_up", ""), r.get("allergy", ""),
            " ".join(r.get("conditions") or []),
            " ".join(r.get("notes") or []),
            " ".join(str(m.get("name", "")) for m in (r.get("meds") or [])),
        ])
    return " ".join(parts)


def _missing_clinical(question: str, records: list[dict]) -> list[str]:
    mentioned = set(CLINICAL.findall(question))
    if not mentioned:
        return []
    blob = _blob(records)
    return sorted(m for m in mentioned if m not in blob)


# ————————————————— 结构化条件 —————————————————
def _cn_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if len(value) == 2 and value[0] == "十":
        return 10 + CN_NUM.get(value[1], 0)
    if len(value) == 2 and value[1] == "十":
        return CN_NUM.get(value[0], 0) * 10
    return CN_NUM.get(value)


def _parse_dates(question: str) -> list[dict]:
    """解析完整日期、年月日、月日；没有年份的按「最近一次出现」推断。"""
    today = datetime.now(UTC).astimezone().date()
    found: list[dict] = []

    for m in DATE_ISO.finditer(question):
        found.append({"year": int(m.group(1)), "month": int(m.group(2)), "day": int(m.group(3))})

    for m in DATE_CN.finditer(question):
        year = int(m.group(1)) if m.group(1) else None
        month, day = int(m.group(2)), int(m.group(3))
        if month < 1 or month > 12 or day < 1 or day > 31:
            continue
        if year is None:
            year = today.year
            try:
                candidate = today.replace(year=year, month=month, day=day)
            except ValueError:
                continue
            # 还没到、且差得远，说明说的是去年
            if (candidate - today).days > 14:
                year -= 1
        found.append({"year": year, "month": month, "day": day})

    dedup: list[dict] = []
    for item in found:
        if item not in dedup:
            dedup.append(item)
    return dedup


def _date_text(conds: list[dict]) -> str:
    return "、".join(f"{c['year']}年{c['month']}月{c['day']}日" for c in conds)


def _date_match(rec: dict, cond: dict) -> bool:
    dt = rec["visit_at"]
    return dt.year == cond["year"] and dt.month == cond["month"] and dt.day == cond["day"]


def _hosp_core(name: str) -> str:
    s = re.sub(r"\s+", "", str(name or ""))
    return re.sub(r"^(?:市|省|区|县|镇|街道)", "", s)


def _hosp_match(candidate: str, target: str) -> bool:
    a, b = _hosp_core(candidate), _hosp_core(target)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _parse_hospitals(question: str, records: list[dict]) -> list[str]:
    """先认档案里出现过的医院；再尝试解析问题里的医院名。"""
    found: list[str] = []
    for rec in records:
        hospital = rec.get("hospital") or ""
        if hospital and hospital != "未记录医院" and hospital in question and hospital not in found:
            found.append(hospital)

    for m in HOSPITAL_TOKEN.finditer(question):
        token = m.group(1)
        token = re.sub(r"^(?:" + "|".join(HOSPITAL_STOP) + r")+", "", token)
        token = re.sub(r"^(?:去|到|在|从|回|来|了)+", "", token)
        if len(token) < 4 or token in ("医院", "人民医院", "中心医院"):
            continue
        if token in found:
            continue
        if any(stop in token for stop in ("为什么", "什么", "哪家", "怎么")):
            continue
        found.append(token)
    return found


def _parse_ordinal(question: str) -> int | None:
    m = ORDINAL.search(question)
    if not m:
        return None
    return _cn_int(m.group(1))


def _filter_pool(question: str, records: list[dict]) -> tuple[list[dict], bool, str]:
    """按日期 / 医院 / 第几次过滤，返回 (匹配记录, 是否有显式条件, 条件描述)。"""
    dates = _parse_dates(question)
    hospitals = _parse_hospitals(question, records)
    ordinal = _parse_ordinal(question)
    explicit = bool(dates or hospitals or ordinal is not None)

    pool = list(records)
    if dates:
        pool = [r for r in pool if any(_date_match(r, d) for d in dates)]
    if hospitals:
        pool = [r for r in pool if any(_hosp_match(r.get("hospital", ""), h) for h in hospitals)]
    if ordinal is not None:
        asc = sorted(pool, key=lambda r: r["visit_at"])
        pool = [asc[ordinal - 1]] if 1 <= ordinal <= len(asc) else []

    bits: list[str] = []
    if dates:
        bits.append("日期 " + _date_text(dates))
    if hospitals:
        bits.append("医院 " + "、".join(hospitals))
    if ordinal is not None:
        bits.append(f"第 {ordinal} 次")
    return pool, explicit, "，".join(bits)


def _all_meds(records: list[dict]) -> list[tuple[dict, dict]]:
    """按「最近优先」跨记录收集药，同名药只保留最近一次。"""
    seen: set[str] = set()
    out: list[tuple[dict, dict]] = []
    for rec in records:
        for med in rec.get("meds") or []:
            name = str(med.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append((med, rec))
    return out


def _named_med(question: str, records: list[dict]) -> tuple[dict, dict] | None:
    """问题里点名了某个药，就在全部档案里找它最近的一次。"""
    q = question.replace(" ", "")
    for rec in records:
        for med in rec.get("meds") or []:
            name = str(med.get("name") or "").replace(" ", "")
            if len(name) < 2:
                continue
            core = re.sub(r"(胶囊|片|颗粒|口服液|注射液|软膏|滴剂|喷雾剂|缓释片|肠溶片|肠溶胶囊)$", "", name)
            if name in q or (len(core) >= 2 and core in q):
                return med, rec
    return None


def _select(question: str, records: list[dict], need: str = "") -> tuple[dict | None, int, bool]:
    pool, explicit, _ = _filter_pool(question, records)
    if explicit:
        if not pool:
            return None, 0, True
        return pool[0], len(pool), True
    if need == "meds":
        for rec in records:
            if rec.get("meds"):
                return rec, 0, False
    return (records[0] if records else None), 0, False


def _select_meds(question: str, records: list[dict]):
    """药单类问题专用：符合条件的最近一次如果没开药，就向前找同条件下最近的有药记录。

    「市第一人民医院开了什么药」不该因为最近一次复查没开药就答「没有」——
    同一家医院更早的处方仍是对这个问题的有效回答，但必须说明取的是哪一次。
    """
    target, matched, explicit = _select(question, records, need="meds")
    skipped = None
    if target is not None and explicit and not (target.get("meds") or []):
        pool, _, _ = _filter_pool(question, records)
        fallback = next((r for r in pool if r.get("meds")), None)
        if fallback is not None:
            skipped = target
            target = fallback
    return target, matched, explicit, skipped


def _no_match(question: str, records: list[dict]) -> dict:
    _, _, label = _filter_pool(question, records)
    cond = label or "这组条件"
    available = "、".join(
        f"{fmt_date(r['visit_at'])} {r.get('hospital') or '未记录医院'}" for r in records[:4]
    )
    return {
        "answer": f"没有找到符合「{cond}」的记录，无法回答。\n\n"
                  f"你的档案里共有 {len(records)} 份记录"
                  + (f"，最近几次是：{available}。" if available else "。")
                  + "\n\n可以问「我一共看过几次」查看完整时间线，或换一个日期 / 医院再问。",
        "citations": [],
        "refused": True,
    }


def _where(rec: dict) -> str:
    return f"{fmt_date(rec['visit_at'])} {rec.get('hospital') or ''}".strip()


def _allergy_answer(records: list[dict]) -> dict:
    """过敏史跨记录汇总：阳性记录优先，其次才是「否认」，空白不等于没有。"""
    positive = [r for r in records if (r.get("allergy") or "").strip()]
    denied = [r for r in records if r.get("allergy_denied")]

    if positive:
        lines = [f"档案里记过 {len(positive)} 次过敏："]
        for rec in positive[:4]:
            lines.append(f"- {rec['allergy']}（{_where(rec)}）")
        if len(positive) > 4:
            lines.append(f"- 还有 {len(positive) - 4} 次，去「我的」翻完整记录。")
        if denied:
            lines.append("")
            lines.append("另有记录写过「没有过敏」；这只代表当时那次问诊的说法，不能覆盖上面的阳性记录。")
        return {
            "answer": "\n".join(lines)
                      + "\n\n过敏史会跨记录保留：新记录空着，不会把旧记录里的过敏抹掉。",
            "citations": [cite_of(r) for r in positive[:3]],
            "refused": False,
        }

    if denied:
        rec = denied[0]
        return {
            "answer": f"档案里写的是「未发现药物过敏」，出自 {_where(rec)}。\n\n"
                      "这是当时那次的记录，之后有变化要重新确认；空着或否认都不等于永远不过敏。",
            "citations": [cite_of(rec)],
            "refused": False,
        }

    return {
        "answer": "所有记录里都没有写过过敏史。\n\n"
                  "「没记录」不等于「没有过敏」——这一栏空着，需要你自己补记，或下次问医生。",
        "citations": [],
        "refused": True,
    }


def answer(question: str, records: list[dict]) -> dict:
    q = question.strip()
    recs = _sorted(records)
    last = recs[0] if recs else None

    # —— 0. 急症：不查档案、不给建议，直接引导就医 ——
    if EMERGENCY.search(q):
        return {
            "answer": "这属于可能需要紧急处理的情况，我不能通过病历问答帮你判断。\n\n"
                      "请立即拨打 120（或当地急救电话），或尽快到最近的急诊。\n"
                      "如果是胸痛、呼吸困难、意识不清、大出血这类症状，不要自行服药、不要等待观察。\n\n"
                      "等你稳定后，我可以帮你把这次就诊整理进档案。",
            "citations": [],
            "refused": True,
            "emergency": True,
        }

    # —— 1. 求诊疗建议 ——
    if ADVICE.search(q):
        return {
            "answer": "这条我不能答。\n\n"
                      "能不能吃、要不要停药、严不严重，这类判断属于诊疗，不在记录工具的范围内。\n\n"
                      "我能告诉你的是：医生当时怎么交代的、开了哪些药、什么时候复诊。"
                      "可以问「医生交代了什么注意事项」。",
            "citations": [],
            "refused": True,
        }

    # —— 2. 身份类闲聊 ——
    if IDENTITY.search(q):
        return {
            "answer": "我是小鹿，只做一件事：把你看病时医生说的话，整理成能翻回去的记录。\n\n"
                      "你可以把问诊录音或文字给我，我抽出医院、诊断、疾病与事件、用药、复诊时间并存进你的档案；"
                      "之后问我「上次什么时候」「今天吃什么药」「我得了什么病」，我只从你的记录里找答案。"
                      "我不做诊断，也不给用药建议。",
            "citations": [],
            "refused": False,
        }

    # —— 3. 系统时间 ——
    if CLOCK.search(q):
        now = datetime.now(UTC).astimezone()
        if last:
            delta = (now.date() - last["visit_at"].date()).days
            tail = f"按这个时间算，你最近一次记录是 {fmt_date(last['visit_at'])}，距今 {delta} 天。"
        else:
            tail = "你的档案里还没有记录。"
        return {
            "answer": f"我这边读到的系统时间是：{now.year}年{now.month}月{now.day}日 "
                      f"{WEEK[now.weekday()]} {now:%H:%M:%S}。\n\n{tail}",
            "citations": [cite_of(last)] if last else [],
            "refused": False,
        }

    # —— 4. 没有档案 ——
    if not recs:
        if re.search(r"上次|什么时候看|几次|记录|档案", q):
            return {"answer": "你的档案里还没有记录，所以这个问题我答不了。\n\n"
                              "先把一次问诊整理进来（录音或粘贴文字都行），之后就能问了。",
                    "citations": [], "refused": True}
        return {"answer": "病历档案未查询到该内容，无法回答。\n\n你的档案里还没有任何记录。",
                "citations": [], "refused": True}

    # —— 5. 问到了档案里没有的具体临床事件 ——
    missing = _missing_clinical(q, recs)
    if missing:
        return {
            "answer": "病历档案未查询到该内容，无法回答。\n\n"
                      f"你问的是「{'、'.join(missing)}」，但你的 {len(recs)} 份记录里都没有提到过。\n"
                      "如果确实做过，建议补一份对应记录，或下次复诊时问医生。",
            "citations": [],
            "refused": True,
        }

    # —— 6. 服药状态：档案里没有打卡数据，不能编 ——
    if ADHERENCE.search(q):
        return {
            "answer": "这个我无法确认。\n\n"
                      "档案里记录的是「医生开的药」，不是你实际吃没吃。我没有服药打卡数据，"
                      "猜一个答案对你是危险的。\n\n"
                      "你要问的是「今天应该吃什么」，可以说「我今天要吃什么药」。",
            "citations": [cite_of(last)],
            "refused": True,
        }

    # —— 7. 诊断 / 疾病 / 就诊原因 ——
    if DIAGNOSIS_Q.search(q):
        target, matched, explicit = _select(q, recs)
        if explicit and not target:
            return _no_match(q, recs)
        if target is None:
            target = last
        conditions = _all_conditions([target])
        if not conditions:
            return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                              f"{_where(target)} 这次没有记录诊断。",
                    "citations": [], "refused": True}
        lines = [f"按 {_where(target)} 这次的记录："]
        lines.extend(f"- {c}" for c in conditions)
        if explicit and matched > 1:
            lines.append(f"（同一天 / 同一家医院有 {matched} 次记录，上面是最近的一次。）")
        cites = [cite_of(target)]
        if not explicit and len(recs) > 1:
            others = []
            for rec in recs:
                if rec["id"] == target["id"]:
                    continue
                cs = _all_conditions([rec])
                if not cs and rec.get("summary"):
                    cs = [rec["summary"]]
                if cs:
                    others.append((rec, list(dict.fromkeys(cs))))
            if others:
                lines.append("")
                lines.append("更早的记录里也记了：")
                for rec, cs in others[:5]:
                    lines.append(f"- {fmt_date(rec['visit_at'])}：{'、'.join(cs)}")
                if len(others) > 5:
                    lines.append(f"（还有 {len(others) - 5} 次未列出。）")
                for rec, _ in others[:2]:
                    c = cite_of(rec)
                    if c["record_id"] not in {x["record_id"] for x in cites}:
                        cites.append(c)
        return {"answer": "\n".join(lines), "citations": cites, "refused": False}

    # —— 8. 药单（先定位记录，再取该次用药）——
    if MED_Q.search(q):
        target, matched, explicit, skipped = _select_meds(q, recs)

        # 8a. 「都用过什么药 / 一共开过哪些」：跨全部档案汇总，同名以最近一次为准
        if not explicit and HISTORY_Q.search(q) and not LAST_Q.search(q):
            items = _all_meds(recs)
            if not items:
                return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                                  f"你的 {len(recs)} 份记录里都没有用药。",
                        "citations": [], "refused": True}
            lines = [f"你的 {len(recs)} 份记录里一共提到 {len(items)} 种药（同名以最近一次为准）："]
            for m, rec in items[:12]:
                lines.append(f"- {_med_line(m)} —— {_where(rec)}")
            if len(items) > 12:
                lines.append(f"（还有 {len(items) - 12} 种未列出。）")
            cites = []
            for _, rec in items:
                c = cite_of(rec)
                if c["record_id"] not in {x["record_id"] for x in cites}:
                    cites.append(c)
            return {"answer": "\n".join(lines), "citations": cites[:4], "refused": False}

        if explicit and not target:
            return _no_match(q, recs)
        if target is None:
            target = last
        meds = target.get("meds") or []
        if not meds:
            return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                              f"{_where(target)} 这次没有记录用药。",
                    "citations": [], "refused": True}
        lines = [f"按 {_where(target)} 这次记录，共 {len(meds)} 种："]
        lines.extend(f"- {_med_line(m)}" for m in meds)
        if skipped is not None:
            lines.append(f"（{_where(skipped)} 那次没有记录用药，上面是同一条件下最近一次有用药的记录。）")
        elif explicit and matched > 1:
            lines.append(f"（有 {matched} 次记录符合条件，上面是最近的一次。）")
        elif not explicit and target["id"] != last["id"]:
            lines.append(f"（这是最近一份有用药记录的档案；更新的一次 {fmt_date(last['visit_at'])} 没有开药。）")

        # 8b. 没有指定日期/医院时，把更早记录里的药也列出来；指定了就严格只答那一次
        older = ([] if explicit else
                 [(m, r) for (m, r) in _all_meds(recs) if r["id"] != target["id"]])
        if older:
            lines.append("")
            lines.append("更早的记录里还提到：")
            for m, rec in older[:5]:
                lines.append(f"- {_med_line(m)}（{fmt_date(rec['visit_at'])}）")
            if len(older) > 5:
                lines.append(f"（还有 {len(older) - 5} 种未列出。）")

        cites = [cite_of(target)]
        for _, rec in older[:2]:
            c = cite_of(rec)
            if c["record_id"] not in {x["record_id"] for x in cites}:
                cites.append(c)
        return {"answer": "\n".join(lines), "citations": cites, "refused": False}

    # —— 9. 剂量 / 服法 / 疗程 ——
    if DOSE_Q.search(q):
        target, matched, explicit, skipped = _select_meds(q, recs)

        # 问题点名了某个药：去全部档案里找它最近的一次，而不是只看最近那份记录
        named = None if explicit else _named_med(q, recs)
        if named:
            med, rec = named
            lines = [f"「{med.get('name')}」记在 {_where(rec)} 那次：", "",
                     f"- {_med_line(med)}"]
            if rec["id"] != last["id"]:
                lines += ["", f"（最近一次 {fmt_date(last['visit_at'])} 的记录里没有这个药。）"]
            lines.append("我只复述记录里的用法，不判断该不该吃、也不改剂量。")
            return {"answer": "\n".join(lines), "citations": [cite_of(rec)], "refused": False}
        if explicit and not target:
            return _no_match(q, recs)
        if target is None:
            target = last
        meds = target.get("meds") or []
        if not meds:
            return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                              f"{_where(target)} 这次没有记录用药。",
                    "citations": [], "refused": True}
        days = (datetime.now(UTC).astimezone().date() - target["visit_at"].date()).days + 1
        lines = [f"按 {_where(target)} 那份记录，今天是第 {days} 天："]
        lines.extend(f"- {_med_line(m)}" for m in meds)
        if skipped is not None:
            lines.append(f"（{_where(skipped)} 那次没有记录用药，上面是同一条件下最近一次有用药的记录。）")
        if re.search(r"几天|疗程", q):
            spans = [m.get("days") for m in meds if m.get("days")]
            if spans:
                lines.append(f"记录的疗程天数：{'、'.join(spans)} 天。")
        lines.append("我只复述记录里的用法，不判断该不该吃、也不改剂量。")
        return {"answer": "\n".join(lines), "citations": [cite_of(target)], "refused": False}

    # —— 10. 复诊 ——
    if FOLLOW_Q.search(q):
        target, matched, explicit = _select(q, recs)
        if explicit and not target:
            return _no_match(q, recs)
        if target is None:
            target = last
        if not target.get("follow_up"):
            if explicit:
                return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                                  f"{_where(target)} 那次没有记录复诊时间。",
                        "citations": [cite_of(target)], "refused": True}
            fallback = next((r for r in recs if r.get("follow_up")), None)
            if not fallback:
                return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                                  f"你的 {len(recs)} 份记录里都没有写复诊时间。",
                        "citations": [], "refused": True}
            return {"answer": f"最近一次（{fmt_date(last['visit_at'])}）没有写复诊时间。\n\n"
                              f"更早的 {_where(fallback)} 记的是：{fallback['follow_up']}。",
                    "citations": [cite_of(fallback)], "refused": False}
        lines = [f"记的是：{target['follow_up']}。", "", f"出自 {_where(target)} 那次。"]
        cites = [cite_of(target)]
        if not explicit:
            ups = [(r, r["follow_up"]) for r in recs
                   if r["id"] != target["id"] and r.get("follow_up")]
            if ups:
                lines += ["", "更早的记录里："]
                for rec, up in ups[:3]:
                    lines.append(f"- {fmt_date(rec['visit_at'])}：{up}")
                for rec, _ in ups[:2]:
                    c = cite_of(rec)
                    if c["record_id"] not in {x["record_id"] for x in cites}:
                        cites.append(c)
        return {"answer": "\n".join(lines), "citations": cites, "refused": False}

    # —— 11. 过敏（跨记录汇总）——
    if ALLERGY_Q.search(q):
        return _allergy_answer(recs)

    # —— 12. 注意事项 ——
    if NOTE_Q.search(q):
        target, matched, explicit = _select(q, recs)
        if explicit and not target:
            return _no_match(q, recs)
        if target is None:
            target = last
        if not target.get("notes"):
            if explicit:
                return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                                  f"{_where(target)} 那次没有记录注意事项。",
                        "citations": [cite_of(target)], "refused": True}
            fallback = next((r for r in recs if r.get("notes")), None)
            if not fallback:
                return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                                  f"你的 {len(recs)} 份记录里都没有写注意事项。",
                        "citations": [], "refused": True}
            return {"answer": f"最近一次（{fmt_date(last['visit_at'])}）没有记录注意事项。\n\n"
                              f"更早的 {_where(fallback)} 写的是：\n"
                              + "\n".join(f"- {n}" for n in fallback["notes"]),
                    "citations": [cite_of(fallback)], "refused": False}
        lines = [f"{_where(target)} 医生交代的是："]
        lines += [f"- {n}" for n in target["notes"]]
        cites = [cite_of(target)]
        if not explicit:
            seen = {str(n).strip() for n in target["notes"]}
            older = []
            for rec in recs:
                if rec["id"] == target["id"]:
                    continue
                for n in rec.get("notes") or []:
                    key = str(n).strip()
                    if key and key not in seen:
                        seen.add(key)
                        older.append((rec, key))
            if older:
                lines += ["", "更早的记录里还交代过："]
                for rec, n in older[:4]:
                    lines.append(f"- {n}（{fmt_date(rec['visit_at'])}）")
                for rec, _ in older[:2]:
                    c = cite_of(rec)
                    if c["record_id"] not in {x["record_id"] for x in cites}:
                        cites.append(c)
        return {"answer": "\n".join(lines), "citations": cites, "refused": False}

    # —— 12.5 医生说了什么：复述该次记录里的诊断、用药、复诊与注意事项 ——
    if DOCTOR_Q.search(q):
        target, matched, explicit = _select(q, recs)
        if explicit and not target:
            return _no_match(q, recs)
        if target is None:
            target = last
        lines = [f"按 {_where(target)} 那次记录：", ""]
        if target.get("summary"):
            lines.append(f"诊断：{target['summary']}。")
        meds = target.get("meds") or []
        if meds:
            lines.append("用药：" + "、".join(_med_line(m) for m in meds) + "。")
        if target.get("follow_up"):
            lines.append(f"复诊：{target['follow_up']}。")
        notes = target.get("notes") or []
        if notes:
            lines.append("注意事项：" + "；".join(notes) + "。")
        text = (target.get("transcript") or "").strip()
        if len(lines) <= 2:
            return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                              f"{_where(target)} 那次只存了转写，没有可复述的字段。",
                    "citations": [cite_of(target)], "refused": True}
        if text:
            lines += ["", f"原话节选：「{text[:140]}{'…' if len(text) > 140 else ''}」"]
        return {"answer": "\n".join(lines), "citations": [cite_of(target)], "refused": False}

    # —— 13. 次数 / 时间线 ——
    pool, explicit, label = _filter_pool(q, recs)
    if COUNT_Q.search(q):
        if explicit and not pool:
            return _no_match(q, recs)
        shown = pool if explicit else recs
        lines = [f"你的档案里{'符合「' + label + '」的' if explicit else ''}一共 {len(shown)} 份记录："]
        for i, rec in enumerate(shown, 1):
            lines.append(f"{i}. {fmt_date(rec['visit_at'])} · {rec.get('hospital') or '未记录医院'} · "
                         f"{rec.get('summary') or '未写明诊断'}")
        return {"answer": "\n".join(lines),
                "citations": [cite_of(r) for r in shown[:3]], "refused": False}

    if LAST_Q.search(q):
        if explicit and not pool:
            return _no_match(q, recs)
        target = pool[0] if explicit else last
        lines = [f"{'符合条件的那次' if explicit else '你最近一次'}是 {fmt_date(target['visit_at'])}"
                 f"（{WEEK[target['visit_at'].weekday()]}），"
                 f"{target.get('hospital') or '未记录医院'}。",
                 f"诊断：{target.get('summary') or '未写明'}。"]
        if not explicit and len(recs) > 1:
            older = "；".join(
                f"{fmt_date(r['visit_at'])} {r.get('hospital') or '未记录'}" for r in recs[1:4]
            )
            lines.append(f"往前还有 {len(recs) - 1} 次：{older}。")
        if explicit and len(pool) > 1:
            lines.append(f"（符合条件的有 {len(pool)} 次，上面是最近的一次。）")
        return {"answer": "\n".join(lines),
                "citations": [cite_of(target)], "refused": False}

    # —— 14. 条目匹配：只在没有显式日期/医院条件时兜底 ——
    if not explicit:
        for rec in recs:
            terms: list[str] = []
            terms.extend(rec.get("conditions") or [])
            if rec.get("summary"):
                terms.append(rec["summary"])
            terms.extend([m.get("name", "") for m in (rec.get("meds") or [])])
            if rec.get("hospital"):
                terms.append(rec["hospital"])
            terms.extend(rec.get("notes") or [])
            hit = [t for t in terms if t and len(t) >= 2 and t in q]
            if hit:
                return {
                    "answer": f"档案里有这一条，出自 {_where(rec)}：\n\n"
                              + "\n".join(f"- {t}" for t in dict.fromkeys(hit))
                              + f"\n\n诊断：{rec.get('summary') or '未写明'}。",
                    "citations": [cite_of(rec)],
                    "refused": False,
                }

        # —— 15. 兜底：关键词命中就给出处，命不中就说没有 ——
        keywords = [w for w in re.split(r"[\s，,。？?！!、]+", q) if len(w) >= 2]
        for rec in recs:
            blob = " ".join([
                rec.get("hospital", ""), rec.get("summary", ""), rec.get("transcript", ""),
                " ".join(rec.get("conditions") or []), " ".join(rec.get("notes") or []),
            ])
            if any(k in blob for k in keywords):
                return {
                    "answer": f"档案里提到这一点的是 {_where(rec)} 那次：\n\n"
                              f"诊断：{rec.get('summary') or '未写明'}。\n"
                              f"原文：「{rec.get('transcript', '')[:120]}…」",
                    "citations": [cite_of(rec)],
                    "refused": False,
                }

    if explicit:
        return _no_match(q, recs)

    return {"answer": "病历档案未查询到该内容，无法回答。\n\n"
                      "你的记录里没有提到这件事；具体该怎么办，建议复诊时问医生。",
            "citations": [], "refused": True}
