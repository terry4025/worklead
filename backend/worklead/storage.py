"""마이그레이션·백업·복원.

- 백업은 SQLite 온라인 백업 API 로 수행한다 (WAL 내용 포함, 파일 단순 복사 아님).
- 마이그레이션 전에 기존 DB 를 백업하고, 실패하면 백업에서 되돌린다.
- 복원은 작업 중지 → 대상 파일 무결성·버전 확인 → 현재 DB 백업 → 교체 순서로 수행한다.
"""

from __future__ import annotations

import logging
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import JSON

log = logging.getLogger("worklead.storage")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def head_revision() -> str:
    script = ScriptDirectory.from_config(alembic_config(Path("unused.sqlite3")))
    head = script.get_current_head()
    assert head is not None
    return head


def current_revision(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    with closing(_read_connection(db_path)) as conn:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None
    return row[0] if row else None


def integrity_ok(db_path: Path) -> bool:
    try:
        with closing(_read_connection(db_path)) as conn:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        return rows == [("ok",)]
    except sqlite3.DatabaseError:
        return False


def _read_connection(db_path: Path) -> sqlite3.Connection:
    # mode=ro prevents missing/corrupt candidates from silently becoming empty DBs.
    return sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_database(db_path: Path) -> tuple[str, int]:
    """무결성뿐 아니라 해당 버전의 실제 앱 스키마와 외래키도 검증한다."""
    from .models import Base

    if not integrity_ok(db_path):
        raise ValueError("복원 파일 무결성 검사 실패")
    rev = current_revision(db_path)
    known = {r.revision for r in ScriptDirectory.from_config(alembic_config(db_path)).walk_revisions()}
    if rev not in known:
        raise ValueError("Worklead DB 버전 정보가 없거나 이 버전에서 지원하지 않는 백업입니다")
    with closing(_read_connection(db_path)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name, table in Base.metadata.tables.items():
            if name.startswith("cafe") and rev in {"0001", "0002_detail_fetch_states", "0003_native_notifications", "0004_relation_decisions", "0005_page_checkpoints"}:
                continue
            if rev == "0001" and name == "detail_fetch_states":
                continue
            if name not in tables:
                raise ValueError("Worklead 필수 테이블이 없는 백업입니다")
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
            required = set(table.columns.keys())
            if name == "duplicate_links" and rev in {"0001", "0002_detail_fetch_states", "0003_native_notifications"}:
                required -= {"dismissed", "revision"}
            if name == "collection_tasks" and rev in {"0001", "0002_detail_fetch_states", "0003_native_notifications", "0004_relation_decisions"}:
                required -= {"checkpoint"}
            if not required.issubset(columns):
                raise ValueError("Worklead 필수 컬럼이 없는 백업입니다")
            for column in table.columns:
                if column.name in columns and isinstance(column.type, JSON):
                    invalid = conn.execute(f'SELECT 1 FROM "{name}" WHERE "{column.name}" IS NOT NULL AND NOT json_valid("{column.name}") LIMIT 1').fetchone()
                    if invalid is not None:
                        raise ValueError("백업의 JSON 데이터가 손상되었습니다")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("백업의 연결된 데이터가 손상되었습니다 (외래키 검사 실패)")
        count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    assert rev is not None
    return rev, count


def _online_copy(db_path: Path, target: Path) -> None:
    with closing(_read_connection(db_path)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    if not integrity_ok(target):
        raise RuntimeError("백업 무결성 검사 실패")
    with target.open("r+b") as stream:
        os.fsync(stream.fileno())


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def backup_database(db_path: Path, backup_dir: Path, reason: str) -> Path:
    """온라인 백업. 실행 중인 DB 의 WAL 까지 일관되게 복사한다."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]", "-", reason)[:60]
    target = backup_dir / f"{db_path.stem}-{stamp}-{safe_reason}-{uuid4().hex[:12]}.sqlite3"
    temporary = target.with_suffix(".part")
    try:
        _online_copy(db_path, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
        _remove_sidecars(temporary)
    log.info("backup created reason=%s file=%s", reason, target.name)
    return target


def migrate(db_path: Path, backup_dir: Path) -> None:
    head = head_revision()
    current = current_revision(db_path)
    if current == head:
        return
    backup: Path | None = None
    if db_path.exists() and current is not None:
        backup = backup_database(db_path, backup_dir, f"pre-migrate-{current}")
    try:
        command.upgrade(alembic_config(db_path), "head")
    except Exception:
        log.exception("migration failed; restoring backup")
        if backup is not None:
            _replace_db(backup, db_path)
        raise


def _replace_db(source: Path, db_path: Path) -> None:
    # Publish only a fully copied, closed and verified SQLite file.
    temporary = db_path.with_name(f".{db_path.stem}-{uuid4().hex}.restore-part")
    try:
        _online_copy(source, temporary)
        _remove_sidecars(db_path)
        temporary.replace(db_path)
    finally:
        temporary.unlink(missing_ok=True)
        _remove_sidecars(temporary)


def restore_database(
    backup_file: Path,
    db_path: Path,
    backup_dir: Path,
    *,
    expected_sha256: str | None = None,
    prepare: Callable[[Path], None] | None = None,
) -> Path:
    """호출 전에 워커·DB 연결을 모두 중지해야 한다. 반환값은 복원 직전 백업 경로."""
    if not backup_file.exists():
        raise FileNotFoundError(backup_file)
    if backup_file.resolve() == db_path.resolve():
        raise ValueError("현재 사용 중인 DB는 복원 파일로 선택할 수 없습니다")
    if any(Path(str(backup_file) + suffix).exists() and Path(str(backup_file) + suffix).stat().st_size
           for suffix in ("-wal", "-journal")):
        raise ValueError("별도 WAL에 의존하는 파일은 복원할 수 없습니다. 앱에서 생성한 백업을 선택하세요")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Validation/migration/preparation run on an isolated copy before live data is touched.
    with tempfile.TemporaryDirectory(prefix="worklead-restore-", dir=db_path.parent) as temp:
        staged = Path(temp) / db_path.name
        shutil.copyfile(backup_file, staged)
        if expected_sha256 is not None and file_digest(staged) != expected_sha256:
            raise BackupChanged("선택한 백업이 검사 이후 변경되었습니다. 다시 검사하세요")
        validate_database(staged)
        migrate(staged, Path(temp) / "backups")
        if prepare is not None:
            prepare(staged)
        validate_database(staged)
        safety = backup_database(db_path, backup_dir, "pre-restore") if db_path.exists() else None
        try:
            _replace_db(staged, db_path)
            validate_database(db_path)
        except Exception:
            log.exception("restore failed; restoring safety backup")
            if safety is not None:
                try:
                    _replace_db(safety, db_path)
                    validate_database(db_path)
                except Exception as recovery_exc:
                    raise RestoreRecoveryFailed(safety) from recovery_exc
            else:
                _remove_sidecars(db_path)
                db_path.unlink(missing_ok=True)
            raise
    return safety or backup_file


class BackupChanged(ValueError):
    pass


class RestoreRecoveryFailed(RuntimeError):
    def __init__(self, safety_backup: Path):
        super().__init__("복원 실패 후 안전 백업으로 되돌리는 작업도 실패했습니다")
        self.safety_backup = safety_backup
