from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, rate_limit
from app.core.config import settings
from app.core.crypto import decrypt
from app.core.security import hash_password, hash_token, verify_password
from app.db import repo
from app.db.models import User
from app.db.session import get_db
from app.schemas import (
    BindPhoneIn,
    ChangePasswordIn,
    DeleteAccountIn,
    DeleteAccountOut,
    ProfileIn,
    ProfileOut,
    UserOut,
)

router = APIRouter(prefix="/api/me", tags=["me"])

# 改密码和注销都要验旧密码，和登录共用同一额度，防止在线暴力试密码
_auth_limit = rate_limit("auth", settings.auth_rate_limit_per_minute)


def _raw_token(x_xiaolu_token: str, authorization: str) -> str:
    token = (x_xiaolu_token or "").strip()
    if not token and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    return token


def _profile_out(profile, user) -> ProfileOut:
    """敏感字段只对本人解密返回；模型里存的是密文。

    手机号以账号（users）为准：它是登录身份。老账号没绑手机号时，
    回落到 profile 里早先填过的号码，避免资料页突然变空。
    """
    return ProfileOut(
        name=profile.name,
        age=profile.age,
        gender=profile.gender,
        hospital=profile.hospital,
        phone=decrypt(user.phone_enc) or decrypt(profile.phone_enc),
        idcard=decrypt(profile.idcard_enc),
    )


@router.get("", response_model=UserOut)
def read_me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        phone=decrypt(user.phone_enc),
        phone_bound=bool(user.phone_hash),
    )


@router.get("/profile", response_model=ProfileOut)
def read_profile(user: User = Depends(current_user), db: Session = Depends(get_db)) -> ProfileOut:
    profile = repo.get_profile(db, user.id) or repo.upsert_profile(db, user.id)
    return _profile_out(profile, user)


@router.put("/profile", response_model=ProfileOut)
def update_profile(
    body: ProfileIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProfileOut:
    data = body.model_dump()
    # 手机号是登录账号，只能走 /api/me/phone（要验密码、要查重）
    data.pop("phone", None)
    profile = repo.upsert_profile(db, user.id, **data)
    return _profile_out(profile, user)


@router.post("/phone", dependencies=[Depends(_auth_limit)])
def bind_phone(
    body: BindPhoneIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """给老账号绑定手机号，或更换登录手机号。

    要验密码：换掉手机号等于换掉登录入口，不能让一个被盗的
    长期会话直接改走账号。
    """
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码不正确，手机号未修改")

    existing = repo.get_user_by_phone(db, body.phone)
    if existing and existing.id != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个手机号已经绑定过别的账号")
    if decrypt(user.phone_enc) == body.phone:
        return {"ok": True, "phone": body.phone, "changed": False}

    try:
        repo.set_user_phone(db, user, body.phone)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "这个手机号已经绑定过别的账号") from None
    return {"ok": True, "phone": body.phone, "changed": True}



@router.post("/password", dependencies=[Depends(_auth_limit)])
def change_password(
    body: ChangePasswordIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    x_xiaolu_token: str = Header(default=""),
    authorization: str = Header(default=""),
) -> dict:
    """改密码。必须验旧密码，改完让其它设备下线。"""
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前密码不正确")
    if body.old_password == body.new_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "新密码不能与当前密码相同")

    repo.update_password(db, user.id, hash_password(body.new_password))

    revoked = 0
    token = _raw_token(x_xiaolu_token, authorization)
    if token:
        revoked = repo.delete_other_sessions(db, user.id, hash_token(token))
    return {"ok": True, "sessions_revoked": revoked}


@router.delete("", response_model=DeleteAccountOut, dependencies=[Depends(_auth_limit)])
def delete_account(
    body: DeleteAccountIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DeleteAccountOut:
    """注销账号：删除该账号的全部服务端数据，不可恢复。"""
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码不正确，未删除任何数据")

    counts = repo.delete_user_account(db, user.id)
    return DeleteAccountOut(
        ok=True,
        records=counts.get("records", 0),
        sessions=counts.get("sessions", 0),
        runs=counts.get("runs", 0),
    )
