from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import rate_limit
from app.core.config import settings
from app.core.crypto import decrypt
from app.core.security import hash_password, hash_token, new_session_token, verify_password
from app.db import repo
from app.db.session import get_db
from app.schemas import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

_auth_limit = rate_limit("auth", settings.auth_rate_limit_per_minute)


def user_out(user) -> UserOut:
    """手机号只回给本人，库里存的是密文。"""
    phone = decrypt(user.phone_enc)
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        phone=phone,
        phone_bound=bool(user.phone_hash),
    )


def _token_response(user, db: Session) -> TokenOut:
    token = new_session_token()
    repo.create_session(db, user.id, hash_token(token), settings.access_token_ttl_seconds)
    return TokenOut(
        access_token=token,
        expires_in=settings.access_token_ttl_seconds,
        user=user_out(user),
    )


@router.post("/register", response_model=TokenOut, dependencies=[Depends(_auth_limit)])
def register(body: RegisterIn, db: Session = Depends(get_db)) -> TokenOut:
    # 用户名可重复，只校验手机号
    if repo.get_user_by_phone(db, body.phone):
        raise HTTPException(status.HTTP_409_CONFLICT, "这个手机号已经注册过了，直接登录就行")
    try:
        user = repo.create_user(db, body.username, hash_password(body.password), body.phone)
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "这个手机号已经注册过了，直接登录就行"
        ) from None
    return _token_response(user, db)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(_auth_limit)])
def login(body: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    if body.phone:
        user = repo.get_user_by_phone(db, body.phone)
        # 统一话术，避免通过错误信息枚举用户
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "手机号或密码不正确")
    else:
        # 老账号迁移通道：只有还没绑手机号的账号可以用用户名登录
        users = repo.get_legacy_users_by_username(db, body.username)
        if len(users) > 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "这个用户名对应多个老账号，请先用手机号登录并绑定",
            )
        user = users[0] if users else None
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码不正确")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已停用")
    return _token_response(user, db)
