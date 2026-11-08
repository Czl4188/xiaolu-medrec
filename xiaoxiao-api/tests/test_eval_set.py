"""难例集本身与抽取效果的回归护栏。

难例集是「药品知识库有没有用」的唯一证据，它本身坏掉（少行、列名改了、金标准被改宽）
比抽取器回退更隐蔽，所以单独测。
"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "eval_drug_extract.py"
GOLD = ROOT / "scripts" / "eval_data" / "drug_hard_cases_30.csv"


def _load_eval():
    spec = importlib.util.spec_from_file_location("eval_drug_extract", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hard_cases_file_is_intact():
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8-sig")))
    assert len(rows) == 30, "难例集应为 30 条"
    assert {r["id"] for r in rows} == {f"D{i:02d}" for i in range(1, 31)}
    for row in rows:
        assert row["转写文本"].strip(), f"{row['id']} 缺转写文本"
        assert row["金标准药名"].strip(), f"{row['id']} 缺金标准药名"


def test_hard_cases_cover_each_type():
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8-sig")))
    kinds = {r["类型"] for r in rows}
    # 商品名与同音错字是词典主要收益来源，缺了这两类，实验就失去意义
    assert any("商品名" in k for k in kinds)
    assert any("同音" in k or "错字" in k for k in kinds)


@pytest.fixture(scope="module")
def scores():
    ev = _load_eval()
    return ev.run("after")["summary"]


def test_name_extraction_does_not_regress(scores):
    # 当前 0.862，护栏留出余量，只拦明显回退
    assert scores["f1"] >= 0.80, f"药名 F1 回退到 {scores['f1']:.3f}"
    assert scores["precision"] >= 0.90, f"药名 P 回退到 {scores['precision']:.3f}"


def test_fatal_errors_stay_low(scores):
    # 现状 2 处，都出在 D25「二甲双胍先别吃了」：否定词表里没有「别吃了」，
    # 这属于规则层遗留（词典救不了），修的时候把这条断言收紧到 0。
    assert scores["fatal"] <= 2, f"不可容忍错误升到 {scores['fatal']} 处"
    assert scores["fatal_stopped"] <= 1, "停用混入变多了"
