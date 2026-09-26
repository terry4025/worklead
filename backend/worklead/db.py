"""SQLite 엔진·세션. WAL, 외래키, 잠금 대기(busy_timeout) 정책을 연결마다 적용한다."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import TypeDecorator

BUSY_TIMEOUT_MS = 5000


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """UTC 시각을 naive 로 저장하고 aware(UTC) 로 읽는다."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # type: ignore[override]
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime 은 저장하지 않는다 (UTC aware 필요)")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # type: ignore[override]
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_MS / 1000},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - 연결 시점 설정
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur.close()

    return engine


class Database:
    def __init__(self, db_path: Path):
        self.path = db_path
        self.engine = make_engine(db_path)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        """짧은 트랜잭션 단위. 예외 시 롤백."""
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()
