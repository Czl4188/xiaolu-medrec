from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import current_user, rate_limit
from app.core.config import settings
from app.core.crypto import decrypt
from app.core.security import hash_password, hash_token, verify_password
from app.db import repo
from app.db.models import User
from app.db.session import get_db
from app.schemas import (
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


def _profile_out(profile) -> ProfileOut:
    """敏感字段只对本人解密返回；模型里存的是密文。"""
    return ProfileOut(
        name=profile.name,
        age=profile.age,
        gender=profile.gender,
        hospital=profile.hospital,
        phone=decrypt(profile.phone_enc),
        idcard=decrypt(profile.idcard_enc),
    )


@router.get("", response_model=UserOut)
def read_me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/profile", response_model=ProfileOut)
def read_profile(user: User = Depends(current_user), db: Session = Depends(get_db)) -> ProfileOut:
    profile = repo.get_profile(db, user.id) or repo.upsert_profile(db, user.id)
    return _profile_out(profile)


@router.put("/profile", response_model=ProfileOut)
def update_profile(
    body: ProfileIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProfileOut:
    profile = repo.upsert_profile(db, user.id, **body.model_dump())
    return _profile_out(profile)


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
