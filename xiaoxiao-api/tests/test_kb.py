"""药品知识库接入后的验收项。

覆盖四件事：
1. 商品名能换成通用名（波立维 → 氯吡格雷）；
2. 通用名保留原文剂型粒度（苯磺酸氨氯地平片 不被缩成 氨氯地平）；
3. 医保目录的甲乙类与 ATC 分类查得到；
4. 说明书修订公告按品种能查到。
"""

from app.agent import kb
from app.agent.extractor import extract


def test_alias_resolves_to_generic():
    data = extract("医生给我开了波立维 75mg，一天一次，说这个不能停。")
    names = [m["name"] for m in data["meds"]]
    assert names == ["氯吡格雷"], f"商品名没换成通用名：{names}"
    assert data["meds"][0]["dose"] == "75mg"


def test_generic_keeps_dosage_form():
    data = extract("开苯磺酸氨氯地平片5mg每天一次，阿托伐他汀钙片20mg每晚一次。")
    names = [m["name"] for m in data["meds"]]
    assert "苯磺酸氨氯地平片" in names
    assert "阿托伐他汀钙片" in names
    assert "氨氯地平" not in names, "通用名不该被缩成没有剂型的样子"


def test_insurance_class_and_atc():
    entry = kb.lookup("氯吡格雷")
    assert entry is not None
    assert entry.insurance_class in {"甲类", "乙类"}
    assert entry.atc4.startswith("B01"), f"ATC 分类不对：{entry.atc4}"
    assert kb.lookup("奥美拉唑").atc4.startswith("A02")


def test_label_updates_by_drug():
    updates = kb.label_updates("孟鲁司特")
    assert updates, "说明书修订公告没查到"
    assert updates[0]["notice_no"].endswith("号")
    assert updates[0]["notice_date"][:4].isdigit()


def test_allergen_is_not_a_taken_drug():
    data = extract("我青霉素过敏，这次开了阿莫西林胶囊 0.5g 一天三次。")
    names = [m["name"] for m in data["meds"]]
    assert all("青霉素" not in n for n in names), f"过敏原被当成在用药：{names}"
    assert all("青霉素" not in n for n in data["discontinued"]), f"过敏原被当成停药：{data['discontinued']}"
    assert data["allergy"].startswith("青霉素") or "过敏" in data["allergy"]


def test_unlisted_word_not_invented():
    data = extract("医生说吃点维生素就行，没开别的药。")
    names = [m["name"] for m in data["meds"]]
    assert "维生素" not in names, "泛称不该当成品名入库"
