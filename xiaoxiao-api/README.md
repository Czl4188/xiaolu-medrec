# 小鹿 · 病历记录 API

按《FastAPI Agent 生产级后端架构》实现的模块化单体：**FastAPI 无状态 API + 独立 Worker + 持久化任务队列**。
前端 `../xiaoxiao-ai/app.html` 目前仍是 localStorage 版，尚未切到这套 API（见文末「当前边界」）。

---

## 1. 快速开始（本地，SQLite，零外部依赖）

```bash
cd xiaoxiao-api
python -m venv .venv
.venv/Scripts/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
cp .env.example .env

.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
# 另开一个终端跑 Worker
.venv/Scripts/python -m app.worker.run
```

- 接口文档：http://127.0.0.1:8000/api/docs
- 健康检查：`/healthz`（存活）、`/readyz`（就绪，含数据库探测）
- 首次启动自动建表（仅非生产）；生产用 `alembic upgrade head`

跑测试：

```bash
.venv/Scripts/python -m pytest -q      # 14 passed
```

---

## 2. 目录

```
app/
  main.py               应用装配：中间件 → 路由 → 静态前端
  core/
    config.py           全部配置走环境变量
    security.py         pbkdf2 口令散列 + JWT(HS256)，只用标准库
    logging.py          结构化 JSON 日志，带 request_id / user_id / run_id
    ratelimit.py        Redis 优先，无 Redis 退化为进程内
  db/
    models.py           users / profiles / records / runs / run_steps / tool_calls / token_usage / jobs
    repo.py             数据访问；所有用户数据查询强制带 user_id
    session.py          连接池与 Session
  agent/
    extractor.py        转写 → 结构化字段（规则式，不编）
    answer.py           基于档案作答 + 拒答 + 临床实体守卫
    llm.py              模型适配：超时/退避重试/熔断/备用模型/用量落库
  api/
    routes_auth.py      POST /api/auth/register|login
    routes_me.py        GET/PUT /api/me, /api/me/profile
    routes_records.py   CRUD + POST /api/records/extract
    routes_chat.py      POST /api/chat（SSE） + /api/chat/sync
    routes_ops.py       /api/records/{id}/reindex、/api/runs/{id}、/api/asr
    routes_health.py    /healthz, /readyz
  worker/
    run.py              Worker 进程（行级抢占、失败退避、dead 状态）
    tasks.py            任务实现（可替换）
alembic/                迁移（已生成初始版本）
tests/test_api.py       14 条接口测试
```

---

## 3. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册，返回 JWT |
| POST | `/api/auth/login` | 登录（错误话术统一，避免枚举用户） |
| GET/PUT | `/api/me/profile` | 患者资料 |
| GET/POST | `/api/records` | 列表 / 新建（按 `client_id` 幂等） |
| GET/DELETE | `/api/records/{id}` | 单条（跨用户一律 404） |
| POST | `/api/records/extract` | 文本 → 结构化字段，不落库 |
| POST | `/api/chat` | SSE：`meta` → `delta`* → `done` |
| POST | `/api/chat/sync` | 同上的非流式版本 |
| POST | `/api/records/{id}/reindex` | 建 run + 投递队列，立即返回 `run_id` |
| GET | `/api/runs/{id}` | 轮询运行状态 |
| POST | `/api/asr` | 音频转写（未配置时返回 501，不假装成功） |

---

## 4. 架构文档逐条对照

| 文档要求 | 当前实现 | 说明 |
|---|---|---|
| API 无状态、可扩容 | ✅ JWT 不落会话表；限流/状态放 Redis 或数据库 | 两实例可直接起 |
| 长任务不挂 BackgroundTasks | ✅ `jobs` 表 + 独立 Worker 进程 | 进程重启任务不丢，失败指数退避，超次数进 `dead` |
| RabbitMQ + Celery | ⚠️ 用数据库队列替代 | 单端口沙箱跑不了 RabbitMQ；`repo.claim_job()` 是唯一替换点，PG 下已用 `FOR UPDATE SKIP LOCKED` |
| PostgreSQL + Alembic | ✅ 代码兼容 PG，已生成初始迁移 | 本地默认 SQLite 便于零配置启动 |
| pgvector / RAG | ❌ 未接 | `record.reindex` 只做分块与摘要，向量库是下一步 |
| Redis | ✅ 可选 | 不配则限流退化为进程内，`/readyz` 会显示 `memory` |
| 模型治理（超时/重试/熔断/降级/用量） | ✅ `agent/llm.py` | 每次调用（含失败）写 `token_usage`；无 Key 时退回规则文案，不报错也不假装 |
| 用户数据隔离 | ✅ 所有查询强制 `user_id`；测试覆盖读/删/列表 | 跨用户访问返回 404 而非 403，避免泄露存在性 |
| 结构化日志 + request_id | ✅ `/healthz` + `X-Request-ID` 透传 | |
| OpenTelemetry / Prometheus / Sentry | ❌ 未接 | 日志字段已按可采集的形状输出，接 SDK 即可 |
| S3 私有桶 | ❌ 未接 | 音频目前只做类型/大小校验；存储层留空 |
| CI/CD、预发布环境 | ❌ 未做 | 需要真实仓库与环境 |

---

## 5. 部署

单机/预发布：

```bash
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
docker compose up --build
docker compose exec api alembic upgrade head
```

compose 起 **2 个 API + 2 个 Worker + PostgreSQL + Redis**，对应文档 §7 的拓扑。
生产建议：把 postgres / redis 换成云托管（改 `DATABASE_URL`、`REDIS_URL`），API 与 Worker 分别扩缩容，
`/healthz` 作存活探针、`/readyz` 作就绪探针，滚动发布。

---

## 6. 前端已接入（服务端模式）

`../xiaoxiao-ai/app.html` 启动时会探测同源 `/healthz`：

- **探测到后端** → 顶栏显示「服务端模式」。登录/注册、资料、档案、抽取、作答全部走 `/api`，
  数据在 PostgreSQL/SQLite，换设备登录还在。
- **探测不到** → 显示「本机模式」，退回 localStorage；页面照常可用，只是数据只在这台机器。

跨域部署时用 `app.html?api=https://api.example.com` 指定后端地址。

前端对服务端的调用点：

| 时机 | 接口 |
|---|---|
| 登录 / 首次注册 | `POST /api/auth/login` → 401 时 `POST /api/auth/register` |
| 进入应用 | `GET /api/me/profile`、`GET /api/records` |
| 整理转写 | `POST /api/records/extract`（失败退回本机规则并提示） |
| 确认归档 | `POST /api/records`（带 `client_id` 幂等，回填服务端 id） |
| 删除记录 | `DELETE /api/records/{id}` |
| 提问 | `POST /api/chat/sync`（失败退回本机规则，并在气泡里注明） |

## 7. 当前边界（重要）

1. **AI 模型尚未启用。** 模型适配层、熔断、用量统计都已就绪，但需要你提供 **base URL + model 名**
   （模型网关需要与 `LLM_BASE_URL` 匹配）。配置后回答会由模型润色，
   事实与来源仍由 `agent/answer.py` 决定。
2. **WorkBuddy 的发布沙箱只有单端口，且不提供 PostgreSQL / Redis / RabbitMQ。**
   这套后端在那里只能以 SQLite 单实例模式运行；文档里的多实例 + 托管数据库拓扑需要自己的服务器或云环境。
3. **ASR 未接入。** 服务端没有离线语音识别，`ASR_PROVIDER=none` 时 `/api/asr` 明确返回 501；
   现在录音转写仍靠浏览器 Web Speech（Chrome 会把音频送到 Google，国内大概率不通）。
4. **向量检索未接入。** 当前作答是「按规则命中 + 结构化字段」，不是语义检索。
5. **`/api/chat` 的 SSE 是按句切片推送，不是 token 级流式。** 传输层是真的 SSE，
   但模型是一次性返回；换真流式只需替换 `agent/llm.py` 的调用方式，事件格式不用变。


---

## 8. 部署到 WorkBuddy 发布沙箱（实测记录）

发布的是 `dist/`（运行时子集），不是仓库根：

```bash
./scripts/build_dist.sh          # 生成 dist/ + secret.key（密钥随包，gitignore）
```

四个坑，改代码前先看：

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报 `--port: '--proxy-headers' 不是整数` | 平台不展开 `startCmd` 里的 `$PORT` | 直接写 `--port 8000`，与发布参数一致 |
| 前端总是「本机模式」 | 平台的 `/healthz` 被占成自己的探针 | 前端改探 `/api/ping` |
| 登录后立刻 401 | **网关改写 `Authorization` 头**（长度 50 → 386） | 令牌走 `X-Xiaolu-Token` |
| 令牌签发即失效 | 多进程/重启时签名密钥不一致 | 改为服务端会话（`sessions` 表） |

另外：`docker-compose.yml` 会让预检判定「需要 PostgreSQL/Redis」而拒绝发布，所以 `dist/` 里不放它。
