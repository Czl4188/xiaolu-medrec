# 小鹿 Agent 严格复审整改说明

对应文档：`_review.md`（《小鹿 Agent 严格复审与整改建议》，2026-11-02 22:35–22:50）
整改对象：`xiaoxiao-ai/app.html`（单文件前端）、`xiaoxiao-api/`（FastAPI 服务端）、`xiaoxiao-api/dist/`（发布包）
整改时间：2026-11-02 22:50–23:37
验证方式：pytest 回归、源码审查、桌面与 390px 移动端浏览器端到端操作（临时账号 + 虚构病历）

---

## 1. 总体结论

复审报告列出的 4 项 P0 已全部整改，P1 中的 7 项已整改、1 项部分整改、1 项按产品边界明确取舍；P2 中与「数据正确性」直接相关的 3 项已整改，纯体验类重排暂缓。

后端 pytest 由 23 条增加到 **34 条，全部通过**；新增 11 条覆盖 P0-02 / P0-03 / P1-03 / P1-04 / P1-08 以及「按医院问药跳过最近无药记录」的回归用例。

整改版本已重新发布到原链接并完成线上验收，详见第 5 节。

---

## 2. P0 逐项整改

| 编号 | 问题 | 状态 | 实现方式 |
| --- | --- | --- | --- |
| P0-01 | 抽取未完成即可归档，迟到响应/旧草稿覆盖 | 已整改 | 草稿增加 `extractState = extracting / ready / failed` 与 `extractSeq` 序号；保存按钮在 `extracting`、`saving` 时禁用并显示「正在整理…」；`formBelongs` + `editedFields` 记录用户手工改过的字段，`applyExtract` 逐字段合并，迟到响应不覆盖人工修改；抽取失败进入全手工校对，不自动套用本机旧规则；导出时若 `state.saving` 直接拦截 |
| P0-02 | 按日期/医院查询返回最近一次或另一家医院的药单 | 已整改 | `answer.py` 重写候选选择：解析为日期、医院、第几次三类结构化条件，`_filter_pool` 对条件**取交集**；无匹配走 `_no_match` 明确拒答，不再回退最近记录；医院支持核心名/别名匹配（`_hosp_core` / `_hosp_match`）；前端本机模式同步实现 `localDateConds` / `localHospitalConds` / `localHospMatch` / `localPick`，两种模式口径一致 |
| P0-03 | 最近一次无过敏记录掩盖既往过敏史 | 已整改 | 服务端 `_allergy_answer` 跨记录汇总：阳性优先、否认次之、全空输出「未记录，不代表没有过敏」；前端 `allergySummary` 同口径，「今日用药」改为「最近一次医嘱用药」并注明过敏史跨记录汇总；空白处显示「未记录（不等于没有）」 |
| P0-04 | 服务端不可用时本机降级仍用旧错误规则 | 已整改 | 服务端异常时问答改为「服务端暂时不可用，这条我没有作答」，不再用本机旧规则生成药物字段；归档路径抽取失败则强制全手工录入，提示「待手工填写」 |

---

## 3. P1 逐项整改

| 编号 | 问题 | 状态 | 实现方式 |
| --- | --- | --- | --- |
| P1-01 | 引用标签空白 | 已整改 | `buildCloudSources` 统一为 `{label}` 对象结构，展示就诊日期、医院、序号；`openRecord` 支持点击来源标签跳转到「我的」并展开对应记录 |
| P1-02 | 个人资料未真正同步 | 已整改 | 新增 `PUT /api/me/profile`；保存中禁用按钮并显示状态，成功提示「资料已同步到服务端」，失败明确提示「只存到本机，未同步」；新设备登录恢复显示名/年龄/性别/常去医院；头像、身份证按产品约定**仅存本机**，读服务端资料时不被覆盖 |
| P1-03 | 日期前缀污染医院名、就诊日期未抽取 | 已整改 | 抽取医院前先剥离日期、时段与口语前缀；新增 `COMMON_DRUGS` 之外的 `_parse_visit_date`，输出 `visit_hint`（`ExtractOut.visit_hint`），原文有明确日期时优先采用，年份不确定不臆断 |
| P1-04 | 不带剂型药名漏抽、无法增删药物 | 已整改 | 抽取器加入常见药名词典 `COMMON_DRUGS` + 药名锚点窗口，识别「阿莫西林 0.5g」这类写法；归档弹窗增加「＋ 添加药物」和每行删除按钮（`dlgAddMed` / `data-del-med`），漏抽时明确提示手工补齐 |
| P1-05 | 归档后不能纠错、已保存字段不可见 | 已整改 | `openEditDialog` / `saveEdit` 通过 `PATCH` 修改同一条记录，保留原始转写；详情页展示疾病/事件、停用药、否认过敏、注意事项，并区分原文与人工修改 |
| P1-06 | 「今日用药」与服药打卡名不副实 | 已整改（按报告第一方案取舍） | 明确产品边界为「只做档案」：改名「最近一次医嘱用药」，移除服药勾选控件，界面写明「只复述医嘱，不做服药打卡」 |
| P1-07 | 隐私与存储文案互相矛盾 | 已整改 | 全站按 `服务端模式 / 本机模式` 动态显示统一说明；删除「识别只在本机」「只存在本机浏览器」等与现状冲突的静态文案，保留「音频可能经浏览器厂商云端」的准确表述 |
| P1-08 | 录音授权偏主动、账号生命周期不完整 | 已整改（找回密码明确不支持） | 登录与注册分离（`gateNew`），退出后自动取消勾选；进入工作台不自动开麦，录音前提示「征得医生与在场其他人同意」；新增「账号与安全」：改密码（验旧密码，改完吊销其它设备会话、保留当前）、注销账号（勾选知情 + 输密码二次确认，删除记录/资料/会话/运行数据）、数据存放说明与保留期限。**忘记密码**因未绑定手机/邮箱无法自助找回，界面已如实说明并给出「导出档案后注销重建」的路径 |
| P1-09 | 生产调试信息与安全响应头 | 已整改（代码层） | `/api/ping` 只返回 `{status, app}`，去掉 env / pid / cwd / 密钥指纹 / 密钥文件状态；`main.py` 中间件统一加 `X-Frame-Options: DENY`、`Permissions-Policy`、`Content-Security-Policy`、`X-Content-Type-Options`、`Referrer-Policy`、`Cross-Origin-Opener-Policy`；`/api/*` 强制 `Cache-Control: no-store`；`ensure_secret_key` 在 lifespan 中固定签名密钥 |

---

## 4. P2 逐项整改

| 编号 | 问题 | 状态 | 说明 |
| --- | --- | --- | --- |
| P2-01 | 390px 归档弹窗五列过挤 | 已整改 | 窄屏药物编辑改为卡片式分区：药名独占一行，剂量/频次一行、服法/天数一行，删除按钮固定在右上角；390px 实测无横向溢出 |
| P2-02 | 手机工作台过长、首屏不是整理流程 | 已整改 | 窄屏下重新排序：录音与手动整理 → 本次记录 → 对话 → 用药与概览，首屏不再是聊天区 |
| P2-03 | 底部导航、字号、顶栏拥挤 | 已整改 | 新增底部四入口导航（44px 触控高度、安全区适配），顶部导航补「工作台」入口；窄屏辅助字号提升到 11.5px |
| P2-04 | 整理后把长原文灌入聊天框 | 已整改 | 去掉自动灌入，改为记录卡片上的「用这段问小鹿」显式按钮 |
| P2-05 | 保存与导出缺少统一任务状态 | 已整改 | 增加 `state.saving` 与顶栏持久状态（保存中… / 保存失败）；保存中禁用导出；服务端模式导出前先拉服务端最新列表，不只信内存 |
| P2-06 | 导入缺少校验、缺可读版导出 | 已整改（自定义起止日期未做） | 导入校验 JSON 结构、日期、转写、meds 字段类型、500 份上限、重复跳过与逐条结果；新增关键词搜索、按医院筛选、近 30/90/365 天筛选、匹配计数与空状态；新增「打印 / 存 PDF」可读版（时间线、诊断、疾病事件、停用药物、用药表、复诊、注意事项、原始转写、免责声明）。**未做**自定义起止日期与服务端 PDF |

---

## 5. 验证结果

**后端回归（本地）**

```
xiaoxiao-api/.venv/Scripts/python.exe -m pytest tests/ -q
34 passed, 1 warning in 3.56s
```

新增的 11 条用例：

- `test_query_by_date_returns_that_visit`
- `test_query_by_hospital_returns_that_visit`
- `test_query_by_date_and_hospital_intersection`
- `test_query_no_match_does_not_fall_back_to_latest`
- `test_allergy_summary_keeps_history_when_latest_is_blank`
- `test_extract_visit_hint_and_drug_without_suffix`
- `test_change_password_requires_old_password`
- `test_change_password_rotates_other_sessions`
- `test_delete_account_removes_records_and_login`
- `test_delete_account_does_not_touch_other_users`
- `test_med_query_skips_latest_visit_without_meds`

并修复了 fixture 的账号隔离（`fresh_headers`），避免新增用例之间互相污染记录池。

**前端端到端（浏览器，4 套脚本 54 项断言）**

- 主流程：注册、抽取延迟下保存按钮禁用、多药剂量、人工修改保留、归档、搜索命中/空状态/清空、打印内容、改密、注销、390px 布局。
- 竞态：连续两份草稿，第一份迟到响应不覆盖第二份。
- 本机模式：断网注册、本机抽取、落盘、搜索、打印、账号区文案与隐藏逻辑。
- 边界：非 JSON、空文件、混合坏数据、重复导入、保存 500、旧格式记录兼容。

结果全部通过，无 JS 异常。

**发布包一致性**

```
6654fa00c357fdcc8a9250e28ca7c3c7  xiaoxiao-ai/app.html
6654fa00c357fdcc8a9250e28ca7c3c7  xiaoxiao-api/web/app.html
6654fa00c357fdcc8a9250e28ca7c3c7  xiaoxiao-api/dist/web/app.html
```

`app/` 与 `dist/app/` 无差异；已清除发布包内测试探针 HTML 和本地测试数据文件 `dist/data/app.db`，保留固定的 `dist/secret.key`。本轮新增的后端改动（`schemas.py`、`db/repo.py`、`api/routes_me.py`、`agent/answer.py`）已同步进 `dist/app/`。

**线上状态（本轮已发布并验收）**

发布前后对比：线上原为 219747 bytes 的上一版，无 `mfilter` / `tabbar` / `acct__danger` / `printBtn`；重新覆盖后为 **240065 bytes**，上述模块分别命中 18 / 12 / 4 / 2 次，与本地 `dist` 一致。

- `/api/ping` 只返回 `{"status":"ok","app":"xiaolu-medrec-api"}`。
- 响应头含 CSP、`Permissions-Policy`、`Referrer-Policy`、`X-Content-Type-Options`、`X-Frame-Options: DENY`；`/api/*` 返回 `Cache-Control: no-store`。

**线上账号生命周期实测**

| 检查 | 结果 |
| --- | --- |
| 注册 / 建一条记录 | 200 / 201 |
| 改密（旧密码错） | 400「当前密码不正确」 |
| 改密（正确） | 200，返回 `sessions_revoked` |
| 旧密码登录 / 新密码登录 | 401 / 200 |
| 注销（密码错） | 400，记录仍在（1 条） |
| 注销（正确） | 200，`records=1, sessions=2` |
| 注销后原令牌 / 登录 | 401 / 401 |
| 同名重新注册 | 200，记录数 0 |

**线上浏览器验收**

搜索命中、线上 CSP 下「打印 / 存 PDF」可正常生成、账号与安全区可见、注销回到登录页、390px 底部导航可见且无横向溢出，无 JS 异常。

以下为上一轮线上验收记录，保留供对照。

- `app.html` 219747 bytes，与本地/发布包一致；`extractState` 17 次、`dlgAddMed`、`allergySummary`、`localDateConds`、`gateNew`、`visit_hint`、`未记录（不等于没有）`、`服务端暂时不可用` 均命中。
- `/api/ping` 只返回 `{"status":"ok","app":"xiaolu-medrec-api"}`，调试信息已清除。
- 响应头已含 CSP、`Permissions-Policy`、`Referrer-Policy`、`X-Content-Type-Options`、`X-Frame-Options: DENY`；`/api/*` 返回 `Cache-Control: no-store`。

**线上真实接口回归（临时账号 + 虚构病历）**

| 检查 | 结果 |
| --- | --- |
| 注册 / 登录 | 200，令牌走 `X-Xiaolu-Token` |
| 抽取 `10月1日去了市第一人民医院…阿莫西林 0.5g 每天两次，共7天` | 医院 = `市第一人民医院`（无「日去了」污染）；`visit_hint` = `2026-10-01`；不带剂型的「阿莫西林」正确抽到 0.5g / 每天两次 / 7 天 |
| 建两条档案 | 10-01 市第一人民医院 奥美拉唑（青霉素过敏）、11-01 市第二人民医院 布洛芬 |
| 「10月1日开了什么药？」 | 返回 10-01 市第一人民医院 奥美拉唑，引用同记录 |
| 「市第一人民医院开了什么药？」 | 同上，不再返回另一家医院 |
| 「10月1日在市第一人民医院开了什么药？」 | 同上，日期+医院交集生效 |
| 「2026年12月31日开了什么药？」 | 明确「没有找到符合…的记录」，不回退最近记录 |
| 「我有哪些过敏？」 | 跨记录汇总出青霉素过敏并标注 10-01 原始记录 |

---

## 6. 遗留事项与建议

按复审报告的四阶段顺序，第一、第二阶段已全部完成，第三阶段除「找回密码」外完成，第四阶段的主要体验项完成。剩余：

1. **找回密码**：需要先有手机或邮箱绑定；当前只能导出档案后注销重建，界面已如实说明（P1-08 剩余）。
2. **自定义起止日期筛选、服务端 PDF、记录分页**：现有搜索 + 30/90/365 天筛选 + 浏览器打印可覆盖日常使用（P2-06 剩余）。
3. **运维侧**：数据库加密、备份权限、日志脱敏方案，以及登录/注册/问答接口的限流与异常审计复核（P1-09 后半段）。这些项无法仅从外部测试确认。
4. **工程化**：单实例 SQLite 换 PostgreSQL、令牌从 localStorage 换 HttpOnly Cookie、多实例部署。

上述遗留完成前，仍建议将产品标注为**测试版**，不要引导用户依赖它管理真实用药。

---

## 7. 本轮改动文件清单

| 文件 | 改动 |
| --- | --- |
| `xiaoxiao-ai/app.html` | 抽取状态机、字段合并、引用定位、过敏汇总、资料同步、增删药物、归档后编辑、本机结构化查询、保存状态、导入导出校验、隐私文案；本轮新增账号与安全（改密/注销/数据说明）、档案搜索与筛选、打印/存 PDF、底部导航、移动端排序与卡片式药物表单 |
| `xiaoxiao-api/app/agent/extractor.py` | `COMMON_DRUGS`、`_parse_visit_date`、`visit_hint`、医院/日期清洗 |
| `xiaoxiao-api/app/agent/answer.py` | 结构化交集查询、无匹配拒答、过敏跨记录汇总；本轮新增 `_select_meds`（最近一次无药时向前找有药记录） |
| `xiaoxiao-api/app/schemas.py` | `ExtractOut.visit_hint`；本轮新增 `ChangePasswordIn`、`DeleteAccountIn`、`DeleteAccountOut` |
| `xiaoxiao-api/app/api/routes_me.py` | 本轮新增 `POST /api/me/password`、`DELETE /api/me`，并接入 auth 限流 |
| `xiaoxiao-api/app/db/repo.py` | 本轮新增 `update_password`、`delete_other_sessions`、`delete_user_account` |
| `xiaoxiao-api/app/api/routes_health.py` | `/api/ping` 去除调试信息 |
| `xiaoxiao-api/app/main.py` | 安全响应头、`no-store`、lifespan + `ensure_secret_key` |
| `xiaoxiao-api/app/api/deps.py`、`db/models.py` | 会话令牌与数据模型（前序上线修复，本轮沿用） |
| `xiaoxiao-api/tests/test_api.py` | 新增 11 条回归用例，`fresh_headers` 账号隔离 |
| `xiaoxiao-api/web/app.html`、`xiaoxiao-api/dist/web/app.html` | 同步为最新版 |
| `xiaoxiao-api/dist/app/` | 同步本轮后端改动 |
| `xiaoxiao-api/dist/` | 清理测试探针与本地数据库 |
| `小鹿Agent双视角测试评估.md` | 用户视角与开发者视角评估、五轮自检记录、上线建议 |

**部署信息**：`appId = <你的 appId>`，`localDir = xiaoxiao-api/dist`，复用链接 `https://xiaolu-medrec.app.workbuddy.host/?sharecode=<访问码>`，http-service（FastAPI，端口 8000）。本轮改动已覆盖发布并完成线上验收。
