"""SQLite 엔진·세션. WAL, 외래키, 잠금 대기(busy_timeout) 정책을 연결마다 적용한다."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import TypeDecorator

BUSY_TIMEOUT_MS = 5000


class MaintenanceBusy(Exception):
    """복원 중이거나 작업이 아직 끝나지 않아 DB를 교체할 수 없다."""


class DatabaseGate:
    """DB 사용과 파일 교체의 경계. 워커는 네트워크 요청을 포함한 전체 실행을 예약한다.

    API의 yield dependency는 진입/종료가 서로 다른 스레드에서 실행될 수 있으므로
    세션 예약 수는 스레드와 무관하게 해제한다. operation의 중첩만 같은 스레드에서 처리한다.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._exclusive = False
        self._unavailable = False
        self._local = threading.local()
        self.generation = 0

    @contextlib.contextmanager
    def access(self) -> Iterator[None]:
        nested = bool(getattr(self._local, "depth", 0))
        if not nested:
            with self._condition:
                if self._unavailable:
                    raise MaintenanceBusy("복원 실패 후 안전 백업 복구가 필요합니다. 수집을 중지했습니다")
                if self._exclusive:
                    raise MaintenanceBusy("백업 복원 중입니다. 잠시 후 다시 시도하세요")
                self._active += 1
        try:
            yield
        finally:
            if not nested:
                with self._condition:
                    self._active -= 1
                    self._condition.notify_all()

    @contextlib.contextmanager
    def operation(self) -> Iterator[None]:
        with self.access():
            depth = getattr(self._local, "depth", 0)
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth = depth

    @contextlib.contextmanager
    def exclusive(self, timeout: float = 10.0) -> Iterator[None]:
        if getattr(self._local, "depth", 0):
            raise RuntimeError("DB 작업 안에서 복원을 시작할 수 없습니다")
        with self._condition:
            if self._exclusive:
                raise MaintenanceBusy("이미 백업 복원 중입니다")
            self._exclusive = True
            deadline = time.monotonic() + timeout
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._exclusive = False
                    self._condition.notify_all()
                    raise MaintenanceBusy("진행 중인 작업이 아직 끝나지 않았습니다. 작업이 끝난 뒤 복원을 다시 시도하세요")
                self._condition.wait(remaining)
        self._local.depth = 1
        try:
            yield
        finally:
            self._local.depth = 0
            with self._condition:
                self._exclusive = False
                self._condition.notify_all()

    def restored(self) -> None:
        # Only the maintenance owner calls this before releasing exclusive access.
        self.generation += 1
        self._unavailable = False

    def unavailable(self) -> None:
        with self._condition:
            self._unavailable = True


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
        self.gate = DatabaseGate()
        self.engine = make_engine(db_path)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        """짧은 트랜잭션 단위. 예외 시 롤백."""
        with self.gate.access():
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
