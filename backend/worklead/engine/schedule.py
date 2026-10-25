"""Persistent scheduling based on actual runs, shared by the worker and UI."""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..models import Run, Source
from .runs import ACTIVE

RECHECK_EVERY = {"recheck_recent": timedelta(hours=6), "recheck_stale": timedelta(hours=24)}


def next_due(s, source: Source, kind: str, now: datetime) -> datetime | None:
    last = s.scalar(select(Run).where(Run.source_id == source.id, Run.kind == kind).order_by(Run.created_at.desc()).limit(1))
    if last is not None and last.state in ACTIVE:
        return None
    every = timedelta(minutes=source.interval_minutes or 360) if kind == "discovery" else RECHECK_EVERY[kind]
    due = (last.finished_at or last.created_at) + every if last else now
    if (kind == "discovery" and source.id == "daangn-alba" and last is not None
            and last.state == "succeeded" and last.started_at is not None):
        # The adaptive regional planner enforces 10/30/120-minute due times.
        # Do not add the whole run duration to every five-minute check. Active
        # work above still prevents overlap; failures/cooldowns stay conservative.
        due = last.started_at + every
    if (kind == "discovery" and last is not None and last.state == "partial"
            and not last.error_code and (last.result or {}).get("continuation_pending")):
        delay = max(60, int((last.result or {}).get("continuation_delay_seconds", 60)))
        due = (last.finished_at or last.created_at) + timedelta(seconds=delay)
    # A source's discovery, rechecks and manual requests share its server limit.
    cooldown = s.scalar(select(func.max(Run.retry_after)).where(Run.source_id == source.id, Run.retry_after > now))
    return max(due, cooldown) if cooldown else due
