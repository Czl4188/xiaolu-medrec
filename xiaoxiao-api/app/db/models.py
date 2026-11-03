"""数据模型。

两条硬约束写在这里而不是靠自觉：
1. 所有业务表都带 user_id，且查询一律经 repository 层附加过滤条件。
2. 运行记录（run/step/tool_call/token_usage）与业务数据分表，方便按天归档。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # 展示名默认等于用户名；患者档案里的姓名单独放 profile
    display_name: Mapped[str] = mapped_column(String(64), default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    profile: Mapped["Profile"] = relationship(back_populates="user", uselist=False)


class Session(Base):
    """服务端会话。

    不用 JWT 的原因：签名密钥在部署平台上难以稳定（多进程/重启会产生不同密钥，
    表现为「刚发的令牌立刻失效」）。改成随机令牌 + 数据库校验，重启、扩容都不受影响，
    而且能真正「退出即失效」。
    """

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user", "user_id"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Profile(Base):
    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(64), default="")
    age: Mapped[str] = mapped_column(String(8), default="")
    gender: Mapped[str] = mapped_column(String(8), default="")
    hospital: Mapped[str] = mapped_column(String(128), default="")
    # 敏感字段：加密后存，接口只对本人返回明文
    phone_enc: Mapped[str] = mapped_column(Text, default="")
    idcard_enc: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    user: Mapped[User] = relationship(back_populates="profile")


class Record(Base):
    """一次就诊。字段与前端 localStorage 结构保持一致，便于迁移。"""

    __tablename__ = "records"
    __table_args__ = (
        Index("ix_records_user_visit", "user_id", "visit_at"),
        UniqueConstraint("user_id", "client_id", name="uq_records_user_client"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 客户端生成的 id（v_20261102_2037），用于幂等与前端对账
    client_id: Mapped[str] = mapped_column(String(48), default="")
    visit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    hospital: Mapped[str] = mapped_column(String(160), default="")
    summary: Mapped[str] = mapped_column(String(240), default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    meds: Mapped[list] = mapped_column(JSON, default=list)
    follow_up: Mapped[str] = mapped_column(String(160), default="")
    allergy: Mapped[str] = mapped_column(String(160), default="")
    notes: Mapped[list] = mapped_column(JSON, default=list)
    # 具体疾病与医疗事件（高血压 / 胃镜 / 胆囊结石手术…），供问答与检索使用
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    # 明确停用/不吃的药：单独存，不混进当前用药
    discontinued: Mapped[list] = mapped_column(JSON, default=list)
    # 明确否认过敏（「没有过敏」）：与 allergy 互斥，避免把否认记成过敏史
    allergy_denied: Mapped[bool] = mapped_column(default=False)
    mode: Mapped[str] = mapped_column(String(16), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Run(Base):
    """一次 Agent 运行（对应架构文档 §5）。"""

    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="chat")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/running/ok/failed/cancelled
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Step(Base):
    __tablename__ = "run_steps"
    __table_args__ = (Index("ix_steps_run", "run_id", "seq"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="ok")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TokenUsage(Base):
    """模型用量与成本：没有这张表，谈「成本可追踪」就是空话。"""

    __tablename__ = "token_usage"
    __table_args__ = (Index("ix_usage_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    """持久化任务队列。

    架构文档要求长任务不能只挂 BackgroundTasks（进程重启就丢）。这张表就是
    队列的本体：worker 用行级锁抢占，失败重试，超过次数进 dead 状态。
    单机/SQLite 也能跑；上生产把 REDIS_URL 配上或换 RabbitMQ 时，只替换
    repository 的 claim() 实现即可。
    """

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/ok/failed/dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    locked_by: Mapped[str] = mapped_column(String(48), default="")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
