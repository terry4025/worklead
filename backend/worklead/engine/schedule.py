"""Persistent scheduling based on actual runs, shared by the worker and UI."""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..models import Run, Source, SourcePolicy
from .runs import ACTIVE

RECHECK_EVERY = {"recheck_recent": timedelta(hours=6), "recheck_stale": timedelta(hours=24)}


def regional_due(last: Run | None) -> datetime | None:
    if last is None or last.source_id != "daangn-alba" or last.state not in {"succeeded", "partial"}:
        return None
    value = (last.result or {}).get("regional_retry_after")
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
        return parsed if parsed is not None and parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


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
    if (kind == "discovery" and source.id == "daangn-alba" and last is not None
            and last.state in {"succeeded", "partial"}):
        # A bounded regional retry / saturated-feed poll may precede the next
        # normal watch. This never overrides active work or host cooldowns.
        early = regional_due(last)
        if early is not None:
            due = min(due, early)
    # A source's discovery, rechecks and manual requests share its server limit.
    cooldown = s.scalar(select(func.max(Run.retry_after)).where(Run.source_id == source.id, Run.retry_after > now))
    return max(due, cooldown) if cooldown else due


def scheduler_delay(app) -> float:
    """Wake at a nearby due time instead of rounding it to the next minute.

    Regional revisit rules and host cooldowns still decide whether a request is
    due. This only removes additional scheduler sleep, never advances a poll.
    """
    maximum = max(1.0, float(app.config.scheduler_interval_seconds))
    now = app.clock()
    delay = maximum
    with app.db.session() as session:
        for source in session.scalars(select(Source).where(Source.kind == "site", Source.auto_collect_enabled.is_(True))):
            if source.stopped_reason or getattr(app.adapters.get(source.id), "browser_collection", False):
                continue
            policy = session.get(SourcePolicy, source.id)
            if policy is None or policy.status not in ("allowed", "restricted"):
                continue
            for kind in ("discovery", *RECHECK_EVERY):
                due = next_due(session, source, kind, now)
                if due is not None:
                    delay = min(delay, max(1.0, (due - now).total_seconds()))
    return delay
