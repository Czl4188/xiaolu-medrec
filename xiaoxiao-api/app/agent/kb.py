"""药品知识库：医保目录词典 + 商品名别名 + 说明书修订索引。

数据来源（全部随仓库提交，离线可用）：
  · drugs.csv          国家基本医疗保险、生育保险和工伤保险药品目录（2025 年），
                       2026-01-01 执行；含通用名、剂型、甲乙类、药品分类代码（即 ATC 前四级）。
  · drug_aliases.csv   常见商品名与旧称 → 通用名，人工整理，55 条。
  · label_updates.csv  药品说明书修订公告索引（药监局公告，经中国医药信息网转载），
                       2021 年至今 107 条，用于回答"这药的说明书最近改过没有"。

设计取舍：
  · 只做「归一与校验」，不生成任何用药建议。知识库越界就会变成医疗建议。
  · 匹配用最长优先的表查，不用 4000 项的大正则：中文药名长度 2–16，逐字符查表更快。
  · 查不到的词不猜，原样返回 None，由调用方退回旧规则。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAX_LEN = 16

DOSAGE_SUFFIX = (
    "缓释片", "控释片", "肠溶片", "咀嚼片", "分散片", "口崩片", "薄膜衣片", "片",
    "缓释胶囊", "肠溶胶囊", "胶囊", "颗粒", "口服溶液", "口服液", "注射液", "注射剂",
    "软膏", "乳膏", "滴眼液", "喷雾剂", "吸入剂", "气雾剂", "散", "丸", "凝胶", "栓", "贴",
    "口服制剂", "制剂", "药品",  # 说明书公告里常写「××制剂」「××等药品」
)

# 命中药物后，允许继续吞掉的剂型词（保持「奥美拉唑肠溶胶囊」这种原文粒度）
TAIL_FORMS = (
    "缓释控释剂型", "口服常释剂型", "口服液体剂", "干混悬剂", "混悬液", "缓释片", "控释片",
    "肠溶片", "咀嚼片", "分散片", "泡腾片", "口崩片", "薄膜衣片", "缓释胶囊", "肠溶胶囊",
    "片", "胶囊", "颗粒", "口服溶液", "口服液", "注射液", "注射剂", "软膏", "乳膏",
    "滴眼液", "喷雾剂", "鼻喷剂", "吸入剂", "气雾剂", "滴剂", "含片", "散", "丸", "凝胶", "栓", "贴",
)

# 这些词在目录里是剂型或包装描述，单拎出来会满屏误判
STOPWORDS = {"复方", "注射", "口服", "颗粒", "胶囊", "片剂", "溶液", "混悬", "乳膏", "凝胶"}


# 盐基前缀：目录只收通用名，但病历原话常写成「苯磺酸氨氯地平片」，补上这一层才能保住原文粒度
SALT_PREFIXES = (
    "苯磺酸", "硫酸氢", "盐酸", "硫酸", "琥珀酸", "马来酸", "富马酸", "酒石酸",
    "醋酸", "磷酸", "枸橼酸", "氢溴酸", "乳酸", "葡萄糖酸", "硝酸",
)
# 成盐离子：阿托伐他汀钙片、缬沙坦氢氯噻嗪片这类写法中间的单个字
SALT_IONS = set("钙钠钾镁锌铁铋铵锂")


@dataclass(frozen=True)
class Entry:
    generic: str          # 归一后的通用名（比对用）
    name: str             # 目录里的原始药名
    form: str = ""
    insurance_class: str = ""
    atc4: str = ""
    atc_class: str = ""

    @property
    def is_jia(self) -> bool:
        return self.insurance_class == "甲类"


@dataclass(frozen=True)
class Hit:
    _start: int
    _end: int
    key: str              # 词典里命中的那个写法（可能是商品名）
    surface: str          # 原文里的完整写法（含剂型后缀）
    entry: Entry

    # 与 re.Match 的接口对齐，调用方不用分支
    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def group(self, index: int = 1) -> str:
        return self.surface


def normalize(name: str) -> str:
    """去括号注释、去罗马数字后缀、去剂型后缀、去空格。"""
    s = re.sub(r"[（(].*?[)）]", "", str(name or ""))
    s = re.sub(r"[ⅠⅡⅢⅣ]+$", "", s).strip()
    s = re.sub(r"\s+", "", s)
    for suffix in DOSAGE_SUFFIX:
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            return s[: -len(suffix)]
    return s


@dataclass
class _Index:
    entries: dict[str, Entry]           # 归一通用名 → 条目
    surface: dict[str, Entry]           # 任意可匹配写法 → 条目
    aliases: set[str]                   # 商品名/旧称（命中后要换成通用名）
    labels: dict[str, list[dict]]       # 归一通用名 → 修订公告


@lru_cache(maxsize=1)
def _load() -> _Index:
    entries: dict[str, Entry] = {}
    surface: dict[str, Entry] = {}
    aliases: set[str] = set()
    labels: dict[str, list[dict]] = {}

    drugs_csv = DATA_DIR / "drugs.csv"
    if drugs_csv.exists():
        with open(drugs_csv, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                generic = normalize(name)
                if len(generic) < 2 or generic in STOPWORDS:
                    continue
                entry = entries.get(generic) or Entry(
                    generic=generic,
                    name=name,
                    form=row.get("dosage_form", ""),
                    insurance_class=row.get("insurance_class", ""),
                    atc4=row.get("atc4", ""),
                    atc_class=row.get("atc_class", ""),
                )
                entries.setdefault(generic, entry)
                # 原文写法、归一写法、去剂型写法都能命中
                keys = {name, generic}
                # 再加一层盐基前缀（苯磺酸氨氯地平、盐酸二甲双胍…）
                for prefix in SALT_PREFIXES:
                    if not name.startswith(prefix):
                        keys.add(prefix + name)
                for key in keys:
                    if 2 <= len(key) <= MAX_LEN:
                        surface.setdefault(key, entry)

    aliases_csv = DATA_DIR / "drug_aliases.csv"
    if aliases_csv.exists():
        with open(aliases_csv, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                alias = (row.get("alias") or "").strip()
                generic = normalize(row.get("generic", ""))
                entry = entries.get(generic)
                if alias and entry and len(alias) >= 2:
                    surface[alias] = entry  # 商品名优先指向通用名条目
                    aliases.add(alias)

    labels_csv = DATA_DIR / "label_updates.csv"
    if labels_csv.exists():
        with open(labels_csv, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                for drug in re.split(r"[；;]", row.get("drugs", "")):
                    key = normalize(drug)
                    if len(key) >= 2:
                        labels.setdefault(key, []).append(row)

    return _Index(entries=entries, surface=surface, aliases=aliases, labels=labels)


def _extend_tail(text: str, end: int) -> int:
    """把紧跟其后的剂型词并进来，保留「奥美拉唑肠溶胶囊」这种原文粒度。

    允许中间夹一个成盐离子（阿托伐他汀钙片、枸橼酸铋钾颗粒），但不吞别的字。
    """
    for form in TAIL_FORMS:
        if text.startswith(form, end):
            return end + len(form)
    if end < len(text) and text[end] in SALT_IONS:
        for form in TAIL_FORMS:
            if text.startswith(form, end + 1):
                return end + 1 + len(form)
    return end


def scan(text: str) -> list[Hit]:
    """最长优先扫描，返回不重叠的药名命中。查不到就当没有，不猜。"""
    index = _load().surface
    if not index:
        return []
    hits: list[Hit] = []
    i, n = 0, len(text)
    while i < n:
        matched: Entry | None = None
        end = i
        key = ""
        for length in range(min(MAX_LEN, n - i), 1, -1):
            entry = index.get(text[i:i + length])
            if entry is not None:
                matched, end, key = entry, i + length, text[i:i + length]
                break
        if matched is not None:
            tail = _extend_tail(text, end)
            hits.append(Hit(_start=i, _end=tail, key=key, surface=text[i:tail], entry=matched))
            i = tail
        else:
            i += 1
    return hits


def lookup(name: str) -> Entry | None:
    index = _load().surface
    key = str(name or "").strip()
    return index.get(key) or index.get(normalize(key))


def is_alias(surface: str) -> bool:
    """这个写法是不是商品名/旧称（是的话对外要换成通用名）。"""
    return surface in _load().aliases


def label_updates(name: str) -> list[dict]:
    """这味药的说明书修订公告（按日期倒序）。"""
    index = _load().labels
    return sorted(index.get(normalize(name), []), key=lambda r: r.get("notice_date", ""), reverse=True)


def stats() -> dict:
    index = _load()
    return {
        "drugs": len(index.entries),
        "surface_keys": len(index.surface),
        "labels": sum(len(v) for v in index.labels.values()),
    }
