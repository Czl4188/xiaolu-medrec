# 病历档案 JSON 结构（schema_version 1.0）

> 一份就诊记录 = 一个 JSON 文件。缺字段一律 `null` / `[]` / `{}`，不得用猜测值填充。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | ✓ | 固定 `"1.0"`，升级时用于迁移 |
| `patient_id` | string | ✓ | `pt_` + 6 位十六进制，与目录名一致 |
| `visit_id` | string | ✓ | `v_<YYYYMMDD>_<NN>`，与文件名一致 |
| `visit_time` | string | ✓ | ISO 8601，带时区；只有日期时 `T00:00:00+08:00` |
| `time_precision` | string | ✓ | `datetime` / `date` / `unknown` |
| `hospital` | string\|null | ✓ | 转写中提到的医院全称 |
| `department` | string\|null | | 科室 |
| `doctor` | string\|null | | 医生姓氏或全名 |
| `chief_complaint` | string\|null | | 主诉原文（不改写） |
| `diagnosis` | string[] | ✓ | 诊断结论，按医生原话，可多条 |
| `allergy` | string[] | ✓ | 过敏史；"否认过敏"也要记，记 `["否认药物过敏"]` |
| `medicines` | object[] | ✓ | 药品明细，无用药时 `[]` |
| `follow_up` | object | ✓ | 复诊安排，无则各字段 `null` |
| `notes` | string[] | ✓ | 医嘱与注意事项原文，一条一句 |
| `source` | object | ✓ | 音频与转写溯源 |
| `unconfirmed` | string[] | ✓ | 未抽到 / 存疑字段说明 |
| `created_at` | string | ✓ | 归档时间 ISO 8601 |

## 2. `medicines[]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 药名（通用名 + 剂型，按转写原话） |
| `dose` | string\|null | 单次剂量，含单位，如 `20 mg`；原样保留 |
| `frequency` | string\|null | 频次，如 `每日 2 次` |
| `timing` | string\|null | 与进餐关系，如 `早、晚餐前 30 分钟` |
| `duration_days` | number\|null | 疗程天数；只说"两周"可换算 14 并写进 `evidence` |
| `route` | string\|null | 给药途径，如 `口服` |
| `note` | string\|null | 该药的特别交代（整片吞服、避光等） |
| `evidence` | string\|null | 转写原句 + 时间戳 |

**禁止**：由同类药推断剂量；把"一天三次"写成"每日 3 次，每次 1 片"（片数未说就不写）。

## 3. `follow_up`

```json
{ "date": "2026-09-05", "department": "消化内科门诊", "note": "评估服药耐受与症状变化", "evidence": "[01:31] 医生：9 月 5 号来门诊复一次" }
```

`date` 只写 `YYYY-MM-DD`（复诊通常无具体时刻）。相对时间（"两周后来"）→ 以 `visit_time` 为基准换算，并在 `note` 保留原话。

## 4. `source`

```json
{
  "audio_file": "2026-08-31-瑞金-消化内科.m4a",
  "audio_path": "用户上传原路径（不复制原件）",
  "asr_engine": "whisper-large-v3",
  "asr_params": { "language": "zh", "diarization": true },
  "transcript_path": "transcripts/v_20260831_01.md",
  "duration_sec": 760,
  "segments": 42
}
```

## 5. `patient.json`

```json
{
  "patient_id": "pt_7f3a2c",
  "display_name": "陈**",
  "sex": "女",
  "birth_year": 1979,
  "allergy": ["青霉素类（阿莫西林、氨苄西林）"],
  "chronic": ["慢性胃炎 2 年"],
  "notes": "过敏史为患者自述，未见书面记录",
  "created_at": "2026-11-02T00:40:00+08:00",
  "updated_at": "2026-11-02T00:41:00+08:00"
}
```

`display_name` 默认只留姓 + `**`；用户明确要求全名才存全名。

## 6. `index.json`

```json
{
  "schema_version": "1.0",
  "patient_id": "pt_7f3a2c",
  "count": 3,
  "visits": [
    {
      "visit_id": "v_20260831_01",
      "visit_time": "2026-08-31T10:20:00+08:00",
      "hospital": "上海瑞金医院",
      "department": "消化内科",
      "diagnosis": ["慢性胃炎伴糜烂", "幽门螺杆菌感染"],
      "medicine_names": ["艾司奥美拉唑镁肠溶片", "枸橼酸铋钾胶囊", "克拉霉素片", "甲硝唑片"],
      "follow_up_date": "2026-09-05",
      "file": "visits/v_20260831_01.json"
    }
  ]
}
```

排序：`visit_time` 倒序（最新在前）。追加时读-改-写，写前校验 `count` 与数组长度一致。

## 7. 命名与去重

- `visit_id` 序号：同日多次就诊用 `_01`、`_02`。
- 同一音频重复上传：先比对 `source.audio_file` + `duration_sec` + `visit_time`，命中则提示"这次就诊已归档（v_20260831_01），要覆盖还是新建一条？"，不静默重复写入。
- 覆盖时保留旧文件为 `v_20260831_01.bak.json`，并告知用户。

## 8. 迁移

`schema_version` 升级时：

1. 读旧 JSON，补齐新字段默认值；
2. 写回前备份为 `*.v1.json`；
3. 更新 `index.json` 的 `schema_version`；
4. 逐份回读校验后再删除备份。
