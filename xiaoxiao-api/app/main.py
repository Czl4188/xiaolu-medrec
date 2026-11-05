"""应用入口。

装配顺序有讲究：中间件（request_id / 计时 / 体积限制 / 安全头）→ 路由 →
静态前端。静态要最后挂，否则 "/" 会吞掉 /api。
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    routes_admin,
    routes_ai,
    routes_auth,
    routes_chat,
    routes_geo,
    routes_health,
    routes_me,
    routes_ops,
    routes_records,
)
from app.core.config import settings
from app.core.logging import new_request_id, request_id_ctx, run_id_ctx, setup_logging, user_id_ctx
from app.db.base import Base
from app.db.migrate import ensure_columns, ensure_indexes
from app.db.session import engine

logger = logging.getLogger("app.main")
ACCESS_LOG = logging.getLogger("app.access")


def ensure_secret_key() -> None:
    """没配 SECRET_KEY 时生成一个并落盘。

    否则每次重启令牌全部失效、所有用户被踢下线；而把密钥写进代码仓库更糟。
    落盘在 data/secret.key（与数据库同目录，权限 600）。
    """
    if not settings.secret_key.startswith("dev-only"):
        return

    import secrets

    # 顺序很重要：
    # 1) 随包发布的 secret.key —— 与 app 同目录，所有进程/实例读到同一份，
    #    不受工作目录影响（部署平台常常不在项目根目录启动）；
    # 2) ./data/secret.key —— 自建部署时首次启动生成。
    root = Path(__file__).resolve().parents[1]
    shipped = root / "secret.key"
    try:
        if shipped.exists():
            settings.secret_key = shipped.read_text(encoding="utf-8").strip()
            return

        data_key = Path("./data/secret.key")
        if data_key.exists():
            settings.secret_key = data_key.read_text(encoding="utf-8").strip()
            return

        data_key.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(48)
        data_key.write_text(value, encoding="utf-8")
        try:
            data_key.chmod(0o600)
        except Exception:
            pass
        settings.secret_key = value
        logger.warning("SECRET_KEY 未配置，已生成并保存到 data/secret.key")
    except Exception as exc:
        logger.warning("无法持久化 SECRET_KEY（%s），本次使用临时密钥，重启后需重新登录", exc)
        settings.secret_key = secrets.token_urlsafe(48)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("启动 %s env=%s", settings.app_name, settings.app_env)
    if settings.database_url.startswith("sqlite"):
        Path("./data").mkdir(parents=True, exist_ok=True)
    ensure_secret_key()
    init_db()
    ensure_columns()
    ensure_indexes()
    yield
    logger.info("服务退出，开始释放连接")
    engine.dispose()


def create_app() -> FastAPI:
    setup_logging("DEBUG" if settings.debug else "INFO")

    app = FastAPI(
        lifespan=lifespan,
        title="小鹿 · 病历记录 API",
        version="1.0.0",
        docs_url="/api/docs" if not settings.is_prod else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_prod else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "X-Xiaolu-Token", "Content-Type", "Idempotency-Key"],
        max_age=600,
    )

    @app.middleware("http")
    async def observability(request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_request_id()
        request_id_ctx.set(rid)
        user_id_ctx.set("-")
        run_id_ctx.set("-")

        # 请求体上限：先看声明长度，挡掉明显超限的
        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            limit = settings.max_upload_mb if request.url.path.endswith("/asr") else settings.max_request_mb
            if int(declared) > limit * 1024 * 1024:
                return JSONResponse({"detail": f"请求体超过 {limit}MB"}, status_code=413)

        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("未处理异常 path=%s", request.url.path)
            response = JSONResponse({"detail": "服务内部错误", "request_id": rid}, status_code=500)

        elapsed = int((time.monotonic() - started) * 1000)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "microphone=(self), camera=(self), geolocation=(self), payment=()"
        )
        # 单文件前端用内联脚本/样式；connect-src 放开 https 是为了允许用户自配模型网关
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "font-src 'self' data:; "
            "connect-src 'self' https:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        elif response.headers.get("content-type", "").startswith("text/html"):
            # 页面必须每次回源：登录方式改版后，旧缓存会让用户
            # 看到上一版表单（把手机号填进用户名栏），登录必然失败。
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        ACCESS_LOG.info(
            "%s %s", request.method, request.url.path,
            extra={"method": request.method, "path": request.url.path,
                   "status": response.status_code, "ms": elapsed},
        )
        return response

    for module in (routes_health, routes_auth, routes_me, routes_records, routes_chat, routes_ai, routes_geo, routes_admin, routes_ops):
        app.include_router(module.router)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):  # pragma: no cover
        logger.exception("兜底异常处理: %s", exc)
        return JSONResponse({"detail": "服务内部错误"}, status_code=500)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """同一端口顺带托管前端页面（可选）。

    单一端口是部署环境的硬限制；把静态页挂在同一个服务上，前端就能用
    相对路径 /api/... 调接口，不必再处理跨域。
    """
    root = Path(__file__).resolve().parents[1]      # 项目根（app/ 的上一层）
    candidates = [
        os.getenv("FRONTEND_DIR", ""),
        str(root / "web"),                           # 部署包形态：web/ 是前端快照
        str(Path(__file__).resolve().parents[2] / "xiaoxiao-ai"),
        str(Path.cwd() / "xiaoxiao-ai"),
        str(Path.cwd() / "web"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate, "app.html").exists():
            app.mount("/", StaticFiles(directory=candidate, html=True), name="frontend")
            logger.info("已挂载前端静态目录: %s", candidate)
            return
    logger.info("未找到前端目录，仅提供 API")


def init_db() -> None:
    """开发/预发布环境建表；生产走 Alembic。"""
    if settings.is_prod:
        logger.info("生产环境跳过自动建表，请执行 alembic upgrade head")
        return
    Base.metadata.create_all(bind=engine)
    logger.info("已确保表结构存在（dev）")


app = create_app()
