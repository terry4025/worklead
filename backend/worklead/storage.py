"""마이그레이션·백업·복원.

- 백업은 SQLite 온라인 백업 API 로 수행한다 (WAL 내용 포함, 파일 단순 복사 아님).
- 마이그레이션 전에 기존 DB 를 백업하고, 실패하면 백업에서 되돌린다.
- 복원은 작업 중지 → 대상 파일 무결성·버전 확인 → 현재 DB 백업 → 교체 순서로 수행한다.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

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
    with sqlite3.connect(db_path) as conn:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None
    return row[0] if row else None


def integrity_ok(db_path: Path) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    return bool(row) and row[0] == "ok"


def backup_database(db_path: Path, backup_dir: Path, reason: str) -> Path:
    """온라인 백업. 실행 중인 DB 의 WAL 까지 일관되게 복사한다."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{db_path.stem}-{stamp}-{reason}.sqlite3"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if not integrity_ok(target):
        target.unlink(missing_ok=True)
        raise RuntimeError("백업 무결성 검사 실패")
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
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    shutil.copyfile(source, db_path)


def restore_database(backup_file: Path, db_path: Path, backup_dir: Path) -> Path:
    """호출 전에 워커·DB 연결을 모두 중지해야 한다. 반환값은 복원 직전 백업 경로."""
    if not backup_file.exists():
        raise FileNotFoundError(backup_file)
    if not integrity_ok(backup_file):
        raise ValueError("복원 파일 무결성 검사 실패")
    rev = current_revision(backup_file)
    if rev is None:
        raise ValueError("Worklead DB 가 아니거나 버전 정보가 없습니다")
    known = {r.revision for r in ScriptDirectory.from_config(alembic_config(db_path)).walk_revisions()}
    if rev not in known:
        raise ValueError(f"이 버전에서 알 수 없는 DB 버전입니다: {rev}")
    safety = backup_database(db_path, backup_dir, "pre-restore") if db_path.exists() else None
    try:
        _replace_db(backup_file, db_path)
        migrate(db_path, backup_dir)
    except Exception:
        if safety is not None:
            _replace_db(safety, db_path)
        raise
    return safety or backup_file
