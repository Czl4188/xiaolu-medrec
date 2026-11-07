"""数据访问集中在这里。

关键点：所有涉及用户数据的函数都**必须**接收 user_id 且拼进 where，
路由层不直接写 query —— 这样「忘记加 user_id」这种事故只剩一个发生地点。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import String, cast, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import encrypt, normalize_phone, phone_digest
from app.db.models import Job, Profile, Record, Run, Session, Step, ToolCall, TokenUsage, User


# ————————————————— 用户 —————————————————
def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_user_by_phone(db: Session, phone: str) -> User | None:
    digest = phone_digest(phone)
    if not digest:
        return None
    return db.scalar(select(User).where(User.phone_hash == digest))


def get_legacy_users_by_username(db: Session, username: str) -> list[User]:
    """还没绑手机号的老账号。

    新账号一律有手机号，所以这里过滤 phone_hash IS NULL 之后，
    即使新账号取了同名用户名，也不会和可登录的老账号混在一起。
    """
    rows = db.scalars(
        select(User).where(User.username == username, User.phone_hash.is_(None))
    ).all()
    return list(rows)


def get_user(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def create_user(db: Session, username: str, password_hash: str, phone: str = "") -> User:
    normalized = normalize_phone(phone)
    user = User(
        username=username,
        password_hash=password_hash,
        display_name=username,
        phone_hash=phone_digest(normalized) or None,
        phone_enc=encrypt(normalized),
    )
    db.add(user)
    try:
        db.flush()
        db.add(Profile(user_id=user.id, name=username, phone_enc=encrypt(normalized)))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    db.refresh(user)
    return user


def set_user_phone(db: Session, user: User, phone: str) -> User:
    """绑定或更换登录手机号；profile 同步一份，保证资料页显示一致。"""
    normalized = normalize_phone(phone)
    user.phone_hash = phone_digest(normalized) or None
    user.phone_enc = encrypt(normalized)
    profile = db.get(Profile, user.id)
    if profile is not None:
        profile.phone_enc = encrypt(normalized)
    db.commit()
    db.refresh(user)
    return user


def get_profile(db: Session, user_id: str) -> Profile | None:
    return db.get(Profile, user_id)


def upsert_profile(db: Session, user_id: str, **fields) -> Profile:
    phone = fields.pop("phone", None)
    idcard = fields.pop("idcard", None)
    profile = db.get(Profile, user_id)
    if profile is None:
        profile = Profile(user_id=user_id)
        db.add(profile)
    for key, value in fields.items():
        if hasattr(profile, key):
            setattr(profile, key, value)
    # 敏感字段加密后再落库；空串表示清空
    if phone is not None:
        profile.phone_enc = encrypt(phone)
    if idcard is not None:
        profile.idcard_enc = encrypt(idcard)
    profile.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(profile)
    return profile


# ————————————————— 会话 —————————————————
def create_session(db: Session, user_id: str, token_hash: str, ttl_seconds: int) -> None:
    db.add(Session(
        token_hash=token_hash,
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    ))
    db.commit()


def get_session_user(db: Session, token_hash: str) -> User | None:
    row = db.get(Session, token_hash)
    if not row:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:          # SQLite 读回来是 naive
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        db.delete(row)
        db.commit()
        return None
    return db.get(User, row.user_id)


def delete_session(db: Session, token_hash: str) -> None:
    row = db.get(Session, token_hash)
    if row:
        db.delete(row)
        db.commit()


def update_password(db: Session, user_id: str, password_hash: str) -> bool:
    user = db.get(User, user_id)
    if user is None:
        return False
    user.password_hash = password_hash
    db.commit()
    return True


def delete_other_sessions(db: Session, user_id: str, keep_token_hash: str) -> int:
    """改密码后让其它设备下线，当前这台保留。

    不保留当前会话的话，用户在设置页改完密码会被立刻踢回登录页，
    看起来像「改失败了」。
    """
    rows = list(db.scalars(
        select(Session).where(Session.user_id == user_id, Session.token_hash != keep_token_hash)
    ))
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)


def delete_user_account(db: Session, user_id: str) -> dict[str, int]:
    """彻底删除账号及全部业务数据。

    SQLite 默认不启用外键级联，所以这里按依赖顺序显式删除，
    不依赖 PRAGMA 或数据库配置；换到 PostgreSQL 也不会漏删。
    返回各表实际删除的行数，便于前端如实反馈「删了什么」。
    """
    run_ids = list(db.scalars(select(Run.id).where(Run.user_id == user_id)))
    if run_ids:
        db.execute(delete(Step).where(Step.run_id.in_(run_ids)))
        db.execute(delete(ToolCall).where(ToolCall.run_id.in_(run_ids)))

    counts: dict[str, int] = {}
    statements = (
        ("records", delete(Record).where(Record.user_id == user_id)),
        ("runs", delete(Run).where(Run.user_id == user_id)),
        ("usage", delete(TokenUsage).where(TokenUsage.user_id == user_id)),
        ("sessions", delete(Session).where(Session.user_id == user_id)),
        ("profile", delete(Profile).where(Profile.user_id == user_id)),
        # 任务队列里可能还压着这个账号的抽取任务，避免注销后仍被处理
        ("jobs", delete(Job).where(cast(Job.payload, String).like(f"%{user_id}%"))),
    )
    for name, stmt in statements:
        counts[name] = db.execute(stmt).rowcount or 0

    db.execute(delete(User).where(User.id == user_id))
    db.commit()
    return counts


# ————————————————— 就诊记录 —————————————————
TRASH_KEEP = 5   # 「最近删除」只保留 5 份，超出真删最旧的


def list_records(db: Session, user_id: str) -> list[Record]:
    """在用档案：软删除的不出现在这里。"""
    stmt = (
        select(Record)
        .where(Record.user_id == user_id, Record.deleted_at.is_(None))
        .order_by(Record.visit_at.desc())
    )
    return list(db.scalars(stmt))


def get_record(db: Session, user_id: str, record_id: str,
               include_deleted: bool = False) -> Record | None:
    stmt = select(Record).where(Record.id == record_id, Record.user_id == user_id)
    if not include_deleted:
        stmt = stmt.where(Record.deleted_at.is_(None))
    return db.scalar(stmt)


def list_deleted_records(db: Session, user_id: str, limit: int = TRASH_KEEP) -> list[Record]:
    """最近删除，按删除时间倒序。"""
    stmt = (
        select(Record)
        .where(Record.user_id == user_id, Record.deleted_at.is_not(None))
        .order_by(Record.deleted_at.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def create_record(db: Session, user_id: str, data: dict) -> tuple[Record, bool]:
    """按 client_id 幂等：同一条记录重复提交只落一份。返回 (记录, 是否新建)。"""
    client_id = (data.get("client_id") or "").strip()
    if client_id:
        existing = db.scalar(
            select(Record).where(Record.user_id == user_id, Record.client_id == client_id)
        )
        if existing:
            # 同一条记录此前被删过：再提交等于恢复，别让它「存了但看不见」
            if existing.deleted_at is not None:
                existing.deleted_at = None
                db.commit()
                db.refresh(existing)
            return existing, False

    record = Record(user_id=user_id, **data)
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Record).where(Record.user_id == user_id, Record.client_id == client_id)
        )
        if existing:
            return existing, False
        raise
    db.refresh(record)
    return record, True


def delete_record(db: Session, user_id: str, record_id: str) -> bool:
    """软删除：进回收站，超出 5 份才真删最旧的那条。"""
    record = get_record(db, user_id, record_id)
    if not record:
        return False
    record.deleted_at = datetime.now(UTC)
    db.flush()

    # 只留最近 TRASH_KEEP 份：多出来的硬删，避免存储与隐私风险累积
    extra = db.scalars(
        select(Record)
        .where(Record.user_id == user_id, Record.deleted_at.is_not(None))
        .order_by(Record.deleted_at.desc())
        .offset(TRASH_KEEP)
    ).all()
    for old in extra:
        db.delete(old)
    db.commit()
    return True


def restore_record(db: Session, user_id: str, record_id: str) -> Record | None:
    """从回收站恢复。记录不在回收站里（或不属于本人）返回 None。"""
    record = get_record(db, user_id, record_id, include_deleted=True)
    if not record or record.deleted_at is None:
        return None
    record.deleted_at = None
    db.commit()
    db.refresh(record)
    return record


# ————————————————— 运行记录 —————————————————
def create_run(db: Session, user_id: str, kind: str, payload: dict,
               idempotency_key: str | None = None) -> tuple[Run, bool]:
    if idempotency_key:
        existing = db.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
        if existing:
            return existing, False
    run = Run(user_id=user_id, kind=kind, input=payload,
              idempotency_key=idempotency_key or None, status="queued")
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
        if existing:
            return existing, False
        raise
    db.refresh(run)
    return run, True


def finish_run(db: Session, run_id: str, status: str, output: dict | None = None,
               error: str = "") -> None:
    db.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(status=status, output=output or {}, error=error,
                finished_at=datetime.now(UTC))
    )
    db.commit()


def add_step(db: Session, run_id: str, seq: int, name: str, status: str = "ok",
             detail: dict | None = None) -> None:
    db.add(Step(run_id=run_id, seq=seq, name=name, status=status, detail=detail or {},
                finished_at=datetime.now(UTC)))
    db.commit()


def add_tool_call(db: Session, run_id: str, tool: str, args: dict, result: dict,
                  error: str = "", duration_ms: int = 0) -> None:
    db.add(ToolCall(run_id=run_id, tool=tool, args=args, result=result,
                    error=error, duration_ms=duration_ms))
    db.commit()


def get_run(db: Session, user_id: str, run_id: str) -> Run | None:
    return db.scalar(select(Run).where(Run.id == run_id, Run.user_id == user_id))


# ————————————————— 任务队列 —————————————————
def enqueue(db: Session, kind: str, payload: dict,
            idempotency_key: str | None = None, delay_seconds: int = 0,
            max_attempts: int = 5) -> tuple[Job, bool]:
    if idempotency_key:
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing:
            return existing, False
    job = Job(kind=kind, payload=payload, idempotency_key=idempotency_key or None,
              max_attempts=max_attempts,
              run_after=datetime.now(UTC) + timedelta(seconds=delay_seconds))
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing:
            return existing, False
        raise
    db.refresh(job)
    return job, True


def claim_job(db: Session, worker_id: str) -> Job | None:
    """抢占一个到期任务。

    PostgreSQL 下用 FOR UPDATE SKIP LOCKED，多 Worker 不会抢同一条；
    SQLite 单机没有并发写，普通事务即可。
    """
    now = datetime.now(UTC)
    stmt = (
        select(Job)
        .where(Job.status == "pending", Job.run_after <= now)
        .order_by(Job.run_after)
        .limit(1)
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    job = db.scalar(stmt)
    if not job:
        return None

    job.status = "running"
    job.locked_by = worker_id
    job.locked_at = now
    job.attempts += 1
    db.commit()
    db.refresh(job)
    return job


def complete_job(db: Session, job_id: str) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(status="ok", last_error=""))
    db.commit()


def fail_job(db: Session, job: Job, error: str) -> str:
    """失败按指数退避重排；超过 max_attempts 进 dead（相当于死信）。"""
    if job.attempts >= job.max_attempts:
        job.status = "dead"
    else:
        job.status = "pending"
        job.run_after = datetime.now(UTC) + timedelta(seconds=min(300, 5 * 2 ** job.attempts))
    job.last_error = error[:1000]
    db.commit()
    return job.status
