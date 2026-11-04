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

另外提供两个配套动作，同样受 ADMIN_TOKEN 保护：

- `GET  /api/admin/accounts`        当前账号清单（手机号打码、记录数），用来定「留谁删谁」；
- `POST /api/admin/accounts/purge`  按 id 白名单清理，先备份再删；
- `POST /api/admin/accounts/restore` 从备份 JSON 恢复账号（密码需重设，原散列无法还原）；
- `POST /api/admin/accounts/import-records` 把备份账号里的档案并入一个现有账号
  （适合「新号已经能登录、老号只有档案」的情况，不必让用户换密码）；
- `POST /api/admin/accounts/merge` 两个现有账号合并：档案搬过去、资料空字段补齐，
  可选删除来源账号（先备份）；
- `POST /api/admin/accounts/credentials` 改手机号与密码（合并后把登录方式对齐用）。

跑完建议把 `ADMIN_TOKEN` 清空再发布一次，这些接口就消失了。
"""

import hmac
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt, normalize_phone, phone_digest
from app.db import repo
from app.db.models import Profile, Record, User
from app.db.models import Session as AuthSession
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


# ————————————————— 账号清单 / 清理 / 恢复 —————————————————
def _mask(phone: str) -> str:
    return (phone[:3] + "****" + phone[-4:]) if len(phone) == 11 else ""


@router.get("/accounts", dependencies=[Depends(_require_admin)])
def list_accounts(db: Session = Depends(get_db)) -> dict:
    rows = []
    for user in db.scalars(select(User).order_by(User.created_at)):
        records = len(list(db.scalars(select(Record.id).where(Record.user_id == user.id))))
        rows.append({
            "id": user.id,
            "username": user.username,
            "phone": _mask(decrypt(user.phone_enc)),
            "phone_bound": bool(user.phone_hash),
            "records": records,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        })
    return {"total": len(rows), "accounts": rows}


@router.post("/accounts/purge", dependencies=[Depends(_require_admin)])
def purge_accounts(body: dict, db: Session = Depends(get_db)) -> dict:
    """按 id 白名单清理：不在 keep_ids 里的账号一律注销。"""
    if str(body.get("confirm") or "") != "DELETE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "需要 confirm=DELETE 才会执行")

    keep = {str(x) for x in (body.get("keep_ids") or [])}
    if not keep:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "keep_ids 不能为空，避免误删全部")

    users = list(db.scalars(select(User)))
    victims = [u for u in users if u.id not in keep]

    backup = _backup(db, [{"id": u.id, "username": u.username, "records": 0} for u in victims])
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"purge-backup-{stamp}.json"
    path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

    deleted = []
    for user in victims:
        name, count = user.username, len(list(db.scalars(
            select(Record.id).where(Record.user_id == user.id))))
        repo.delete_user_account(db, user.id)
        deleted.append({"username": name, "records": count})

    return {"ok": True, "deleted": deleted, "backup_file": str(path), "backup": backup}


@router.post("/accounts/restore", dependencies=[Depends(_require_admin)])
def restore_account(body: dict, db: Session = Depends(get_db)) -> dict:
    """从备份里的一条账号恢复到库里。

    原来的 password_hash 没有备份（也不该备份），所以必须指定新密码；
    手机号若仍被别人占用会直接报错，需要先清理占用方。
    """
    account = body.get("account") or {}
    password = str(body.get("password") or "")
    if not account.get("user") or len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少账号数据，或密码少于 8 位")

    from app.core.crypto import encrypt
    from app.core.security import hash_password
    from app.db.models import Profile as ProfileModel, Record as RecordModel

    info = account["user"]
    if db.get(User, info["id"]) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该账号已存在，未重复恢复")

    prof = account.get("profile") or {}
    phone = normalize_phone(decrypt(prof.get("phone_enc") or ""))
    if phone and repo.get_user_by_phone(db, phone):
        raise HTTPException(status.HTTP_409_CONFLICT, "该手机号已被其它账号占用，请先清理")

    created = datetime.fromisoformat(info["created_at"]) if info.get("created_at") else datetime.now(UTC)
    user = User(
        id=info["id"],
        username=info["username"],
        display_name=info.get("display_name") or info["username"],
        password_hash=hash_password(password),
        phone_hash=phone_digest(phone) or None,
        phone_enc=encrypt(phone),
        created_at=created,
    )
    db.add(user)
    db.add(ProfileModel(
        user_id=user.id,
        name=prof.get("name") or info["username"],
        age=prof.get("age") or "",
        gender=prof.get("gender") or "",
        hospital=prof.get("hospital") or "",
        phone_enc=prof.get("phone_enc") or "",
        idcard_enc=prof.get("idcard_enc") or "",
    ))
    for rec in account.get("records") or []:
        visit = rec.get("visit_at")
        db.add(RecordModel(
            user_id=user.id,
            client_id=rec.get("client_id") or "",
            visit_at=datetime.fromisoformat(visit) if visit else datetime.now(UTC),
            hospital=rec.get("hospital") or "",
            summary=rec.get("summary") or "",
            transcript=rec.get("transcript") or "",
            meds=rec.get("meds") or [],
            follow_up=rec.get("follow_up") or "",
            notes=rec.get("notes") or [],
            conditions=rec.get("conditions") or [],
            discontinued=rec.get("discontinued") or [],
            allergy=rec.get("allergy") or "",
            allergy_denied=bool(rec.get("allergy_denied")),
            mode=rec.get("mode") or "manual",
        ))
    db.commit()
    return {"ok": True, "username": user.username, "phone": _mask(phone),
            "records": len(account.get("records") or []), "password_reset": True}


@router.post("/accounts/import-records", dependencies=[Depends(_require_admin)])
def import_records(body: dict, db: Session = Depends(get_db)) -> dict:
    """把备份里某个账号的就诊记录并入现有账号，按 client_id 去重。"""
    from app.db.models import Record as RecordModel

    target_id = str(body.get("target_id") or "")
    account = body.get("account") or {}
    user = db.get(User, target_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "目标账号不存在")

    existing = {c for c in db.scalars(
        select(RecordModel.client_id).where(RecordModel.user_id == target_id))}
    added, skipped = 0, 0
    for rec in account.get("records") or []:
        cid = rec.get("client_id") or ""
        if cid and cid in existing:
            skipped += 1
            continue
        visit = rec.get("visit_at")
        db.add(RecordModel(
            user_id=target_id,
            client_id=cid,
            visit_at=datetime.fromisoformat(visit) if visit else datetime.now(UTC),
            hospital=rec.get("hospital") or "",
            summary=rec.get("summary") or "",
            transcript=rec.get("transcript") or "",
            meds=rec.get("meds") or [],
            follow_up=rec.get("follow_up") or "",
            notes=rec.get("notes") or [],
            conditions=rec.get("conditions") or [],
            discontinued=rec.get("discontinued") or [],
            allergy=rec.get("allergy") or "",
            allergy_denied=bool(rec.get("allergy_denied")),
            mode=rec.get("mode") or "manual",
        ))
        existing.add(cid)
        added += 1
    db.commit()
    return {"ok": True, "username": user.username, "added": added, "skipped": skipped}


@router.post("/accounts/merge", dependencies=[Depends(_require_admin)])
def merge_accounts(body: dict, db: Session = Depends(get_db)) -> dict:
    """把 from_id 的内容并入 to_id。

    - 就诊记录按 client_id 去重后搬过去（来源账号的记录会被删除，不是复制）；
    - 资料只补 to 的空字段（姓名以 to 为准，避免把新号名字改掉）；
    - delete_source=true 时删掉来源账号；删除前先把来源完整备份。
    """
    from app.db.models import Profile as ProfileModel, Record as RecordModel

    src_id = str(body.get("from_id") or "")
    dst_id = str(body.get("to_id") or "")
    if not src_id or not dst_id or src_id == dst_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "from_id / to_id 不合法")
    src, dst = db.get(User, src_id), db.get(User, dst_id)
    if src is None or dst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "来源或目标账号不存在")

    backup = _backup(db, [{"id": src.id, "username": src.username, "records": 0}])
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"merge-{src.username}-{stamp}.json"
    path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

    # 记录：搬过去（先去重，再删来源）
    dst_cids = {c for c in db.scalars(
        select(RecordModel.client_id).where(RecordModel.user_id == dst_id))}
    moved, skipped = 0, 0
    for rec in list(db.scalars(select(RecordModel).where(RecordModel.user_id == src_id))):
        if rec.client_id and rec.client_id in dst_cids:
            skipped += 1
            db.delete(rec)
            continue
        rec.user_id = dst_id
        dst_cids.add(rec.client_id)
        moved += 1

    # 资料：只补空字段
    sp, dp = db.get(ProfileModel, src_id), db.get(ProfileModel, dst_id)
    filled = []
    if sp is not None:
        if dp is None:
            dp = ProfileModel(user_id=dst_id, name=sp.name)
            db.add(dp)
        for field in ("age", "gender", "hospital"):
            if not getattr(dp, field) and getattr(sp, field):
                setattr(dp, field, getattr(sp, field))
                filled.append(field)
        if not dp.idcard_enc and sp.idcard_enc:
            dp.idcard_enc = sp.idcard_enc
            filled.append("idcard")
        # 手机号以目标账号为准，不搬（它是登录身份）

    db.commit()

    deleted = False
    if bool(body.get("delete_source")):
        repo.delete_user_account(db, src_id)
        deleted = True

    return {
        "ok": True,
        "from": src.username,
        "to": dst.username,
        "moved_records": moved,
        "skipped_records": skipped,
        "filled_profile": filled,
        "source_deleted": deleted,
        "backup_file": str(path),
        "backup": backup,
    }


@router.post("/accounts/credentials", dependencies=[Depends(_require_admin)])
def set_credentials(body: dict, db: Session = Depends(get_db)) -> dict:
    """合并后把目标账号的登录方式改回用户熟悉的手机号 / 密码。

    只给运维用：手机号查重，密码走正常散列。
    """
    from app.core.crypto import encrypt
    from app.core.security import hash_password

    user_id = str(body.get("user_id") or "")
    phone = normalize_phone(str(body.get("phone") or ""))
    password = str(body.get("password") or "")
    if not phone or len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "手机号或密码不合法")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")

    holder = repo.get_user_by_phone(db, phone)
    if holder is not None and holder.id != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "该手机号已被其它账号占用")

    user.phone_hash = phone_digest(phone)
    user.phone_enc = encrypt(phone)
    user.password_hash = hash_password(password)
    profile = db.get(Profile, user.id)
    if profile is not None:
        profile.phone_enc = encrypt(phone)
    # 改密后让旧会话失效，避免旧设备继续挂着
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return {"ok": True, "username": user.username, "phone": _mask(phone)}
