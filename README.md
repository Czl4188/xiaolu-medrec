# 小鹿 · 个人病历档案（测试版）

把一次问诊的录音或文字整理成结构化病历，并且**只依据你自己的档案**回答问题。每条回答都带来源，查不到就说查不到。

> 这是测试版原型，仅用于病历记录与整理，**不构成诊疗建议，不能替代医生诊断**。请勿用它管理真实用药。

---

## 扫码使用

![扫码使用小鹿](assets/qr-xiaolu-card.png)

手机相机或微信扫一扫，登录后就能录音、整理和询问。

- 二维码指向线上入口，**链接固定不变**：每次发布新版本后，扫码看到的都是最新版，不用重新做码。
- 换域名时才需要重新生成（访问码通过环境变量传入，不写进仓库文本）：

  ```bash
  XIAOLU_URL="https://<域名>/app.html?sharecode=<访问码>" python scripts/make_qr.py
  ```

- 打印或贴纸用纯二维码：[assets/qr-xiaolu.png](assets/qr-xiaolu.png)。

---

## 微信小程序

`xiaolu-miniprogram/` 是原生微信小程序，复用同一套后端：手机号登录、按时间翻档案、只按档案回答。
用同一手机号密码登录，看到的就是网页版里的档案。

自己跑起来 / 上线前要做什么（配置 request 合法域名、生成小程序码、名称审核），见
[xiaolu-miniprogram/README.md](xiaolu-miniprogram/README.md)。

---

## 功能

- **录音转写**：浏览器语音识别边说边转写，停下后再用千问转写校对一遍；也可以直接粘贴文字
- **结构化抽取**：医院、就诊日期、诊断、疾病/事件、用药（药名/剂量/频次/服法/天数）、复诊、过敏、注意事项
- **归档前校对**：规则先抽、千问再校对药名与剂量（每个字段都要能在原文找到依据）；抽取未完成时不能保存；药可以增、删、改；失败转全手工，不猜字段
- **档案问答**：只查档案，按日期 / 医院 / 第几次取交集，无匹配明确拒答；急症直接给 120 提示
- **过敏汇总**：跨记录保留阳性过敏，新记录的空白不会覆盖旧记录
- **检索与导出**：关键词 / 医院 / 时间范围筛选，JSON 备份，打印或存 PDF
- **拍照识药**：服务端调用千问 VL 识别药盒/处方，字段可改后再入库
- **账号与安全**：手机号 + 密码登录（用户名可重名）、改密码、绑定/更换手机号、注销并删除全部数据；手机号与身份证加密存储、跨设备恢复
- **语音提问**：询问页麦克风直接说话，千问转写后填进输入框
- **放进日历**：录音结束后可把复诊与用药时间导出 `.ics`，到点提醒
- **四套主题**：浅白（默认）/ 暖纸 / 樱粉 / 暗夜，按账号记住

---

## 目录结构

```
.
├── xiaoxiao-ai/              # 前端（自包含单文件 app.html）
│   ├── app.html              # 主应用
│   ├── index.html            # 介绍页
│   ├── choose.html           # 主题选样台
│   ├── styles/ scripts/      # 介绍页与选样台的资源
│   └── README.md
├── xiaoxiao-api/             # 后端（FastAPI 模块化单体）
│   ├── app/
│   │   ├── api/              # 路由：auth / me / records / chat / ai / health / ops
│   │   ├── agent/            # 抽取、作答、模型适配
│   │   ├── core/             # 配置、加密、安全、限流、日志
│   │   ├── db/               # 模型、仓储、迁移
│   │   └── main.py
│   ├── tests/                # pytest
│   ├── alembic/              # 数据库迁移
│   ├── web/                  # 部署时托管的前端副本
│   ├── .env.example
│   └── README.md
├── assets/                   # 入口二维码（卡片 + 纯码）
├── scripts/make_qr.py        # 重新生成二维码
├── xiaolu-miniprogram/       # 微信小程序（原生，复用同一后端）
├── xiaoxiao-medrec/          # 可安装的 WorkBuddy Skill
├── ARCHITECTURE.md           # 架构：模块、数据流、接口、关键取舍
├── CONTRIBUTING.md           # 协作规则：分支、提交、测试、安全红线
├── 小鹿Agent严格复审整改说明.md
├── 小鹿Agent双视角测试评估.md
└── 小鹿Agent资料同步与模型接入.md
```

---

## 本地运行

### 1. 后端

```bash
cd xiaoxiao-api
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env

# 不接模型也能跑；要用识药/对话润色，填 LLM_API_KEY
python -m uvicorn app.main:app --reload --port 8000
```

首次启动会自动建表，并给旧库补新增字段。

### 2. 前端

两种方式：

- **服务端模式**：后端已把 `xiaoxiao-api/web/` 挂在根路径，直接访问 `http://127.0.0.1:8000/app.html`。
- **本机模式**：直接打开 `xiaoxiao-ai/app.html`，数据只存浏览器，不连后端。

修改 `xiaoxiao-ai/app.html` 后，如果要让后端托管，需要同步一份：

```bash
cp xiaoxiao-ai/app.html xiaoxiao-api/web/app.html
```

---

## 配置

所有配置走环境变量，模板见 `xiaoxiao-api/.env.example`。常用项：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 默认 `sqlite:///./data/app.db`；生产建议 PostgreSQL |
| `SECRET_KEY` | 会话与敏感字段加密的根密钥，生产必须固定且保密 |
| `LLM_PROVIDER` | `none` 或 `openai`（兼容 OpenAI 协议的网关） |
| `LLM_BASE_URL` | 例如 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_API_KEY` | 模型 Key，**只放服务端** |
| `LLM_MODEL` | 文本模型，默认 `qwen-plus` |
| `LLM_VISION_MODEL` | 识图模型，默认 `qwen3-vl-plus` |

> 仓库里不会出现任何真实 Key。`config.env`、`.env`、`*.key`、`data/` 都已在 `.gitignore` 中。

---

## 测试

```bash
cd xiaoxiao-api
pytest -q
```

当前 38 条用例，覆盖鉴权、跨账号隔离、抽取边界、日期/医院交集查询、过敏汇总、账号生命周期、资料同步与模型开关。

---

## 部署

后端是单端口 HTTP 服务：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

- 需要 Python 3.11+，依赖见 `requirements.txt`
- SQLite 适合单实例；多实例请换 PostgreSQL，并在前面加反向代理
- 生产环境请设置 `APP_ENV=prod`、固定的 `SECRET_KEY`，并把 `config.env` / `.env` 排除在版本库外
- 前端与后端同域时，`CORS_ORIGINS` 可以留空

---

## 安全与隐私

- 手机号、身份证号在数据库中是 Fernet 密文，接口只对本人返回明文
- 跨账号访问统一返回 404，不泄露资源是否存在
- 修改密码会吊销其它设备会话；注销账号需要再次输入密码，并删除记录、资料、会话与运行数据
- 响应头包含 CSP、`X-Frame-Options: DENY`、`Permissions-Policy`，`/api/*` 禁止缓存
- 模型 Key 只在服务端，前端通过 `/api/ai/*` 代理调用

---

## 一起开发

先看两份文档：

- [`ARCHITECTURE.md`](ARCHITECTURE.md)：系统由什么组成、数据怎么流、接口有哪些、为什么这么选
- [`CONTRIBUTING.md`](CONTRIBUTING.md)：分支与提交规范、测试要求、数据库变更、安全红线、PR 清单

最短路径：

1. 从 `main` 切功能分支：`git checkout -b feat/your-feature`
2. 提交前确认 `git status` 里没有 `.env`、`config.env`、`*.key`、`data/`
3. 开 Pull Request，写清改了什么、怎么验证
4. 后端改动补 `pytest`；前端改动至少在 390px 与桌面各走一遍主流程

### 已知待办

- 找回密码（需要先有手机号或邮箱验证通道）
- 自定义起止日期筛选、服务端 PDF、记录分页
- PostgreSQL 多实例、备份与审计
- 真实诊室噪声环境下的语音识别评估

---

## 免责声明

本项目仅用于个人病历记录与整理，不提供诊断、处方或用药建议。任何医疗决策请咨询执业医师；如遇急症，请立即拨打 120 或前往急诊。
