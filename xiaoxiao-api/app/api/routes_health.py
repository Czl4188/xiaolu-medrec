import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import settings
from app.db.session import get_db

logger = logging.getLogger("app.health")
router = APIRouter(tags=["health"])


@router.get("/api/ping")
def ping() -> dict:
    """前端用来判断「有没有后端」的探针。

    只返回存活状态，不暴露路径、密钥指纹、进程号或配置——
    这些信息对攻击者有用，对用户没用。
    """
    return {"status": "ok", "app": settings.app_name}


@router.get("/healthz")
def healthz() -> dict:
    """存活探针：只证明进程还能响应，不碰数据库。"""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)) -> JSONResponse:
    """就绪探针：数据库连不上就不接流量。"""
    checks = {"database": "ok"}
    ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
        ok = False

    payload = {
        "status": "ok" if ok else "degraded",
        "checks": checks,
    }
    if not ok:
        return JSONResponse(payload, status_code=503)
    return JSONResponse(payload)
