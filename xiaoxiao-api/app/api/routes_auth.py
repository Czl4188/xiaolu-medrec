from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import rate_limit
from app.core.config import settings
from app.core.security import hash_password, hash_token, new_session_token, verify_password
from app.db import repo
from app.db.session import get_db
from app.schemas import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

_auth_limit = rate_limit("auth", settings.auth_rate_limit_per_minute)


def _token_response(user, db: Session) -> TokenOut:
    token = new_session_token()
    repo.create_session(db, user.id, hash_token(token), settings.access_token_ttl_seconds)
    return TokenOut(
        access_token=token,
        expires_in=settings.access_token_ttl_seconds,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenOut, dependencies=[Depends(_auth_limit)])
def register(body: RegisterIn, db: Session = Depends(get_db)) -> TokenOut:
    if repo.get_user_by_username(db, body.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已被占用")
    try:
        user = repo.create_user(db, body.username, hash_password(body.password))
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已被占用") from None
    return _token_response(user, db)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(_auth_limit)])
def login(body: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = repo.get_user_by_username(db, body.username.strip())
    # 统一话术，避免通过错误信息枚举用户
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码不正确")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已停用")
    return _token_response(user, db)
