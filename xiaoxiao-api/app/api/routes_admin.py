"""一次性数据迁移：把老账号资料里的手机号升级成登录账号。

改用手机号登录之前创建的账号，`users.phone_hash` 都是空的，登录不了。
这里按产品要求处理两类人：

1. 资料里填过合法手机号、且号码没被别人占用 → 绑定为登录手机号，之后直接用手机号登录；
2. 没有可用手机号 → 注销（删除账号与全部数据）。

删除不可逆，所以做了三层保护：

- 必须在服务端配置 `ADMIN_TOKEN` 且请求头带对，否则接口直接 404（等于不存在）；
- 默认 dry-run，只有 `confirm="DELETE"` 才真正删除；
- 删除前把账号、资料、记录打包成 JSON 备份落盘并随响应返回，备份保留密文字段，
  同一个 `secret.key` 下可以原样恢复。

跑完建议把 `ADMIN_TOKEN` 清空再发布一次，这个接口就消失了。
"""

import hmac
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt, normalize_phone, phone_digest
from app.db import repo
from app.db.models import Profile, Record, User
from app.db.session import get_db

logger = logging.getLogger("app.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])

BACKUP_DIR = Path("data")


def _require_admin(x_admin_token: str = Header(default="")) -> None:
    expected = (settings.admin_token or "").strip()
    if not expected:
        # 没配置 = 未启用，对外表现成不存在
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    if not hmac.compare_digest(x_admin_token.strip(), expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限")


def _phone_of(db: Session, user: User) -> tuple[str, str]:
    """返回 (手机号, 来源)。优先账号上已绑的，其次资料里填过的。"""
    direct = normalize_phone(decrypt(user.phone_enc))
    if direct:
        return direct, "user"
    profile = db.get(Profile, user.id)
    if profile is not None:
        legacy = normalize_phone(decrypt(profile.phone_enc))
        if legacy:
            return legacy, "profile"
    return "", ""


def _valid(phone: str) -> bool:
    return len(phone) == 11 and phone.startswith("1") and phone[1] in "3456789"


def _scan(db: Session) -> dict:
    users = list(db.scalars(select(User).order_by(User.created_at)))
    used = {u.phone_hash for u in users if u.phone_hash}

    to_bind: list[dict] = []
    conflicts: list[dict] = []
    to_delete: list[dict] = []

    for user in users:
        record_count = len(list(db.scalars(select(Record.id).where(Record.user_id == user.id))))

        if user.phone_hash:
            continue  # 已经是手机号账号，不动

        phone, source = _phone_of(db, user)
        row = {
            "id": user.id,
            "username": user.username,
            "phone": phone,
            "phone_masked": (phone[:3] + "****" + phone[-4:]) if phone else "",
            "source": source,
            "records": record_count,
        }

        if not _valid(phone):
            to_delete.append(row)
            continue

        digest = phone_digest(phone)
        if digest in used:
            # 号码已经被别的账号占用：这个人没有可用登录方式
            row["reason"] = "手机号已被其它账号占用"
            conflicts.append(row)
            to_delete.append(row)
            continue

        used.add(digest)
        to_bind.append(row)

    return {
        "total_users": len(users),
        "to_bind": to_bind,
        "to_delete": to_delete,
        "conflicts": conflicts,
        "bind_count": len(to_bind),
        "delete_count": len(to_delete),
    }


def _backup(db: Session, rows: list[dict]) -> dict:
    out = {"exported_at": datetime.now(UTC).isoformat(), "accounts": []}
    for row in rows:
        user = db.get(User, row["id"])
        if user is None:
            continue
        profile = db.get(Profile, user.id)
        records = list(db.scalars(select(Record).where(Record.user_id == user.id)))
        out["accounts"].append({
            "user": {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
            # 密文原样保留：同一个 secret.key 可以直接恢复
            "profile": {
                "name": profile.name if profile else "",
                "age": profile.age if profile else "",
                "gender": profile.gender if profile else "",
                "hospital": profile.hospital if profile else "",
                "phone_enc": profile.phone_enc if profile else "",
                "idcard_enc": profile.idcard_enc if profile else "",
            },
            "records": [
                {
                    "client_id": r.client_id,
                    "visit_at": r.visit_at.isoformat() if r.visit_at else None,
                    "hospital": r.hospital,
                    "summary": r.summary,
                    "transcript": r.transcript,
                    "meds": r.meds,
                    "follow_up": r.follow_up,
                    "notes": r.notes,
                    "conditions": r.conditions,
                    "discontinued": r.discontinued,
                    "allergy": r.allergy,
                    "allergy_denied": r.allergy_denied,
                    "mode": r.mode,
                }
                for r in records
            ],
        })
    return out


@router.get("/legacy-accounts", dependencies=[Depends(_require_admin)])
def preview(db: Session = Depends(get_db)) -> dict:
    """只读预演：谁会绑定手机号、谁会被删除。"""
    return _scan(db)


@router.post("/legacy-accounts", dependencies=[Depends(_require_admin)])
def apply(body: dict, db: Session = Depends(get_db)) -> dict:
    if str(body.get("confirm") or "") != "DELETE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "需要 confirm=DELETE 才会执行")

    report = _scan(db)
    backup = _backup(db, report["to_delete"])

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"legacy-backup-{stamp}.json"
    path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

    bound, deleted = [], []
    for row in report["to_bind"]:
        user = db.get(User, row["id"])
        if user is None:
            continue
        try:
            repo.set_user_phone(db, user, row["phone"])
            bound.append({"username": user.username, "phone": row["phone_masked"]})
        except Exception as exc:  # noqa: BLE001
            logger.warning("绑定手机号失败 %s: %s", user.username, exc)
            db.rollback()

    for row in report["to_delete"]:
        user = db.get(User, row["id"])
        if user is None:
            continue
        repo.delete_user_account(db, user.id)
        deleted.append({"username": row["username"], "records": row["records"]})

    logger.info("老账号迁移：绑定 %d，删除 %d", len(bound), len(deleted))
    return {
        "ok": True,
        "bound": bound,
        "deleted": deleted,
        "backup_file": str(path),
        "backup": backup,
    }
