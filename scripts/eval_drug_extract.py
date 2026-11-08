"""药品抽取难例集评测：同一份 30 条文本，分别在「词典前」「词典后」跑一遍。

用途：给药品知识库的收益一个可复现的数字，而不是口头说「更准了」。

用法（仓库根目录）：

    python scripts/eval_drug_extract.py --no-kb --tag before
    python scripts/eval_drug_extract.py --tag after

    # 需要先装依赖、并用 3.10+ 的解释器
    python scripts/eval_drug_extract.py --no-kb --tag before --out eval_out

`--no-kb` 会把知识库的三个入口短路掉（scan / lookup / is_alias），
退回纯正则匹配，等价于引入 `app/agent/kb.py` 之前的行为。

产出（默认写到 eval_out/，不入库）：
    eval_out/drug_eval-<tag>.md    可读结果 + 逐条明细
    eval_out/drug_eval-<tag>.csv   逐条明细，便于自己再做统计

判定口径（写进答辩材料时照抄这一段）：
  · 药名匹配：两边都做归一（去盐基前缀、去剂型后缀），归一后完全相同才算命中。
  · TP / FP / FN 按「每味药」计，micro 汇总后算 P / R / F1。
  · 剂量准确率两个口径都要报：
      条件口径——分母只算「药名已命中」的药；
      端到端口径——分母是金标准里所有带剂量的药（漏抽的药，剂量也算没对上）。
    只报条件口径会虚高，对外只用端到端口径。
  · 不可容忍错误（沿用错误分级表）：
      1) 异药误抽——抽出一个金标准里没有的药（含把剂量词当成药名）；
      2) 剂量量级错——药名对、剂量差 10 倍及以上；
      3) 停用药混入——金标准里明确停用的药出现在在用药里。
  · 漏抽（FN）单列，算可容忍：用户在原话里能看出来。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "xiaoxiao-api"
sys.path.insert(0, str(API))

from app.agent import kb as drug_kb  # noqa: E402
from app.agent.extractor import extract  # noqa: E402

GOLD = Path(__file__).resolve().parent / "eval_data" / "drug_hard_cases_30.csv"

SALT_PREFIX = re.compile(
    r"^(?:苯磺酸|盐酸|硫酸|琥珀酸|酒石酸|马来酸|富马酸|氢溴酸|磷酸|醋酸|枸橼酸|乳酸|葡萄糖酸|"
    r"氯化钠|单硝酸|硝酸|复方)"
)
DOSAGE_FORM = re.compile(
    r"(?:缓释片|控释片|肠溶片|分散片|咀嚼片|泡腾片|口崩片|薄膜衣片|片|胶囊|颗粒|口服液|糖浆|"
    r"软膏|乳膏|滴眼液|喷雾剂|吸入剂|注射液|散|丸|凝胶|贴|混悬液|干混悬剂)$"
)
UNITS = {"克": "g", "g": "g", "毫克": "mg", "mg": "mg", "微克": "μg", "μg": "μg", "ug": "μg",
         "毫升": "ml", "ml": "ml", "片": "片", "粒": "粒", "单位": "单位", "iu": "iu"}
SCALE = {"μg": 0.001, "mg": 1.0, "g": 1000.0, "ml": 1.0}


def disable_kb() -> None:
    """把知识库短路，退回引入 kb.py 之前的纯正则行为。"""
    drug_kb.scan = lambda text: []          # type: ignore[assignment]
    drug_kb.lookup = lambda name: None      # type: ignore[assignment]
    drug_kb.is_alias = lambda key: False    # type: ignore[assignment]


def norm_name(text: str) -> str:
    s = re.sub(r"\s+", "", str(text or ""))
    s = SALT_PREFIX.sub("", s)
    s = DOSAGE_FORM.sub("", s)
    return s


def parse_dose(text: str) -> tuple[float, str] | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-zμ]+|[\u4e00-\u9fa5]+)?", str(text or ""))
    if not m:
        return None
    value = float(m.group(1))
    raw_unit = (m.group(2) or "").strip()
    unit = UNITS.get(raw_unit.lower(), UNITS.get(raw_unit, raw_unit))
    if unit not in SCALE:
        return None
    return value * SCALE[unit], "mg" if unit != "ml" else unit


def dose_equal(pred: str, gold: str) -> bool:
    p, g = parse_dose(pred), parse_dose(gold)
    if not p or not g:
        return False
    return abs(p[0] - g[0]) <= 1e-6


def dose_magnitude_wrong(pred: str, gold: str) -> bool:
    p, g = parse_dose(pred), parse_dose(gold)
    if not p or not g or g[0] == 0:
        return False
    return max(p[0], g[0]) / max(min(p[0], g[0]), 1e-9) >= 10


def split_cell(cell: str) -> list[str]:
    return [x.strip() for x in re.split(r"[；;]", str(cell or "")) if x.strip()]


def run(tag: str, gold_path: Path = GOLD) -> dict:
    rows = list(csv.DictReader(open(gold_path, encoding="utf-8-sig")))
    detail = []
    tp = fp = fn = 0
    dose_total = dose_hit = 0          # 条件口径：只算药名命中的
    dose_total_all = dose_hit_all = 0  # 端到端口径：分母是金标准里所有带剂量的药
    fatal_extra = fatal_scale = fatal_stopped = 0

    for row in rows:
        text = row["转写文本"]
        gold_names = split_cell(row["金标准药名"])
        gold_doses = split_cell(row["金标准剂量"])
        gold_stop = split_cell(row.get("停用药", ""))
        gold_map = {norm_name(n): (gold_doses[i] if i < len(gold_doses) else "")
                    for i, n in enumerate(gold_names)}
        stop_set = {norm_name(x) for x in gold_stop}

        out = extract(text)
        pred = out.get("meds") or []
        pred_names = [norm_name(m.get("name", "")) for m in pred]

        matched, unmatched_pred = set(), []
        for name in pred_names:
            if name in gold_map and name not in matched:
                matched.add(name)
            else:
                unmatched_pred.append(name)

        missing = [n for n in gold_map if n not in matched]
        tp += len(matched)
        fn += len(missing)
        fp += len(unmatched_pred)

        # 剂量：药名命中且有金标准剂量的，逐个比
        wrong_scale_here = 0
        dose_counted: set[str] = set()
        for idx, name in enumerate(pred_names):
            if name not in gold_map or not gold_map[name] or name in dose_counted:
                continue
            dose_counted.add(name)
            dose_total += 1
            dose_total_all += 1
            pred_dose = pred[idx].get("dose", "")
            if dose_equal(pred_dose, gold_map[name]):
                dose_hit += 1
                dose_hit_all += 1
            elif dose_magnitude_wrong(pred_dose, gold_map[name]):
                fatal_scale += 1
                wrong_scale_here += 1
        # 端到端口径：没抽到的药，它的剂量也算没对上
        for name, gold_dose in gold_map.items():
            if gold_dose and name not in dose_counted:
                dose_total_all += 1

        fatal_extra += len(unmatched_pred)
        stop_leak = [n for n in pred_names if n in stop_set]
        fatal_stopped += len(stop_leak)

        detail.append({
            "id": row["id"],
            "类型": row["类型"],
            "转写文本": text,
            "金标准": "；".join(f"{n} {gold_map[n]}".strip() for n in gold_map),
            "金标准停药": "；".join(gold_stop),
            "抽取结果": "；".join(
                f"{m.get('name', '')} {m.get('dose', '')}".strip() for m in pred
            ) or "（未抽出）",
            "抽取停药": "；".join(out.get("discontinued") or []),
            "命中": len(matched),
            "漏抽": "；".join(missing) or "",
            "误抽": "；".join(unmatched_pred) or "",
            "剂量对错": "—" if not any(gold_map[n] for n in matched) else (
                "对" if not wrong_scale_here else "量级错"
            ),
            "不可容忍": "；".join(
                ([f"异药:{x}" for x in unmatched_pred] +
                 [f"停用混入:{x}" for x in stop_leak] +
                 (["剂量量级错"] if wrong_scale_here else []))
            ) or "",
        })

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    summary = {
        "tag": tag,
        "samples": len(rows),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "dose_hit": dose_hit, "dose_total": dose_total,
        "dose_acc": dose_hit / dose_total if dose_total else 0.0,
        "dose_hit_all": dose_hit_all, "dose_total_all": dose_total_all,
        "dose_acc_all": dose_hit_all / dose_total_all if dose_total_all else 0.0,
        "fatal": fatal_extra + fatal_scale + fatal_stopped,
        "fatal_extra": fatal_extra, "fatal_scale": fatal_scale, "fatal_stopped": fatal_stopped,
    }
    return {"summary": summary, "detail": detail}


def write_out(result: dict, out_dir: Path) -> None:
    s = result["summary"]
    tag = s["tag"]
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / f"drug_eval-{tag}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(result["detail"][0].keys()))
        writer.writeheader()
        writer.writerows(result["detail"])

    md = [
        f"# 药品抽取难例集评测 · {tag}",
        "",
        f"- 样本：{s['samples']} 条（商品名 / 同音错字 / 长名剂型 / 停药 / 剂量位置 / 多药连说）",
        f"- 药名：TP {s['tp']}，FP {s['fp']}，FN {s['fn']}",
        f"- **P {s['precision']:.3f} / R {s['recall']:.3f} / F1 {s['f1']:.3f}**",
        f"- 剂量（条件口径）：{s['dose_hit']}/{s['dose_total']} = **{s['dose_acc']:.3f}**",
        f"- 剂量（端到端口径）：{s['dose_hit_all']}/{s['dose_total_all']} = **{s['dose_acc_all']:.3f}**",
        f"- **不可容忍错误：{s['fatal']} 处**"
        f"（异药误抽 {s['fatal_extra']}，剂量量级错 {s['fatal_scale']}，停用混入 {s['fatal_stopped']}）",
        "",
        "| 编号 | 类型 | 抽取结果 | 命中 | 漏抽 | 误抽 | 不可容忍 |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in result["detail"]:
        md.append(
            f"| {d['id']} | {d['类型']} | {d['抽取结果']} | {d['命中']} | "
            f"{d['漏抽'] or '—'} | {d['误抽'] or '—'} | {d['不可容忍'] or '—'} |"
        )
    md_path = out_dir / f"drug_eval-{tag}.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"[{tag}] P={s['precision']:.3f} R={s['recall']:.3f} F1={s['f1']:.3f} "
          f"剂量={s['dose_hit']}/{s['dose_total']}(条件) "
          f"{s['dose_hit_all']}/{s['dose_total_all']}(端到端) 不可容忍={s['fatal']}")
    print(f"  → {md_path} / {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="药品抽取难例集评测")
    parser.add_argument("--tag", default="after", help="写进产物文件名，如 before / after")
    parser.add_argument("--no-kb", action="store_true", help="短路知识库，复现加词典之前的效果")
    parser.add_argument("--out", default="eval_out", help="产物目录，默认 eval_out/")
    args = parser.parse_args()

    if args.no_kb:
        disable_kb()
    write_out(run(args.tag), Path(args.out))


if __name__ == "__main__":
    main()
