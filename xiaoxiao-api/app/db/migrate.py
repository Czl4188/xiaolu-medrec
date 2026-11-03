"""轻量结构迁移。

`create_all` 只会建缺失的表，不会给已有表补列。线上库是上一版建的，
新增 `phone_enc` / `idcard_enc` 后必须显式 ALTER，否则资料接口会 500。
写法保持 SQLite / PostgreSQL 通用。
"""

import logging

from sqlalchemy import inspect

from app.db.session import engine

logger = logging.getLogger("app.migrate")

_COLUMNS = (
    ("profiles", "phone_enc", "TEXT DEFAULT ''"),
    ("profiles", "idcard_enc", "TEXT DEFAULT ''"),
)


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
