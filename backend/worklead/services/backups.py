"""화면의 백업·복원. 백업 폴더 안의 현재 모드 파일만 선택할 수 있다."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update

from ..api import schemas as S
from ..context import AppContext
from ..db import Database
from ..models import HostBudget, Lead, Run, Source
from ..storage import BackupChanged, RestoreRecoveryFailed, backup_database, file_digest, restore_database, validate_database
from .settings import get_profile_row, get_setting, put_setting, stop_pending_ai
from ..sources.registry import sync_sources

log = logging.getLogger("worklead.backups")


class BackupError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def _directory(app: AppContext) -> Path:
    directory = app.config.backup_dir
    if directory.is_symlink() or directory.resolve().parent != app.config.data_dir.resolve():
        raise BackupError(400, "invalid_backup", "사용자 데이터 폴더 안의 백업 폴더만 사용할 수 있습니다")
    return directory.resolve()


def _candidate(app: AppContext, backup_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}\.sqlite3", backup_id):
        raise BackupError(400, "invalid_backup", "백업 파일 이름이 올바르지 않습니다")
    stem = app.config.db_path.stem
    if not backup_id.startswith(stem + "-") or (stem == "worklead" and backup_id.startswith("worklead-demo-")):
        raise BackupError(400, "invalid_backup", "현재 모드의 백업만 선택할 수 있습니다")
    directory = _directory(app)
    path = directory / backup_id
    if path.is_symlink() or path.resolve().parent != directory:
        raise BackupError(400, "invalid_backup", "백업 폴더의 일반 파일만 선택할 수 있습니다")
    if not path.is_file():
        raise BackupError(404, "backup_not_found", "백업 파일을 찾을 수 없습니다")
    return path


def _summary(path: Path) -> S.BackupOut:
    stat = path.stat()
    return S.BackupOut(id=path.name, file_name=path.name, created_at=datetime.fromtimestamp(stat.st_mtime, UTC), size_bytes=stat.st_size)


def list_backups(app: AppContext) -> S.BackupList:
    directory = _directory(app)
    items = []
    for path in directory.glob("*.sqlite3"):
        try:
            items.append(_summary(_candidate(app, path.name)))
        except (BackupError, OSError):
            continue
    items.sort(key=lambda x: (x.created_at, x.id), reverse=True)
    return S.BackupList(directory=str(directory), items=items)


def create_backup(app: AppContext) -> S.BackupOut:
    # Shares the DB gate with workers, while SQLite's online backup handles concurrent writes.
    with app.db.gate.operation():
        return _summary(backup_database(app.config.db_path, _directory(app), "manual"))


def inspect_backup(app: AppContext, backup_id: str) -> S.BackupInspection:
    path = _candidate(app, backup_id)
    digest = file_digest(path)
    revision = None
    count = None
    problem = None
    try:
        if any(Path(str(path) + suffix).exists() and Path(str(path) + suffix).stat().st_size for suffix in ("-wal", "-journal")):
            raise ValueError("별도 WAL에 의존하는 파일입니다. 앱에서 생성한 백업을 선택하세요")
        revision, count = validate_database(path)
        if file_digest(path) != digest:
            raise ValueError("검사 도중 백업이 변경되었습니다. 다시 검사하세요")
    except ValueError as exc:
        problem = str(exc)
    return S.BackupInspection(backup=_summary(path), sha256=digest, revision=revision, can_restore=problem is None, problem=problem, lead_count=count)


def _prepare(app: AppContext, path: Path, current_budgets: list[dict] | None = None) -> None:
    db = Database(path)
    try:
        with db.session() as s:
            sync_sources(s, app.adapters)
            get_profile_row(s)
            # Restoring old consent must not restart paid external transmission.
            # The separate AI ledger and OS key are intentionally not restored.
            ai = get_setting(s, "ai")
            put_setting(s, "ai", {**ai, "enabled": False, "external_transfer_consent": False, "pricing_confirmed": False, "price_version": None})
            stop_pending_ai(s)
            # A data restore must not undo requests already sent to external sites today.
            for current in current_budgets or []:
                row = s.get(HostBudget, (current["host"], current["window"]))
                if row is None:
                    s.add(HostBudget(**current))
                else:
                    row.used = max(row.used, current["used"])
                    row.limit = current["limit"]
                    if current["last_request_at"] is not None and (row.last_request_at is None or row.last_request_at < current["last_request_at"]):
                        row.last_request_at = current["last_request_at"]
            for source in s.scalars(select(Source)):
                source.auto_collect_enabled = False
            for run in s.scalars(select(Run).where(Run.state.in_(("queued", "running", "paused")))):
                run.state = "cancelled"
                run.finished_at = app.clock()
                run.lease_owner = None
                run.lease_expires_at = None
                run.retry_after = None
                run.pause_requested = False
                run.cancel_requested = False
                run.note = "백업 복원으로 이전 대기·진행 작업을 취소했습니다"
            # Restored runs cannot resume, including orphaned flags from older
            # interrupted jobs. Clear only this temporary UI state; user records
            # and analysis results remain as captured in the selected backup.
            s.execute(update(Lead).where(Lead.active_job.is_not(None)).values(active_job=None))
    finally:
        db.dispose()


def restore_backup(app: AppContext, backup_id: str, sha256: str) -> S.RestoreOut:
    path = _candidate(app, backup_id)
    # No API session dependency here: acquiring exclusive access from a live session would deadlock.
    with app.db.gate.exclusive():
        path = _candidate(app, backup_id)
        with app.db.session() as s:
            current_budgets = [{"host": row.host, "window": row.window, "used": row.used, "limit": row.limit,
                                "last_request_at": row.last_request_at}
                               for row in s.scalars(select(HostBudget).where(HostBudget.window == datetime.now(UTC).strftime("%Y-%m-%d")))]
        app.db.dispose()
        try:
            safety = restore_database(path, app.config.db_path, app.config.backup_dir,
                                      expected_sha256=sha256, prepare=lambda staged: _prepare(app, staged, current_budgets))
        except BackupChanged as exc:
            raise BackupError(409, "backup_changed", str(exc)) from exc
        except ValueError as exc:
            raise BackupError(400, "invalid_backup", str(exc)) from exc
        except RestoreRecoveryFailed as exc:
            app.db.gate.unavailable()
            raise BackupError(500, "restore_recovery_failed", f"안전 백업 복구에도 실패하여 수집을 중지했습니다. 앱을 종료하고 다음 안전 백업을 확인하세요: {exc.safety_backup.name}") from exc
        except Exception as exc:
            log.exception("restore did not complete")
            raise BackupError(500, "restore_failed", "복원을 완료하지 못했습니다. 기존 데이터는 유지됩니다. 백업 파일과 디스크 상태를 확인하세요", retryable=True) from exc
        _, count = validate_database(app.config.db_path)
        app.db.gate.restored()
        app.hub.notify()
        return S.RestoreOut(restored_backup_id=backup_id, safety_backup=_summary(safety), lead_count=count, auto_collection_paused=True, ai_analysis_paused=True)
