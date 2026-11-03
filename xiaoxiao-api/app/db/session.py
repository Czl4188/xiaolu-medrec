from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_size=0 if _is_sqlite else settings.db_pool_size,
    max_overflow=0 if _is_sqlite else settings.db_max_overflow,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
