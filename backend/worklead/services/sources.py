"""소스 상태·탐색 범위(Coverage) 직렬화.

탐색 목표(전국)와 실제 확인 범위를 분리한다. 계획 작업 완료율은 시장 포괄률이 아니다 (market_coverage=unknown).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api import schemas as S
from ..engine.http import budget_status
from ..models import CollectionTask, CoverageUnit, Run, Source, SourcePolicy
from ..sources.profiled import check_profile
from ..sources.regions import SIDO


def research_state(adapter: Any) -> S.Research:
    profile = getattr(adapter, "profile", None)
    if profile is None:
        return S.Research(ready=True, missing=[])
    chk = check_profile(profile)
    return S.Research(ready=chk.ready, missing=chk.missing)


def coverage(s: Session, db: Any, source: Source, adapter: Any) -> S.Coverage | None:
    if source.kind != "site":
        return None
    last = s.scalar(select(Run).where(Run.source_id == source.id, Run.kind == "discovery", Run.scan_cycle.is_not(None)).order_by(Run.created_at.desc()).limit(1))
    plan: dict[str, Any] = (last.checkpoint or {}).get("plan", {}) if last else {}
    hosts = set(getattr(adapter, "allowed_hosts", set()))
    daily = int(getattr(adapter, "daily_request_budget", 0))
    used, limit = budget_status(db, hosts, daily) if hosts else (0, 0)
    budget = S.Budget(used=used, limit=limit or None, unit_label="요청/일")
    research = research_state(adapter)
    if not plan:
        notes = []
        if not research.ready:
            notes.append("사이트 조사 미완료 — 탐색 범위를 계획할 수 없습니다 (" + ", ".join(research.missing) + ")")
        return S.Coverage(
            target_label="전국",
            region_list_status="unknown",
            unit_label="지역×검색어",
            planned=None,
            completed=0,
            pending=0,
            blocked=0,
            failed=0,
            scan_cycle=None,
            last_visited_at=None,
            next_up=None,
            depth_limit=None,
            budget=budget,
            market_coverage="unknown",
            target_units_total=len(SIDO),
            target_units_covered=None,
            estimated_cycle_days=None,
            notes=notes,
        )
    cycle = s.scalar(select(func.max(CollectionTask.scan_cycle)).where(CollectionTask.source_id == source.id))
    rows = s.execute(
        select(CollectionTask.state, func.count(func.distinct(CollectionTask.region_scope + "|" + CollectionTask.query_group)))
        .where(CollectionTask.source_id == source.id, CollectionTask.scan_cycle == cycle, CollectionTask.depth == 0)
        .group_by(CollectionTask.state)
    ).all()
    by_state = {k: v for k, v in rows}
    planned = int(plan.get("units") or 0)
    completed = by_state.get("done", 0)
    blocked = by_state.get("blocked", 0)
    failed = by_state.get("failed", 0)
    pending = max(0, planned - completed - blocked - failed)
    last_visit = s.scalar(select(func.max(CoverageUnit.last_visited_at)).where(CoverageUnit.source_id == source.id))
    nxt = s.scalar(
        select(CoverageUnit)
        .where(CoverageUnit.source_id == source.id)
        .order_by(CoverageUnit.last_visited_at.is_not(None), CoverageUnit.last_visited_at, CoverageUnit.unit_key)
        .limit(1)
    )
    labels = plan.get("labels", {})
    queries = plan.get("queries", {})
    next_up = f"{labels.get(nxt.region_scope) or nxt.region_scope} · {queries.get(nxt.query_group) or nxt.query_group}" if nxt else None
    req = s.scalar(select(func.sum(func.json_extract(Run.counts, "$.requests"))).where(Run.source_id == source.id, Run.kind == "discovery", Run.scan_cycle == cycle)) or 0
    per_unit = (req / completed) if completed else float(plan.get("depth_limit") or 1)
    est = round(planned * per_unit / daily, 1) if daily and planned else None
    notes = list(plan.get("notes", []))
    if est and est > 1:
        notes.append(f"요청 예산 기준 전국 한 바퀴 약 {est}일 예상 (추정)")
    return S.Coverage(
        target_label=plan.get("target_label", "전국"),
        region_list_status=plan.get("region_list_status", "unknown"),
        unit_label="지역×검색어",
        planned=planned,
        completed=completed,
        pending=pending,
        blocked=blocked,
        failed=failed,
        scan_cycle=cycle,
        last_visited_at=last_visit,
        next_up=next_up,
        depth_limit=plan.get("depth_limit"),
        budget=budget,
        market_coverage="unknown",
        target_units_total=plan.get("target_units_total"),
        target_units_covered=plan.get("target_units_covered"),
        estimated_cycle_days=est,
        notes=notes,
    )


def serialize(s: Session, db: Any, source: Source, adapter: Any) -> S.SourceOut:
    policy = s.get(SourcePolicy, source.id)
    last = s.scalar(select(Run.id).where(Run.source_id == source.id).order_by(Run.created_at.desc()).limit(1))
    caps = [S.Capability(key=c.key, label=c.label, support=c.support, note=c.note) for c in adapter.describe_capabilities()] if adapter else []
    return S.SourceOut(
        id=source.id,
        name=source.name,
        kind=source.kind,  # type: ignore[arg-type]
        scope_note=source.scope_note,
        adapter_version=source.adapter_version,
        policy=S.Policy(
            status=policy.status if policy else "permission_pending",  # type: ignore[arg-type]
            basis=policy.basis if policy else None,
            note=policy.note if policy else None,
            reviewed_at=policy.reviewed_at if policy else None,
            reviewed_by=policy.reviewed_by if policy else None,
            robots_status=policy.robots_status if policy else "unchecked",
            robots_checked_at=policy.robots_checked_at if policy else None,
            robots_summary=policy.robots_summary if policy else None,
        ),
        auto_collect=S.AutoCollect(enabled=source.auto_collect_enabled, interval_minutes=source.interval_minutes),
        health=S.Health(status=source.health_status, checked_at=source.health_checked_at, message=source.health_message, code=source.health_code),  # type: ignore[arg-type]
        stopped_reason=source.stopped_reason,
        capabilities=caps,
        coverage=coverage(s, db, source, adapter),
        research=research_state(adapter),
        last_run_id=last,
    )


def run_out(r: Run) -> S.RunOut:
    counts = {k: int((r.counts or {}).get(k, 0)) for k in S.RunCounts.model_fields}
    return S.RunOut(
        id=r.id,
        source_id=r.source_id,
        lead_id=r.lead_id,
        kind=r.kind,  # type: ignore[arg-type]
        state=r.state,  # type: ignore[arg-type]
        trigger=r.trigger,  # type: ignore[arg-type]
        created_at=r.created_at,
        started_at=r.started_at,
        finished_at=r.finished_at,
        progress=S.Progress(done=r.progress_done, total=r.progress_total, label=r.progress_label),
        counts=S.RunCounts(**counts),
        error=S.RunError(code=r.error_code, message=r.error_message or "", retry_after=r.retry_after) if r.error_code else None,
        note=r.note,
        scan_cycle=r.scan_cycle,
        result=r.result or {},
    )
