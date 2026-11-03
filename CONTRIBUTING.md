# 协作规则

这份规则是为了让几个人同时改这个项目时，不互相踩脚、不把密钥和病历数据带出去。请先读一遍再动手。

---

## 1. 开始之前

```bash
git clone <repo>
cd <repo>
```

后端环境：

```bash
cd xiaoxiao-api
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # 然后填自己的配置
pytest -q                     # 应该是 38 passed
```

**不要**把 `.env`、`config.env`、`secret.key`、`data/` 提交上来。`.gitignore` 已经挡了，但提交前仍要自己看一眼。

---

## 2. 分支

- `main` 保持可运行，不直接推。
- 一个功能一个分支：`feat/档案搜索`、`fix/抽取竞态`。
- 修紧急线上问题：`hotfix/xxx`，合完即删。

```bash
git checkout main
git pull
git checkout -b feat/your-feature
```

---

## 3. 提交信息

格式：`类型: 一句话说明`

| 类型 | 用途 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 修 bug |
| `refactor` | 重构，不改行为 |
| `docs` | 文档 |
| `test` | 测试 |
| `chore` | 构建、依赖、配置 |

例子：

```
fix: 按医院问药时不再因最近一次没开药而拒答
feat: 我的页支持按医院和时间范围筛选
docs: 补充架构说明与协作规则
```

一次提交只做一件事。不要把格式化、重命名和功能混在一起。

---

## 4. 代码规范

### Python

- 遵循现有分层：路由只做校验与编排，SQL 放 `db/repo.py`，业务判断放 `agent/`
- 所有用户数据查询必须带 `user_id`
- 新增接口要写 Pydantic schema，不要直接收 `dict`
- 时间统一用带时区的 `datetime`，不要用 `datetime.utcnow()`
- 注释写「为什么」，不写「这行在干嘛」

### JavaScript / CSS

- 前端是单文件，改之前先搜索有没有现成函数，别重复造
- 统一用 `var` / `function` 风格（与现有代码一致），不要混 ES module
- 用户输入渲染进 DOM 必须经过 `esc()`
- 新增文案要同时考虑桌面和 390px 窄屏

### 通用

- 不要提交 `console.log`、调试用的 `debugger`
- 不要引入新的前端构建链（Vite/Webpack），当前刻意保持零构建
- 新增依赖要在 PR 里说明理由和体积影响

---

## 5. 测试要求

| 改动 | 最少要做的验证 |
| --- | --- |
| 后端逻辑 | 补 `pytest`，跑全量 `pytest -q` |
| 抽取/作答 | 至少加一条覆盖边界的用例（多药、否定、日期、医院） |
| 前端交互 | 桌面 + 390px 各走一遍主流程，确认无 JS 报错 |
| 数据库结构 | 本地用旧库启动一次，确认 `ensure_columns()` 能补列 |

提 PR 时把「怎么验证的」写清楚，附命令或截图。

---

## 6. 数据库变更

- 新表：改 `app/db/models.py`，同时补 Alembic 迁移
- 已有表加列：除了模型，还要在 `app/db/migrate.py` 的 `_COLUMNS` 里登记，否则线上旧库不会自动补列
- 不要写破坏性迁移（删列/改类型）而不提供回滚说明
- 敏感字段必须加密后再入库，参考 `app/core/crypto.py`

---

## 7. 前端同步（容易忘）

`xiaoxiao-ai/app.html` 是源文件，`xiaoxiao-api/web/app.html` 是后端托管用的副本。

改完源文件后：

```bash
cp xiaoxiao-ai/app.html xiaoxiao-api/web/app.html
```

两边不一致时，以 `xiaoxiao-ai/app.html` 为准。

---

## 8. 安全红线

以下任何一条都不要做：

1. 把 API Key、`secret.key`、`config.env`、`.env` 提交到仓库或贴到聊天里
2. 把真实患者姓名、身份证、病历写进测试用例、截图或 issue
3. 在日志里打印完整手机号、身份证、令牌、请求体
4. 关闭鉴权、放宽 CORS 到 `*`、去掉 `user_id` 过滤来「先跑通」
5. 把模型 Key 写进前端或让浏览器直连模型
6. 用 `eval`、`innerHTML` 直接拼用户输入

测试数据一律用虚构信息，账号用临时注册。

建议开启仓库自带的提交检查：

```bash
git config core.hooksPath .githooks
```

它会在 `git commit` 时拦截 `.env`、`config.env`、`*.key`、`*.db`、`data/`、`dist/`，以及内容里疑似 `sk-` 开头的长串。

---

## 9. PR 检查清单

提交 PR 前逐条确认：

- [ ] 分支从最新 `main` 切出
- [ ] `git status` 里没有 `.env` / `config.env` / `*.key` / `data/`
- [ ] `pytest -q` 全绿
- [ ] 新增/修改的接口有 schema 和错误处理
- [ ] 前端在桌面和 390px 都试过
- [ ] 改了 `app.html` 的话，`web/app.html` 已同步
- [ ] 数据库变更已登记到 `migrate.py` 或 Alembic
- [ ] 文档（README / ARCHITECTURE）有同步更新
- [ ] PR 描述写清：改了什么、为什么、怎么验证、有什么风险

评审人重点看：数据正确性、越权、密钥、迁移兼容、移动端。

---

## 10. 发布

- 发布目录是 `xiaoxiao-api/dist/`，不是源码目录
- 不要把 `docker-compose.yml` 放进 `dist`（会触发外部服务预检）
- 发布前确认 `dist/web/app.html` 与源文件一致
- 发布后至少验证：注册登录、抽取一条、问答一条、`/api/ping` 不泄露调试信息

---

## 11. 沟通

- 需求变更先开 issue，说清背景和验收标准
- 涉及数据模型、接口契约、安全策略的改动，先在 issue 里对齐再写代码
- 不确定的地方早点问，不要闷头改一大片再推翻
