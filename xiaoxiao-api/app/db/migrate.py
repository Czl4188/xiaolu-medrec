"""轻量结构迁移。

`create_all` 只会建缺失的表，不会给已有表补列，也不会改索引。
线上库是上一版建的，这次两处变化必须显式处理，否则会出事：

1. 新增 `users.phone_hash` / `users.phone_enc`：手机号成为登录身份。
2. `users.username` 去掉唯一约束：用户名允许重复，只作显示名。

第 2 点如果偷懒不做，两个人取同一个名字时第二个会直接 500。
写法则保持 SQLite / PostgreSQL 通用。
"""

import logging

from sqlalchemy import inspect

from app.db.session import engine

logger = logging.getLogger("app.migrate")

_COLUMNS = (
    ("profiles", "phone_enc", "TEXT DEFAULT ''"),
    ("profiles", "idcard_enc", "TEXT DEFAULT ''"),
    ("users", "phone_hash", "TEXT"),
    ("users", "phone_enc", "TEXT DEFAULT ''"),
)

# 手机号摘要的唯一索引，与 create_all 生成的名字保持一致
_PHONE_INDEX = "ix_users_phone_hash"
_USERNAME_INDEX = "ix_users_username"


def ensure_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not tables:
        return

    existing: dict[str, set[str]] = {}
    for table, column, ddl in _COLUMNS:
        if table not in tables:
            continue
        if table not in existing:
            existing[table] = {c["name"] for c in inspector.get_columns(table)}
        if column in existing[table]:
            continue
        with engine.begin() as conn:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.info("已补充字段 %s.%s", table, column)
        existing[table].add(column)


def ensure_indexes() -> None:
    """用户名索引去掉唯一性，手机号摘要建立唯一索引。"""
    inspector = inspect(engine)
    if "users" not in set(inspector.get_table_names()):
        return

    indexes = {ix["name"]: ix for ix in inspector.get_indexes("users")}

    username_ix = indexes.get(_USERNAME_INDEX)
    if username_ix and username_ix.get("unique"):
        with engine.begin() as conn:
            conn.exec_driver_sql(f"DROP INDEX {_USERNAME_INDEX}")
            conn.exec_driver_sql(f"CREATE INDEX {_USERNAME_INDEX} ON users (username)")
        logger.info("已取消 %s 的唯一约束（用户名可重复）", _USERNAME_INDEX)

    columns = {c["name"] for c in inspector.get_columns("users")}
    if "phone_hash" not in columns:
        return
    if _PHONE_INDEX not in {ix["name"] for ix in inspect(engine).get_indexes("users")}:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                f"CREATE UNIQUE INDEX {_PHONE_INDEX} ON users (phone_hash)"
            )
        logger.info("已为 users.phone_hash 建立唯一索引")
