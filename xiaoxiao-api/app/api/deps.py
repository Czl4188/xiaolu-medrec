from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import settings
from app.core.logging import user_id_ctx
from app.core.security import hash_token
from app.db import repo
from app.db.models import User
from app.db.session import get_db


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def current_user(
    authorization: str = Header(default=""),
    x_xiaolu_token: str = Header(default=""),
    db: Session = Depends(get_db),
) -> User:
    """令牌优先从 X-Xiaolu-Token 读。

    为什么不用 Authorization：部分部署网关会把它替换成自己的令牌
    （实测请求头被改写成长度数百的串），业务令牌因此丢失。
    自定义头不会被改写；Authorization 保留作为本地开发的兼容入口。
    """
    token = (x_xiaolu_token or "").strip()
    if not token and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少访问令牌")
    user = repo.get_session_user(db, hash_token(token))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "令牌无效或已过期")
    if not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    user_id_ctx.set(user.id)
    return user


def rate_limit(scope: str, limit: int | None = None):
    """按 IP + 作用域限流；登录/注册用更严的额度。"""
    def _dep(request: Request) -> None:
        quota = limit or settings.rate_limit_per_minute
        allowed, count = ratelimit.hit(f"{scope}:{client_ip(request)}", quota)
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"请求过于频繁（{count}/{quota} 每分钟），请稍后再试",
                headers={"Retry-After": "60"},
            )
    return _dep
