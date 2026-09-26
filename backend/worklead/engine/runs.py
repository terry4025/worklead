"""영속 작업 큐. API 는 작업을 기록하고 job_id/run_id 를 돌려주며, 관리되는 워커가 실행한다.

- 임대(lease)·heartbeat: 워커가 죽으면 임대 만료 후 queued 로 되돌려 이어서 실행한다.
- 멱등 키: 같은 키로 다시 요청하면 기존 실행을 돌려준다 (중복 실행 방지).
- 일시정지·취소는 요청 플래그로 전달하고 워커가 작업 단위 사이에서 반영한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import Database, utcnow
from ..models import Run, new_id
from .events import emit

ACTIVE = ("queued", "running", "paused")
TERMINAL = ("succeeded", "partial", "failed", "cancelled")
COLLECTION_KINDS = ("discovery", "recheck_recent", "recheck_stale")
INTERACTIVE_KINDS = ("recheck_lead", "reanalyze_lead", "reanalyze_all", "import", "export")

EMPTY_COUNTS = {
    "requests": 0,
    "details_fetched": 0,
    "created": 0,
    "updated": 0,
    "duplicates": 0,
    "excluded": 0,
    "parse_failures": 0,
    "fetch_failures": 0,
    "policy_stops": 0,
    "ai_failures": 0,
}


class RunConflict(Exception):
    def __init__(self, code: str, message: str, run_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.run_id = run_id


def enqueue(
    s: Session,
    kind: str,
    *,
    source_id: str | None = None,
    lead_id: str | None = None,
    trigger: str = "manual",
    idempotency_key: str | None = None,
    input: dict[str, Any] | None = None,
    exclusive: bool = True,
) -> tuple[Run, bool]:
    """(run, created). 같은 멱등 키가 있으면 기존 실행을 돌려준다."""
    if idempotency_key:
        found = s.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
        if found is not None:
            return found, False
    if exclusive and source_id and kind in COLLECTION_KINDS:
        active = s.scalar(select(Run).where(Run.source_id == source_id, Run.kind == kind, Run.state.in_(ACTIVE)))
        if active is not None:
            raise RunConflict("run_already_active", "같은 소스의 같은 종류 실행이 이미 진행 중입니다", active.id)
    if exclusive and lead_id:
        active = s.scalar(select(Run).where(Run.lead_id == lead_id, Run.kind == kind, Run.state.in_(ACTIVE)))
        if active is not None:
            return active, False
    run = Run(
        id=new_id("run"),
        kind=kind,
        source_id=source_id,
        lead_id=lead_id,
        trigger=trigger,
        idempotency_key=idempotency_key,
        state="queued",
        counts=dict(EMPTY_COUNTS),
        input=input or {},
        created_at=utcnow(),
    )
    s.add(run)
    try:
        s.flush()
    except IntegrityError as exc:  # 동시 요청의 멱등 키 충돌
        s.rollback()
        found = s.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
        if found is None:
            raise RunConflict("conflict", "실행을 만들 수 없습니다") from exc
        return found, False
    emit(s, "run.state_changed", {"run_id": run.id, "source_id": source_id, "state": "queued", "kind": kind})
    return run, True


def claim_next(db: Database, owner: str, kinds: tuple[str, ...], lease_s: int) -> Run | None:
    now = utcnow()
    with db.session() as s:
        candidate = s.scalar(
            select(Run.id)
            .where(Run.state == "queued", Run.kind.in_(kinds), or_(Run.retry_after.is_(None), Run.retry_after <= now))
            .order_by(Run.created_at)
            .limit(1)
        )
        if candidate is None:
            return None
        res = s.execute(
            update(Run)
            .where(Run.id == candidate, Run.state == "queued")
            .values(
                state="running",
                lease_owner=owner,
                lease_expires_at=now + timedelta(seconds=lease_s),
                heartbeat_at=now,
                started_at=now,
            )
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            return None
        run = s.get(Run, candidate)
        assert run is not None
        emit(s, "run.state_changed", {"run_id": run.id, "source_id": run.source_id, "state": "running", "kind": run.kind})
        return run


def heartbeat(db: Database, run_id: str, owner: str, lease_s: int, progress: dict[str, Any] | None = None) -> tuple[bool, bool]:
    """(pause_requested, cancel_requested)"""
    now = utcnow()
    with db.session() as s:
        run = s.get(Run, run_id)
        if run is None or run.lease_owner != owner:
            return False, True
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_s)
        if progress:
            for k, v in progress.items():
                setattr(run, k, v)
            emit(
                s,
                "run.progress",
                {
                    "run_id": run.id,
                    "source_id": run.source_id,
                    "progress": {"done": run.progress_done, "total": run.progress_total, "label": run.progress_label},
                    "counts": run.counts,
                },
            )
        return run.pause_requested, run.cancel_requested


def finish(
    db: Database,
    run_id: str,
    state: str,
    *,
    error: tuple[str, str] | None = None,
    retry_after: datetime | None = None,
    result: dict[str, Any] | None = None,
    note: str | None = None,
) -> None:
    now = utcnow()
    with db.session() as s:
        run = s.get(Run, run_id)
        if run is None:
            return
        run.state = state
        run.finished_at = now if state in TERMINAL else None
        run.lease_owner = None
        run.lease_expires_at = None
        if error:
            run.error_code, run.error_message = error
        if retry_after:
            run.retry_after = retry_after
        if result is not None:
            run.result = result
        if note:
            run.note = note
        if state == "paused":
            run.pause_requested = False
        emit(s, "run.state_changed", {"run_id": run.id, "source_id": run.source_id, "state": state, "kind": run.kind, "error_code": run.error_code})


def recover_expired(db: Database) -> int:
    """임대가 만료된 running 작업을 queued 로 되돌린다 (강제 종료 후 재시작 복구)."""
    now = utcnow()
    with db.session() as s:
        rows = s.scalars(select(Run).where(Run.state == "running", or_(Run.lease_expires_at.is_(None), Run.lease_expires_at < now))).all()
        for run in rows:
            run.state = "queued"
            run.lease_owner = None
            run.note = "앱 종료·중단 후 마지막 저장 지점부터 이어서 실행"
            emit(s, "run.state_changed", {"run_id": run.id, "source_id": run.source_id, "state": "queued", "kind": run.kind})
        return len(rows)


def request_action(s: Session, run: Run, action: str) -> Run:
    if action == "pause":
        if run.state == "queued":
            run.state = "paused"
        elif run.state == "running":
            run.pause_requested = True
        else:
            raise RunConflict("invalid_state", f"{run.state} 상태에서는 일시정지할 수 없습니다")
    elif action == "resume":
        if run.state != "paused":
            raise RunConflict("invalid_state", f"{run.state} 상태에서는 재개할 수 없습니다")
        run.state = "queued"
        run.pause_requested = False
        run.retry_after = None
    elif action == "cancel":
        if run.state in ("queued", "paused"):
            run.state = "cancelled"
            run.finished_at = utcnow()
        elif run.state == "running":
            run.cancel_requested = True
        else:
            raise RunConflict("invalid_state", f"{run.state} 상태에서는 취소할 수 없습니다")
    else:
        raise RunConflict("invalid_action", f"알 수 없는 동작: {action}")
    emit(s, "run.state_changed", {"run_id": run.id, "source_id": run.source_id, "state": run.state, "kind": run.kind})
    return run


def active_filter():
    return and_(Run.state.in_(ACTIVE))
