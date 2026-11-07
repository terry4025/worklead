"""작업 실행기: 신규 탐색(전국 순환), 재확인, 수동 입력, 내보내기, 재분석."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, update

from ..context import AppContext
from ..models import CollectionTask, CoverageUnit, DetailFetchState, Lead, Run, Source, SourcePolicy, SourceRecord, new_id
from ..services.diagnostics import write_diagnostics
from ..services.settings import get_setting, ttl_hours
from ..sources.base import DiscoverResult, ParseError, PostNotCollectable, PostRef, SourceNotReady, TaskSpec
from ..sources.profiled import TaskFailed, canonicalize
from . import runs
from .freshness import SAVED_SCOPE, append_saved_candidates, observe_listings, ordered_candidates, prioritize_feed_tasks, saved_candidates, watch_reserve
from .events import emit, notify
from .http import PolicyStop, RunHalt
from .pipeline import apply_analysis, ingest, record_path

log = logging.getLogger("worklead.executor")

MAX_TASKS_PER_RUN = 60
DETAIL_REFRESH_HOURS = 12
EXCLUDED_REFRESH_HOURS = 48
RECHECK_RECENT_DAYS = 14
RECHECK_BATCH = {"recheck_recent": 40, "recheck_stale": 25}


class Halt(Exception):
    def __init__(self, state: str, code: str | None = None, message: str | None = None, retry_after: datetime | None = None):
        super().__init__(message or state)
        self.state = state
        self.code = code
        self.message = message
        self.retry_after = retry_after


class RunScope:
    """실행 중 카운터·진행률·일시정지/취소 확인."""

    def __init__(self, app: AppContext, run: Run, owner: str):
        self.app = app
        self.run_id = run.id
        self.owner = owner
        self.counts: dict[str, int] = {**runs.EMPTY_COUNTS, **(run.counts or {})}
        self.done = run.progress_done
        self.total = run.progress_total
        self.label: str | None = run.progress_label
        self.detail_progress: dict[str, Any] | None = None

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def checkpoint(self, *, check_controls: bool = True) -> None:
        if self.detail_progress is not None:
            with self.app.db.session() as s:
                current = s.get(Run, self.run_id)
                if current:
                    current.result = {**(current.result or {}), "detail_progress": dict(self.detail_progress)}
        pause, cancel = runs.heartbeat(
            self.app.db,
            self.run_id,
            self.owner,
            self.app.config.lease_seconds,
            {"counts": dict(self.counts), "progress_done": self.done, "progress_total": self.total, "progress_label": self.label},
        )
        self.app.hub.notify()
        if check_controls and cancel:
            raise Halt("cancelled", None, "사용자 취소 — 진행 중 요청 종료 후 마지막 저장 지점에서 중단")
        if check_controls and pause:
            raise Halt("paused", None, "사용자 일시정지")


class _ExplorationDeferred(Exception):
    """Keep optional discovery for another run without failing the core scan."""


class _DiscoveryReserved(Exception):
    """Automatic rechecks leave enough HTTP budget for remaining list watches."""


class _Exploration:
    def __init__(self, app: AppContext, scope: RunScope, adapter: Any):
        self.app, self.scope = app, scope
        config = getattr(adapter, "profile", {}).get("discovery", {})
        self.limit = max(0, min(50, int(config.get("exploration_max_requests", 50))))
        self.reserve = max(30, int(config.get("exploration_reserve_requests", 30)))
        self.active = False

    def _state(self, run: Run) -> dict[str, Any]:
        state = dict((run.checkpoint or {}).get("exploration", {}))
        # Resume with the stricter of the original and current limits.
        state["limit"] = min(self.limit, state.get("limit", self.limit))
        state["reserve"] = max(self.reserve, state.get("reserve", self.reserve))
        return state

    @staticmethod
    def _save(run: Run, state: dict[str, Any]) -> None:
        run.checkpoint = {**(run.checkpoint or {}), "exploration": state}
        discovery = dict((run.result or {}).get("discovery", {}))
        discovery.update(
            exploration_requests=state.get("requests", 0),
            exploration_checked=len(state.get("processed", [])),
            exploration_deferred=len(set(state.get("seen", [])) - set(state.get("confirmed", []))),
            exploration_limit=state["limit"], exploration_reserve=state["reserve"],
        )
        run.result = {**(run.result or {}), "discovery": discovery}

    def reserve_request(self, session, budget) -> None:
        if not self.active:
            return
        current = session.get(Run, self.scope.run_id)
        if current is None or current.state != "running" or current.lease_owner != self.scope.owner:
            raise Halt("cancelled", None, "추가 탐색 실행 임대가 종료되었습니다")
        state = self._state(current)
        if state.get("reserved", 0) >= state["limit"] or budget.limit - budget.used < state["reserve"]:
            raise _ExplorationDeferred()
        # Persist before HTTP. A crash before send conservatively retains its
        # reservation, so a restarted run cannot exceed the original limit.
        state["reserved"] = state.get("reserved", 0) + 1
        self._save(current, state)

    def record_request(self) -> None:
        if not self.active:
            return
        with self.app.db.session() as session:
            current = session.get(Run, self.scope.run_id)
            state = self._state(current)
            state["requests"] = state.get("requests", 0) + 1
            self._save(current, state)

    def process(self, adapter: Any, fetcher: Any, refs: list[PostRef], spec: TaskSpec) -> None:
        unique = {_ref_key(ref): ref for ref in refs}
        with self.app.db.session() as session:
            current = session.get(Run, self.scope.run_id)
            state = self._state(current)
            state["seen"] = sorted(set(state.get("seen", [])) | set(unique))
            self._save(current, state)
            processed = set(state.get("processed", []))
            allow_fetch = state.get("reserved", 0) < state["limit"]
            visits = {row.ref_key: row.attempted_at for row in session.scalars(
                select(DetailFetchState).where(DetailFetchState.source_id == adapter.source_id)
            )}
        ordered = sorted(unique.items(), key=lambda item: (
            item[0] in visits,
            visits[item[0]].timestamp() if item[0] in visits else 0,
            -item[1].source_updated_at.timestamp() if item[1].source_updated_at else 0,
            item[0],
        ))
        self.active = True
        try:
            for key, ref in ordered:
                if key in processed:
                    continue
                self.scope.label = f"추가 후보 확인 · {(ref.title_hint or '')[:60]}"
                self.scope.checkpoint()
                try:
                    confirmed = _process_ref(self.app, adapter, fetcher, ref, spec, self.scope.run_id, self.scope,
                                             allow_fetch=allow_fetch)
                except _ExplorationDeferred:
                    allow_fetch = False
                    continue
                except RunHalt as exc:
                    if exc.code != "budget_exhausted":
                        raise
                    # Core work can legitimately spend the last host request.
                    # Optional exploration must not turn that success into failure.
                    allow_fetch = False
                    continue
                with self.app.db.session() as session:
                    current = session.get(Run, self.scope.run_id)
                    state = self._state(current)
                    state["processed"] = sorted(set(state.get("processed", [])) | {key})
                    if confirmed:
                        state["confirmed"] = sorted(set(state.get("confirmed", [])) | {key})
                    self._save(current, state)
                    allow_fetch = allow_fetch and state.get("reserved", 0) < state["limit"]
        finally:
            self.active = False


def _analyze_after_commit(app: AppContext, lead_id: str, scope: RunScope | None = None, *, retry: bool = False) -> None:
    """Actual provider I/O starts only after original/rule commits finish."""
    if getattr(app.provider, "requires_deferred", False):
        app.ai_coordinator.analyze_lead(lead_id, retry=retry, checkpoint=scope.checkpoint if scope else None)


# ── 공통 게이트 ─────────────────────────────────────────────────────


def _gate(app: AppContext, source_id: str) -> None:
    with app.db.session() as s:
        source = s.get(Source, source_id)
        policy = s.get(SourcePolicy, source_id)
        if source is None:
            raise Halt("failed", "unknown_source", "알 수 없는 소스")
        if source.kind == "site":
            if policy is None or policy.status not in ("allowed", "restricted"):
                status = policy.status if policy else "permission_pending"
                raise Halt("failed", "policy_not_allowed", f"자동 수집 권한이 확인되지 않았습니다 ({status}). 정책 검토 후 실행할 수 있습니다.")
            if source.stopped_reason:
                raise Halt("failed", "source_stopped", f"소스가 정지된 상태입니다: {source.stopped_reason}")


def _stop_source(app: AppContext, source_id: str, exc: PolicyStop) -> None:
    now = app.clock()
    with app.db.session() as s:
        source = s.get(Source, source_id)
        if source is None:
            return
        source.stopped_reason = f"{exc.code}: {exc.message}"
        source.health_status = "blocked"
        source.health_code = exc.code
        source.health_message = exc.message
        source.health_checked_at = now
        emit(s, "source.health_changed", {"source_id": source_id, "health": "blocked", "code": exc.code})
        notify(
            s,
            "source_issue",
            f"{source.name}: {exc.message}",
            dedupe_key=f"notify:source:{source_id}:{exc.code}:{now.date().isoformat()}",
            source_id=source_id,
            now=now,
        )
    app.hub.notify()


def _set_health(app: AppContext, source_id: str, status: str, message: str | None, code: str | None = None) -> None:
    with app.db.session() as s:
        source = s.get(Source, source_id)
        if source is None:
            return
        changed = source.health_status != status
        source.health_status, source.health_message, source.health_code = status, message, code
        source.health_checked_at = app.clock()
        if changed:
            emit(s, "source.health_changed", {"source_id": source_id, "health": status, "code": code})


# ── 신규 탐색 ───────────────────────────────────────────────────────


def _unit_key(region_scope: str, query_group: str) -> str:
    return f"{region_scope}|{query_group}"


def _task_key(source_id: str, t: TaskSpec, cycle: int) -> str:
    return f"{source_id}|{t.query_group}|{t.region_scope}|{t.cursor or '-'}|{cycle}"


def _current_cycle(s, source_id: str, unit_keys: set[str], *, revisit: bool = False) -> int:
    cycle = s.scalar(select(func.max(CollectionTask.scan_cycle)).where(CollectionTask.source_id == source_id)) or 1
    done_units = {
        _unit_key(r, q)
        for r, q in s.execute(
            select(CollectionTask.region_scope, CollectionTask.query_group).where(
                CollectionTask.source_id == source_id,
                CollectionTask.scan_cycle == cycle,
                CollectionTask.depth == 0,
                CollectionTask.state == "done",
            )
        )
    }
    # A due revisit unit already done in this cycle starts the next cycle,
    # even with leftovers: fast units must not wait for slow or unfinished ones.
    # Leftover units are still due (not visited) and are planned again.
    if revisit and unit_keys & done_units:
        return cycle + 1
    unfinished = s.scalar(select(CollectionTask.id).where(
        CollectionTask.source_id == source_id,
        CollectionTask.scan_cycle == cycle,
        (CollectionTask.region_scope + "|" + CollectionTask.query_group).in_(unit_keys),
        CollectionTask.state != "done",
    ).limit(1))
    if unfinished:
        return cycle
    if unit_keys and unit_keys <= done_units:
        return cycle + 1
    return cycle
    done_units = {
        _unit_key(r, q)
        for r, q in s.execute(
            select(CollectionTask.region_scope, CollectionTask.query_group).where(
                CollectionTask.source_id == source_id,
                CollectionTask.scan_cycle == cycle,
                CollectionTask.depth == 0,
                CollectionTask.state == "done",
            )
        )
    }
    # A due revisit unit already done in this cycle starts the next cycle,
    # otherwise fast units would wait for every slow unit to come due.
    if unit_keys and (unit_keys <= done_units or (revisit and unit_keys & done_units)):
        return cycle + 1
    return cycle


def _discovery_page(app: AppContext, adapter: Any, spec: TaskSpec, fetcher: Any,
                    task_id: str) -> tuple[DiscoverResult, int, bool]:
    """Snapshot page identities before fetching details so a moving list cannot drop work."""
    if (spec.cursor or "").startswith("detail-drain:"):
        with app.db.session() as s:
            saved = dict(s.get(CollectionTask, task_id).checkpoint or {})
        def restore(row):
            return PostRef(row["url"], row.get("source_post_id"), row.get("title_hint"),
                           datetime.fromisoformat(row["source_updated_at"]) if row.get("source_updated_at") else None,
                           priority=row.get("priority", 0))
        return DiscoverResult([restore(r) for r in saved["drain_core"]], metadata=saved["drain_metadata"],
                              exploration_refs=[restore(r) for r in saved["drain_exploration"]]), 0, True
    if spec.region_scope == SAVED_SCOPE and getattr(adapter, "profile", {}).get("discovery", {}).get("saved_candidate_limit"):
        with app.db.session() as s:
            return saved_candidates(s, adapter, spec.query, app.clock()), 0, False
    if not getattr(adapter, "persist_discovery_pages", False):
        return adapter.discover(spec, fetcher), 0, False
    with app.db.session() as s:
        saved = dict(s.get(CollectionTask, task_id).checkpoint or {})
    if saved.get("version") == 1:
        refs = [PostRef(url=r["url"], source_post_id=r.get("source_post_id"), title_hint=r.get("title_hint"),
                        source_updated_at=datetime.fromisoformat(r["source_updated_at"]) if r.get("source_updated_at") else None)
                for r in saved["refs"]]
        return DiscoverResult(refs, saved.get("next_cursor"), saved.get("metadata", {})), int(saved.get("next_index", 0)), True
    result = adapter.discover(spec, fetcher)
    with app.db.session() as s:
        s.get(CollectionTask, task_id).checkpoint = {
            "version": 1, "next_index": 0, "next_cursor": result.next_cursor, "metadata": result.metadata,
            "refs": [{"url": r.url, "source_post_id": r.source_post_id, "title_hint": r.title_hint,
                      "source_updated_at": r.source_updated_at.isoformat() if r.source_updated_at else None} for r in result.refs],
        }
    return result, 0, False


def run_discovery(app: AppContext, run: Run, owner: str) -> None:
    source_id = run.source_id or ""
    adapter: Any = app.adapters[source_id]
    _gate(app, source_id)
    scope = RunScope(app, run, owner)
    now = app.clock()
    with app.db.session() as s:
        previous = s.scalar(select(Run).where(Run.source_id == source_id, Run.kind == "discovery",
                            Run.id != run.id, Run.finished_at.is_not(None))
                            .order_by(Run.finished_at.desc()).limit(1))
        current = s.get(Run, run.id)
        current.result = {**(current.result or {}), "collection_timing": {
            "started_at": now.isoformat(),
            "previous_finished_at": previous.finished_at.isoformat() if previous else None,
            "gap_seconds": max(0, (now - previous.finished_at).total_seconds()) if previous else None,
        }}

    if not run.checkpoint.get("cycle"):
        groups = get_setting_session(app, "query_groups")["groups"]
        try:
            plan = adapter.plan_discovery(groups, now)
        except SourceNotReady as exc:
            _set_health(app, source_id, "unknown", exc.message, exc.code)
            raise Halt("failed", exc.code, exc.message) from exc
        with app.db.session() as s:
            plan = append_saved_candidates(s, adapter, prioritize_feed_tasks(s, adapter, plan, now), now)
            existing_units = {u.unit_key: u for u in s.scalars(select(CoverageUnit).where(CoverageUnit.source_id == source_id))}
            revisit = any(t.min_revisit_minutes for t in plan.tasks)
            if revisit:
                # Adaptive micro-watches use exact due times. A five-minute
                # grace would double a hot anchor's intended request volume.
                slack = 0 if getattr(adapter, "profile", {}).get("discovery", {}).get("feed", {}).get("adaptive") else 5
                def due(t: TaskSpec) -> bool:
                    unit = existing_units.get(_unit_key(t.region_scope, t.query_group))
                    return (not t.min_revisit_minutes or unit is None or unit.last_visited_at is None
                            or now - unit.last_visited_at >= timedelta(minutes=t.min_revisit_minutes - slack))
                plan.tasks = [t for t in plan.tasks if due(t)]
            unit_keys = {_unit_key(t.region_scope, t.query_group) for t in plan.tasks}
            cycle = _current_cycle(s, source_id, unit_keys, revisit=revisit)
            for t in plan.tasks:
                key = _unit_key(t.region_scope, t.query_group)
                if key not in existing_units:
                    u = CoverageUnit(source_id=source_id, unit_key=key, region_scope=t.region_scope, region_label=t.region_label, query_group=t.query_group, visits=0)
                    s.add(u)
                    existing_units[key] = u
            s.flush()
            # 이전 실행에서 남은(중단·실패·차단) 같은 주기 작업을 이어받는다
            s.execute(
                update(CollectionTask)
                .where(CollectionTask.source_id == source_id, CollectionTask.scan_cycle == cycle, CollectionTask.state.in_(("pending", "failed", "blocked", "running")),
                       (CollectionTask.region_scope + "|" + CollectionTask.query_group).in_(unit_keys))
                .values(run_id=run.id, state="pending")
            )
            have = {
                _unit_key(r, q)
                for r, q in s.execute(
                    select(CollectionTask.region_scope, CollectionTask.query_group).where(
                        CollectionTask.source_id == source_id, CollectionTask.scan_cycle == cycle, CollectionTask.depth == 0,
                        (CollectionTask.region_scope + "|" + CollectionTask.query_group).in_(unit_keys),
                    )
                )
            }
            adopted = s.scalar(select(func.count()).select_from(CollectionTask).where(CollectionTask.run_id == run.id, CollectionTask.state == "pending")) or 0
            # A tiered plan must fit one watch, or fast units would skip a watch.
            capacity = max(0, int(getattr(adapter, "max_tasks_per_run", MAX_TASKS_PER_RUN)) - adopted)
            # 공정 순환: 가장 오래 방문하지 않은 단위부터 (미방문 우선)
            fresh = [t for t in plan.tasks if _unit_key(t.region_scope, t.query_group) not in have]
            adaptive = getattr(adapter, "profile", {}).get("discovery", {}).get("feed", {}).get("adaptive")
            fresh.sort(key=lambda t: (t.region_scope == SAVED_SCOPE,
                                     existing_units[_unit_key(t.region_scope, t.query_group)].last_visited_at is not None,
                                     t.min_revisit_minutes if adaptive else 0,
                                     existing_units[_unit_key(t.region_scope, t.query_group)].last_visited_at or now, _unit_key(t.region_scope, t.query_group)))
            base_order = (s.scalar(select(func.max(CollectionTask.order_no)).where(CollectionTask.run_id == run.id)) or 0) + 1
            for i, t in enumerate(fresh[:capacity]):
                s.add(
                    CollectionTask(
                        id=new_id("task"),
                        run_id=run.id,
                        source_id=source_id,
                        task_key=_task_key(source_id, t, cycle),
                        query_group=t.query_group,
                        region_scope=t.region_scope,
                        cursor=None,
                        scan_cycle=cycle,
                        depth=0,
                        order_no=(base_order + i) * 1000,
                        state="pending",
                        checkpoint={"feed_failure_count": getattr(adapter, "_regional_failure_counts", {}).get(t.region_scope, 0)},
                    )
                )
            s.flush()
            total = s.scalar(select(func.count()).select_from(CollectionTask).where(CollectionTask.run_id == run.id)) or 0
            r = s.get(Run, run.id)
            assert r is not None
            r.scan_cycle = cycle
            r.progress_total = total
            r.checkpoint = {
                "cycle": cycle,
                "plan": {
                    "priority_anchor_scopes": sorted(getattr(adapter, "_priority_anchor_scopes", set())),
                    "revisit_minutes": getattr(adapter, "_regional_normal_minutes", {t.region_scope: t.min_revisit_minutes for t in plan.tasks}),
                    "units": len(unit_keys),
                    "unit_keys": sorted(unit_keys),
                    "unit_label": plan.unit_label,
                    "region_list_status": plan.region_list_status,
                    "target_label": plan.target_label,
                    "target_units_total": plan.target_units_total,
                    "target_units_covered": plan.target_units_covered,
                    "depth_limit": plan.depth_limit,
                    "notes": plan.notes,
                    "queries": {t.query_group: t.query for t in plan.tasks},
                    "query_labels": plan.query_labels,
                    "labels": {t.region_scope: t.region_label for t in plan.tasks},
                },
            }
            scope.total = total
        run = _reload(app, run.id)

    plan_info = run.checkpoint["plan"]
    cycle = run.checkpoint["cycle"]
    # 이어받기: 강제 종료로 running 에 남은 작업을 다시 처리한다 (중복 키로 멱등)
    with app.db.session() as s:
        s.execute(update(CollectionTask).where(CollectionTask.run_id == run.id, CollectionTask.state == "running").values(state="pending"))
    fetcher = app.fetcher_for(adapter)
    exploration = _Exploration(app, scope, adapter)
    discovery_config = getattr(adapter, "profile", {}).get("discovery", {})
    watch_detail_limit = max(0, int(discovery_config.get("detail_requests_per_watch", 0)))
    # The per-watch allowance grows by each never-fetched candidate up to the
    # cap, so a burst drains in this watch; budget and pacing rules still apply.
    watch_detail_cap = max(watch_detail_limit, int(discovery_config.get("detail_requests_per_watch_max", 0)))
    watch = {"active": False, "known": False, "requests": 0, "refresh_requests": 0, "deferred": 0,
             "limit": watch_detail_limit, "fresh": 0, "audit_requests": 0, "audit_active": False}
    audit_limit = max(0, min(2, int(discovery_config.get("title_audit_requests_per_watch", 0))))
    batch_limit = max(0, int(getattr(adapter, "discovery_request_limit", 0)))
    def reserve_request(s, row) -> None:
        current = s.get(Run, run.id)
        if watch["audit_active"] and int((current.checkpoint or {}).get("title_audit_reserved", 0)) >= audit_limit:
            raise _ExplorationDeferred()
        if watch["active"] and (watch["requests"] >= watch["limit"]
                or (watch["known"] and watch["refresh_requests"] >= 1)
                or row.limit - row.used <= watch_reserve(s, app, source_id, adapter)):
            watch["deferred"] += 1
            raise _ExplorationDeferred()
        exploration.reserve_request(s, row)
        if batch_limit and scope.counts.get("requests", 0) >= batch_limit:
            raise RunHalt("discovery_batch", "이번 원문 확인을 저장했습니다. 남은 페이지와 원문은 이어서 확인합니다.")
        if watch["audit_active"]:
            # Reserve in the same transaction as the shared HTTP budget. A
            # process interruption cannot reset this watch's sampling cap.
            current.checkpoint = {**(current.checkpoint or {}),
                "title_audit_reserved": int((current.checkpoint or {}).get("title_audit_reserved", 0)) + 1}
    def on_request() -> None:
        if watch["active"]:
            watch["requests"] += 1
            if watch["known"]:
                watch["refresh_requests"] += 1
        if watch["audit_active"]:
            watch["audit_requests"] += 1
        exploration.record_request()
        scope.bump("requests")
        scope.checkpoint(check_controls=False)
    fetcher.before_request = scope.checkpoint
    fetcher.on_request = on_request
    fetcher.reserve_request = reserve_request
    stopped: PolicyStop | None = None
    try:
        while True:
            scope.checkpoint()
            with app.db.session() as s:
                task = s.scalar(
                    select(CollectionTask)
                    .where(CollectionTask.run_id == run.id, CollectionTask.state == "pending")
                    .order_by(CollectionTask.order_no, CollectionTask.depth)
                    .limit(1)
                )
                if task is None:
                    break
                task.state = "running"
                task.attempts += 1
                spec = TaskSpec(task.query_group, task.region_scope, plan_info["labels"].get(task.region_scope), plan_info["queries"].get(task.query_group), task.cursor, task.depth)
                task_id, order_no = task.id, task.order_no
            query_label = plan_info.get("query_labels", {}).get(spec.query_group) or spec.query or spec.query_group
            scope.label = f"{spec.region_label or spec.region_scope} · {query_label}" + (f" · {spec.cursor}쪽" if spec.cursor else "")
            result_state, last_result, error, found = "done", "ok", None, 0
            list_succeeded, transient_error = False, False
            try:
                result, start_index, resumed = _discovery_page(app, adapter, spec, fetcher, task_id)
                list_succeeded = not result.metadata.get("detail_drain")
                observe_listings(app, source_id, result)
                if "new_core_candidates" in result.metadata:
                    with app.db.session() as s:
                        saved_task = s.get(CollectionTask, task_id)
                        saved_task.checkpoint = {**(saved_task.checkpoint or {}),
                                                 "new_core_candidates": result.metadata["new_core_candidates"],
                                                  "new_listed_entries": result.metadata.get("new_listed_entries", 0),
                                                  "feed_saturated": result.metadata.get("feed_saturated", False),
                                                 "priority_anchor": spec.region_scope in plan_info.get("priority_anchor_scopes", [])}
                found = len(result.refs)
                last_result = "ok" if found else "empty"
                if result.metadata:
                    with app.db.session() as s:
                        current = s.get(Run, run.id)
                        current.result = {**(current.result or {}), "discovery": {
                            **(current.result or {}).get("discovery", {}), **result.metadata,
                            **({"resumed": resumed or (current.result or {}).get("discovery", {}).get("resumed", False),
                                "batch_request_limit": batch_limit, "continuation_pending": True}
                               if batch_limit else {}),
                        }}
                    if result.metadata.get("invalid_urls"):
                        scope.bump("parse_failures")
                task_label = scope.label
                scope.detail_progress = {"done": start_index, "total": found}
                incremental = bool(watch_detail_limit and (result.listed_refs or result.metadata.get("saved_candidate_backlog") or result.metadata.get("detail_drain")))
                candidates = (ordered_candidates(app, source_id, result) if incremental else
                              [("core", ref, False) for ref in result.refs[start_index:]])
                scope.detail_progress["total"] = len(candidates) + start_index
                if incremental and not result.metadata.get("detail_drain"):
                    watch["fresh"] += sum(1 for _, _, known in candidates if not known)
                    watch["limit"] = min(watch_detail_cap, watch_detail_limit + watch["fresh"])
                if incremental and spec.region_scope.startswith("feed-") and spec.depth == 0 and len(candidates) > 2:
                    # Two promising details can reach automation immediately;
                    # durable remainder runs only after all due public lists.
                    remainder, candidates = candidates[2:], candidates[:2]
                    def pack(ref):
                        return {"url": ref.url, "source_post_id": ref.source_post_id, "title_hint": ref.title_hint,
                                "source_updated_at": ref.source_updated_at.isoformat() if ref.source_updated_at else None,
                                "priority": ref.priority}
                    with app.db.session() as s:
                        parent_task = s.get(CollectionTask, task_id)
                        drain_key = parent_task.task_key + ":detail-drain"
                        if not s.scalar(select(CollectionTask.id).where(CollectionTask.task_key == drain_key)):
                            tail = (s.scalar(select(func.max(CollectionTask.order_no)).where(CollectionTask.run_id == run.id)) or 0) + 1000
                            s.add(CollectionTask(id=new_id("task"), run_id=run.id, source_id=source_id,
                                task_key=drain_key, query_group=spec.query_group, region_scope=spec.region_scope,
                                scan_cycle=cycle, cursor=f"detail-drain:{task_id}", depth=1, order_no=tail, state="pending",
                                checkpoint={"drain_core": [pack(r) for kind, r, _ in remainder if kind == "core"],
                                            "drain_exploration": [pack(r) for kind, r, _ in remainder if kind == "exploration"],
                                            "drain_metadata": {"detail_drain": True, "title_audit_ids": result.metadata.get("title_audit_ids", [])}}))
                            scope.total = (scope.total or 0) + 1
                            s.get(Run, run.id).progress_total = scope.total
                audit_ids = set(result.metadata.get("title_audit_ids", []))
                for index, (candidate_kind, ref, known) in enumerate(candidates, start=start_index):
                    if ref.source_post_id in audit_ids and watch["audit_requests"] >= audit_limit:
                        continue
                    remaining = len(candidates) + start_index - index
                    # Quota exhaustion is not an HTTP attempt. Do not pace each
                    # remaining candidate after the watch has spent its allowance.
                    if incremental and (watch["requests"] >= watch["limit"]
                            or (known and watch["refresh_requests"] >= 1)):
                        watch["deferred"] += remaining
                        break
                    scope.detail_progress["title"] = ref.title_hint or ""
                    scope.label = f"{task_label} · 상세 {index}/{found} · {(ref.title_hint or '')[:60]}"
                    scope.checkpoint()
                    watch["active"], watch["known"] = incremental, known
                    watch["audit_active"] = ref.source_post_id in audit_ids
                    deferred_before = watch["deferred"]
                    try:
                        if candidate_kind == "exploration":
                            exploration.process(adapter, fetcher, [ref], spec)
                        else:
                            _process_ref(app, adapter, fetcher, ref, spec, run.id, scope)
                    except _ExplorationDeferred:
                        watch["deferred"] += max(0, remaining - 1)
                        break  # Saved list metadata is reconsidered by the next watch.
                    finally:
                        watch["active"] = False
                        watch["audit_active"] = False
                    if incremental and watch["deferred"] > deferred_before:
                        # Optional exploration catches the same reservation
                        # rejection internally; end this watch just as promptly.
                        watch["deferred"] += max(0, remaining - 1)
                        break
                    if getattr(adapter, "persist_discovery_pages", False):
                        with app.db.session() as s:
                            t = s.get(CollectionTask, task_id)
                            t.checkpoint = {**t.checkpoint, "next_index": index + 1}
                            current = s.get(Run, run.id)
                            info = dict((current.result or {}).get("discovery", {}))
                            current.result = {**(current.result or {}), "discovery": {
                                **info, "posts_considered": info.get("posts_considered", 0) + 1,
                            }}
                    scope.detail_progress["done"] = index + 1
                scope.label = (f"{task_label} · 이번 감시 후보 확인 {scope.detail_progress['done']}개 · "
                               f"다음 감시 후보 {watch['deferred']}개" if incremental else
                               f"{task_label} · 상세 {found}/{found}")
                if not incremental and (result.exploration_refs or "exploration_candidates" in result.metadata):
                    exploration.process(adapter, fetcher, result.exploration_refs, spec)
                if incremental:
                    with app.db.session() as s:
                        current = s.get(Run, run.id)
                        current.result = {**(current.result or {}), "discovery": {
                            **(current.result or {}).get("discovery", {}),
                            "detail_requests_per_watch": watch["limit"],
                            "watch_detail_requests": watch["requests"],
                            "watch_refresh_requests": watch["refresh_requests"],
                            "watch_deferred_details": watch["deferred"],
                            "title_audit_requests": watch["audit_requests"],
                        }}
                depth_limit = int(plan_info["depth_limit"])
                if result.next_cursor and (depth_limit == 0 or spec.depth + 1 < depth_limit):
                    nxt = TaskSpec(spec.query_group, spec.region_scope, spec.region_label, spec.query, result.next_cursor, spec.depth + 1)
                    with app.db.session() as s:
                        key = _task_key(run.source_id or "", nxt, cycle)
                        if s.scalar(select(CollectionTask.id).where(CollectionTask.task_key == key)) is None:
                            s.add(CollectionTask(id=new_id("task"), run_id=run.id, source_id=run.source_id or "", task_key=key, query_group=nxt.query_group, region_scope=nxt.region_scope, cursor=nxt.cursor, scan_cycle=cycle, depth=nxt.depth, order_no=order_no, state="pending"))
                            scope.total = (scope.total or 0) + 1
                if getattr(adapter, "persist_discovery_pages", False):
                    with app.db.session() as s:
                        current = s.get(Run, run.id)
                        info = dict((current.result or {}).get("discovery", {}))
                        current.result = {**(current.result or {}), "discovery": {
                            **info, "pages_completed": info.get("pages_completed", 0) + 1,
                            "continuation_pending": bool(result.next_cursor),
                        }}
            except ParseError as exc:
                scope.bump("parse_failures")
                result_state, last_result, error = "failed", "failed", f"parse_error: {exc}"
            except TaskFailed as exc:
                scope.bump("fetch_failures")
                result_state, last_result, error = "failed", "failed", f"{exc.code}: {exc.message}"
                transient_error = exc.code in {"network_error", "server_error"}
            except PolicyStop as exc:
                scope.bump("policy_stops")
                result_state, last_result, error = "blocked", "blocked", f"{exc.code}: {exc.message}"
                stopped = exc
            except RunHalt as exc:
                with app.db.session() as s:
                    t = s.get(CollectionTask, task_id)
                    if t:
                        t.state = "pending"
                        t.last_error = f"{exc.code}: {exc.message}"
                if exc.code == "discovery_batch":
                    delay = max(60, int(getattr(adapter, "batch_resume_seconds", 60)))
                    with app.db.session() as s:
                        current = s.get(Run, run.id)
                        current.result = {**(current.result or {}), "continuation_pending": True,
                                          "continuation_delay_seconds": delay}
                    detail_errors = scope.counts.get("fetch_failures", 0) + scope.counts.get("parse_failures", 0)
                    message = (f"요청·해석 오류 {detail_errors}건. " if detail_errors else "") + exc.message
                    _set_health(app, source_id, "degraded" if detail_errors else "ok", message,
                                "detail_errors" if detail_errors else None)
                    scope.checkpoint()
                    raise Halt("partial", message=message,
                               retry_after=app.clock() + timedelta(seconds=delay)) from exc
                _set_health(app, run.source_id or "", "degraded", exc.message, exc.code)
                raise Halt("partial" if scope.done else "failed", exc.code, exc.message, exc.retry_after) from exc
            with app.db.session() as s:
                t = s.get(CollectionTask, task_id)
                if t:
                    t.state = result_state
                    t.updated_at = app.clock()
                    t.last_error = error
                    t.result_count = found
                    if spec.region_scope.startswith("feed-") and spec.depth == 0:
                        cp = dict(t.checkpoint or {})
                        if list_succeeded:
                            cp.update(feed_failure_count=0, feed_transient_retry=False)
                            cp.pop("feed_retry_after", None)
                        else:
                            failures = int(cp.get("feed_failure_count", 0)) + 1
                            quick = transient_error and failures <= 2
                            base_minutes = max(5, int(plan_info.get("revisit_minutes", {}).get(spec.region_scope, 30)))
                            retry_at = app.clock() + timedelta(seconds=60 * failures if quick else base_minutes * 60)
                            cp.update(feed_failure_count=failures, feed_transient_retry=quick,
                                      feed_retry_after=retry_at.isoformat(),
                                      priority_anchor=spec.region_scope in plan_info.get("priority_anchor_scopes", []))
                            if quick:
                                current = s.get(Run, run.id)
                                previous = (current.result or {}).get("regional_retry_after")
                                current.result = {**(current.result or {}), "regional_retry_after": min(previous, retry_at.isoformat()) if previous else retry_at.isoformat()}
                        t.checkpoint = cp
                if spec.depth == 0 or result_state != "done":
                    u = s.get(CoverageUnit, (run.source_id, _unit_key(spec.region_scope, spec.query_group)))
                    if u:
                        if list_succeeded or not spec.region_scope.startswith("feed-"):
                            u.last_visited_at = app.clock()
                        u.last_result = last_result
                        u.last_scan_cycle = cycle
                        u.visits += 1
            scope.done += 1
            if stopped is not None:
                break
    finally:
        fetcher.close()

    scope.checkpoint()
    if stopped is not None:
        _stop_source(app, run.source_id or "", stopped)
        raise Halt("failed" if scope.done <= 1 else "partial", stopped.code, stopped.message)
    with app.db.session() as s:
        states = dict(s.execute(select(CollectionTask.state, func.count()).where(CollectionTask.run_id == run.id).group_by(CollectionTask.state)).all())
    failed = states.get("failed", 0) + states.get("blocked", 0)
    detail_errors = scope.counts.get("fetch_failures", 0) + scope.counts.get("parse_failures", 0)
    has_errors = bool(failed or detail_errors)
    skipped = scope.counts.get("skipped", 0)
    message = f"작업 {failed}건 실패, 요청·해석 오류 {detail_errors}건 — 다음 탐색에서 다시 확인" if has_errors else None
    skip_note = f"숨김·삭제 또는 공개 수집 허용 불가·미확인 공고 {skipped}건은 본문을 저장하지 않았습니다" if skipped else None
    _set_health(app, run.source_id or "", "degraded" if has_errors else "ok", message or skip_note)
    note = None
    if plan_info.get("region_list_status") not in ("verified", "not_applicable") or (plan_info.get("target_units_covered") or 0) < (plan_info.get("target_units_total") or 0):
        note = "탐색 범위가 전국 전체로 확인되지 않았습니다 (부분 탐색)"
    if skip_note:
        note = f"{note} · {skip_note}" if note else skip_note
    with app.db.session() as s:
        current = s.get(Run, run.id)
        deferred = (current.result or {}).get("deferred_details", 0) if current else 0
    if deferred:
        deferred_note = f"이전 조회 결과에 따라 상세 재조회 {deferred}건 유예"
        note = f"{note} · {deferred_note}" if note else deferred_note
    raise Halt("partial" if has_errors else "succeeded", "tasks_failed" if failed else "detail_errors" if detail_errors else None, message if has_errors else note)


def _ref_key(ref: PostRef) -> str:
    identity = f"id:{ref.source_post_id}" if ref.source_post_id else f"url:{canonicalize(ref.url)}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _title_key(title: str | None) -> str:
    return "".join(ch for ch in (title or "").casefold() if ch.isalnum())


def _ref_changed(ref: PostRef, state: DetailFetchState | None, rec: SourceRecord | None) -> bool:
    previous_url = state.url if state else rec.canonical_url if rec else None
    previous_title = state.title_hint if state else rec.title if rec else None
    if previous_url and canonicalize(ref.url) != canonicalize(previous_url):
        return True
    if ref.title_hint and previous_title and _title_key(ref.title_hint) != _title_key(previous_title):
        return True
    if ref.source_updated_at:
        if state and state.source_updated_at:
            return ref.source_updated_at != state.source_updated_at
        if rec and rec.last_checked_at:
            return ref.source_updated_at > rec.last_checked_at
    return False


def _remember_detail(app: AppContext, source_id: str, ref: PostRef, outcome: str) -> datetime | None:
    now = app.clock()
    with app.db.session() as s:
        state = s.get(DetailFetchState, (source_id, _ref_key(ref)))
        failures = (state.failures if state else 0) + 1 if outcome != "ok" else 0
        hours = 24 if outcome in ("http_404", "http_410", "collection_disallowed", "hidden_or_deleted") else 6 if outcome in ("parse_error", "collection_permission_unknown") else min(24, 2 ** min(failures - 1, 5))
        retry_at = now + timedelta(hours=hours) if outcome != "ok" else None
        if state is None:
            state = DetailFetchState(source_id=source_id, ref_key=_ref_key(ref), url=ref.url, outcome=outcome, attempted_at=now)
            s.add(state)
        state.url = ref.url
        if ref.title_hint is not None:
            state.title_hint = ref.title_hint
        if ref.source_updated_at is not None:
            state.source_updated_at = ref.source_updated_at
        state.outcome, state.attempted_at, state.retry_at, state.failures = outcome, now, retry_at, failures
    return retry_at


def _detail_issue(app: AppContext, run_id: str, ref: PostRef, code: str, message: str, http_status: int | None = None, retry_at: datetime | None = None) -> None:
    with app.db.session() as s:
        current = s.get(Run, run_id)
        if current is None:
            return
        result = dict(current.result or {})
        issues = list(result.get("detail_issues", []))
        result["detail_issue_count"] = result.get("detail_issue_count", 0) + 1
        if len(issues) < 50:
            issues.append({"source_post_id": ref.source_post_id, "url": ref.url, "title": ref.title_hint,
                           "code": code, "message": message[:500], "http_status": http_status,
                           "retry_at": retry_at.isoformat() if retry_at else None})
        result["detail_issues"] = issues
        current.result = result


def _process_ref(app: AppContext, adapter: Any, fetcher: Any, ref: PostRef, spec: TaskSpec, run_id: str, scope: RunScope,
                 *, allow_fetch: bool = True) -> bool:
    """True after a known cached result or a detail attempt; False for retry deferral."""
    now = app.clock()
    source_id = adapter.source_id
    canonical = canonicalize(ref.url)
    with app.db.session() as s:
        q = select(SourceRecord).where(SourceRecord.source_id == source_id)
        q = q.where(SourceRecord.source_post_id == ref.source_post_id) if ref.source_post_id else q.where(SourceRecord.canonical_url == canonical)
        rec = s.scalar(q)
        state = s.get(DetailFetchState, (source_id, _ref_key(ref)))
        changed = _ref_changed(ref, state, rec)
        if state and state.retry_at and now < state.retry_at and not changed:
            current = s.get(Run, run_id)
            if current:
                current.result = {**(current.result or {}), "deferred_details": (current.result or {}).get("deferred_details", 0) + 1}
            if rec:
                rec.last_seen_at = now
                record_path(s, rec, (spec.region_scope, spec.region_label, spec.query_group), run_id, now)
            return False
        lead = s.get(Lead, rec.lead_id) if rec and rec.lead_id else None
        refresh_hours = EXCLUDED_REFRESH_HOURS if lead and lead.recommendation == "excluded" else DETAIL_REFRESH_HOURS
        if (rec is not None and rec.parser_version == adapter.parser_version
                and rec.access_status == "accessible" and (state is None or state.outcome == "ok")
                and rec.last_checked_at and now - rec.last_checked_at < timedelta(hours=refresh_hours) and not changed):
            # 최근 상세를 확인한 공고: 요청하지 않고 발견 경로만 기록 (다른 지역에서 같은 공고 발견 포함)
            rec.last_seen_at = now
            record_path(s, rec, (spec.region_scope, spec.region_label, spec.query_group), run_id, now)
            scope.bump("duplicates")
            return True
    if not allow_fetch:
        # Continue accounting for cached/deferred candidates after the cap,
        # without pacing or attempting another network reservation.
        raise _ExplorationDeferred()
    result = adapter.fetch_detail(ref, fetcher)
    if result.outcome != "ok":
        scope.bump("fetch_failures")
        code = f"http_{result.status}" if result.status else result.outcome
        retry_at = _remember_detail(app, source_id, ref, code)
        _detail_issue(app, run_id, ref, code, f"상세 요청 실패 ({result.outcome}, HTTP {result.status or '-'})", result.status, retry_at)
        if rec is not None:
            _record_access(app, rec.id, "error", now, scope=scope)
        return True
    try:
        parsed = adapter.parse(ref, result, now)
    except PostNotCollectable as exc:
        log.info("detail skipped source_id=%s post_id=%s reason=%s", source_id, ref.source_post_id, exc.code)
        scope.bump("skipped")
        retry_at = _remember_detail(app, source_id, ref, exc.code)
        _detail_issue(app, run_id, ref, exc.code, exc.message, result.status, retry_at)
        if rec is not None:
            _record_access(app, rec.id, "blocked", now, collection_reason=exc.code, scope=scope)
        return True
    except ParseError as exc:
        log.warning("detail parse failed source_id=%s post_id=%s error_type=ParseError", source_id, ref.source_post_id)
        scope.bump("parse_failures")
        retry_at = _remember_detail(app, source_id, ref, "parse_error")
        _detail_issue(app, run_id, ref, "parse_error", str(exc), result.status, retry_at)
        if rec is not None:
            _record_access(app, rec.id, "error", now, scope=scope)
        return True
    scope.bump("details_fetched")
    with app.db.session() as s:
        r = ingest(
            s,
            source_id,
            parsed,
            parser_version=adapter.parser_version,
            now=now,
            run_id=run_id,
            path=(spec.region_scope, spec.region_label, spec.query_group),
            provider=app.provider,
        )
        lead = s.get(Lead, r.lead_id)
        if lead is not None and lead.analysis_status == "failed" and r.kind != "unchanged" and not getattr(app.provider, "requires_deferred", False):
            scope.bump("ai_failures")
    # Only acknowledge the discovery change after the record transaction commits.
    _remember_detail(app, source_id, ref, "ok")
    if r.kind == "created":
        scope.bump("created")
        if r.recommendation == "excluded":
            scope.bump("excluded")
    elif r.kind == "updated":
        scope.bump("updated")
    else:
        scope.bump("duplicates")
    app.hub.notify()
    _analyze_after_commit(app, r.lead_id, scope)
    if getattr(app.provider, "requires_deferred", False):
        with app.db.session() as s:
            lead = s.get(Lead, r.lead_id)
            if lead is not None and lead.analysis_status == "failed" and r.kind != "unchanged":
                scope.bump("ai_failures")
    return True


# ── 재확인 ─────────────────────────────────────────────────────────


def _record_access(app: AppContext, rec_id: str, status: str, now: datetime, collection_reason: str | None = None, *, scope: RunScope | None = None) -> None:
    lead_id = None
    with app.db.session() as s:
        rec = s.get(SourceRecord, rec_id)
        if rec:
            rec.access_status = status
            if collection_reason:
                rec.provenance = {**(rec.provenance or {}), "collection_block_reason": collection_reason}
            lead = s.get(Lead, rec.lead_id) if rec.lead_id else None
            if lead:
                lead_id = lead.id
                apply_analysis(s, lead, rec, now=now, provider=app.provider)
                emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["access_status"]})
    if lead_id:
        _analyze_after_commit(app, lead_id, scope)


def _recheck_targets(app: AppContext, run: Run) -> list[str]:
    now = app.clock()
    with app.db.session() as s:
        ttl = ttl_hours(s)
        if run.kind == "recheck_lead" or (run.kind == "recheck_stale" and run.lead_id):
            lead = s.get(Lead, run.lead_id)
            if not lead or not lead.primary_record_id:
                return []
            if run.kind == "recheck_stale":
                if lead.source_id != run.source_id:
                    return []
                rec = s.get(SourceRecord, lead.primary_record_id)
                if not rec:
                    return []
                state = s.get(DetailFetchState, (lead.source_id, _ref_key(PostRef(rec.original_url or rec.canonical_url or "", rec.source_post_id))))
                if state and state.retry_at and now < state.retry_at:
                    return []
            return [lead.primary_record_id]
        recent_cut = now - timedelta(days=RECHECK_RECENT_DAYS)
        q = select(SourceRecord).join(Lead, Lead.primary_record_id == SourceRecord.id).where(
            Lead.source_id == run.source_id,
            Lead.source_status.in_(("open", "unknown")),
            or_(Lead.last_checked_at.is_(None), Lead.last_checked_at < now - timedelta(hours=ttl / 2)),
        )
        if run.kind == "recheck_recent":
            q = q.where(Lead.first_seen_at >= recent_cut, Lead.recommendation.in_(("recommended", "needs_review")))
        else:
            q = q.where(Lead.first_seen_at < recent_cut)
        q = q.order_by(Lead.last_checked_at.is_not(None), Lead.last_checked_at, Lead.id)
        deferred_keys = set(s.scalars(select(DetailFetchState.ref_key).where(
            DetailFetchState.source_id == run.source_id, DetailFetchState.retry_at > now,
        )))
        targets = []
        for rec in s.scalars(q):
            if _ref_key(PostRef(rec.original_url or rec.canonical_url or "", rec.source_post_id)) in deferred_keys:
                continue
            targets.append(rec.id)
            if len(targets) >= RECHECK_BATCH[run.kind]:
                break
        return targets


def run_recheck(app: AppContext, run: Run, owner: str) -> None:
    scope = RunScope(app, run, owner)
    targets = _recheck_targets(app, run)
    if run.kind == "recheck_lead":
        with app.db.session() as s:
            lead = s.get(Lead, run.lead_id)
            source = s.get(Source, lead.source_id) if lead else None
            if lead is None or source is None:
                raise Halt("failed", "not_found", "리드를 찾을 수 없습니다")
            if source.kind == "manual":
                raise Halt("failed", "unsupported", "수동 입력 리드는 원문을 대신 확인하지 않습니다. 원문을 직접 열어 확인하세요.")
            source_id = source.id
    else:
        source_id = run.source_id or ""
    _gate(app, source_id)
    adapter: Any = app.adapters[source_id]
    scope.total = len(targets)
    fetcher = app.fetcher_for(adapter)
    reserved_for_discovery = False
    def on_request() -> None:
        scope.bump("requests")
        scope.checkpoint(check_controls=False)
    fetcher.before_request = scope.checkpoint
    fetcher.on_request = on_request
    def reserve_recheck(session, budget):
        if run.kind != "recheck_lead":
            config = getattr(adapter, "profile", {}).get("discovery", {})
            recheck_limit = int(config.get("recheck_requests_per_run", 0))
            if ((recheck_limit and scope.counts.get("requests", 0) >= recheck_limit)
                    or budget.limit - budget.used <= watch_reserve(session, app, source_id, adapter)):
                raise _DiscoveryReserved()
    fetcher.reserve_request = reserve_recheck
    try:
        for rec_id in targets:
            scope.checkpoint()
            now = app.clock()
            with app.db.session() as s:
                rec = s.get(SourceRecord, rec_id)
                url = rec.original_url if rec else None
                ref = PostRef(url or "", rec.source_post_id if rec else None)
                scope.label = f"재확인 {scope.done}/{len(targets)} · {(rec.title if rec else '')[:60]}"
                state = s.get(DetailFetchState, (source_id, _ref_key(ref))) if url else None
                if run.kind != "recheck_lead" and state and state.retry_at and now < state.retry_at:
                    current = s.get(Run, run.id)
                    if current:
                        current.result = {**(current.result or {}), "deferred_details": (current.result or {}).get("deferred_details", 0) + 1}
                    scope.done += 1
                    continue
            if not url:
                scope.done += 1
                continue
            try:
                rv = adapter.revalidate(url, fetcher, now)
                if rv.parsed is not None and ref.source_post_id and rv.parsed.source_post_id != ref.source_post_id:
                    raise ParseError("재확인 응답의 공고 ID가 요청한 공고와 다릅니다")
            except _DiscoveryReserved:
                reserved_for_discovery = True
                with app.db.session() as s:
                    current = s.get(Run, run.id)
                    current.result = {**(current.result or {}), "discovery_budget_reserved": True,
                                      "rechecks_deferred": len(targets) - scope.done}
                break
            except PolicyStop as exc:
                lead_id = None
                with app.db.session() as s:
                    rec = s.get(SourceRecord, rec_id)
                    if rec:
                        rec.access_status = "blocked" if exc.code != "login_required" else "login_required"
                        lead = s.get(Lead, rec.lead_id) if rec.lead_id else None
                        if lead:
                            lead_id = lead.id
                            apply_analysis(s, lead, rec, now=now, provider=app.provider)
                            emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["access_status"]})
                if lead_id:
                    _analyze_after_commit(app, lead_id, scope)
                scope.bump("policy_stops")
                _stop_source(app, source_id, exc)
                raise Halt("failed" if scope.done == 0 else "partial", exc.code, exc.message) from exc
            except RunHalt as exc:
                raise Halt("partial", exc.code, exc.message, exc.retry_after) from exc
            except PostNotCollectable as exc:
                scope.bump("skipped")
                retry_at = _remember_detail(app, source_id, ref, exc.code)
                _detail_issue(app, run.id, ref, exc.code, exc.message, 200, retry_at)
                _record_access(app, rec_id, "blocked", now, collection_reason=exc.code, scope=scope)
                scope.done += 1
                continue
            except ParseError as exc:
                scope.bump("parse_failures")
                retry_at = _remember_detail(app, source_id, ref, "parse_error")
                _detail_issue(app, run.id, ref, "parse_error", str(exc), retry_at=retry_at)
                _record_access(app, rec_id, "error", now, scope=scope)
                scope.done += 1
                continue
            with app.db.session() as s:
                rec = s.get(SourceRecord, rec_id)
                if rec is None:
                    continue
                if rv.parsed is not None:
                    r = ingest(s, source_id, rv.parsed, parser_version=adapter.parser_version, now=now, run_id=run.id, provider=app.provider)
                    scope.bump("updated" if r.kind == "updated" else "duplicates")
                    scope.bump("details_fetched")
                rec = s.get(SourceRecord, rec_id)
                assert rec is not None
                rec.access_status = rv.access_status
                if rv.access_status == "accessible":
                    rec.last_checked_at = now
                if rv.source_status is not None:
                    rec.source_status = rv.source_status
                lead = s.get(Lead, rec.lead_id) if rec.lead_id else None
                if lead:
                    lead.active_job = None
                    apply_analysis(s, lead, rec, now=now, provider=app.provider)
                    emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["recheck"]})
                lead_id = lead.id if lead else None
            if lead_id:
                _analyze_after_commit(app, lead_id, scope)
                if getattr(app.provider, "requires_deferred", False):
                    with app.db.session() as s:
                        current_lead = s.get(Lead, lead_id)
                        if current_lead and current_lead.analysis_status == "failed":
                            scope.bump("ai_failures")
            if rv.access_status != "accessible":
                scope.bump("fetch_failures")
                code = f"http_{rv.http_status}" if rv.http_status else rv.access_status
                retry_at = _remember_detail(app, source_id, ref, code)
                _detail_issue(app, run.id, ref, code, rv.note or "원문 접근 오류", rv.http_status, retry_at)
            else:
                _remember_detail(app, source_id, ref, "ok")
            scope.done += 1
            app.hub.notify()
    finally:
        fetcher.close()
        if run.lead_id:
            with app.db.session() as s:
                lead = s.get(Lead, run.lead_id)
                if lead:
                    lead.active_job = None
    scope.checkpoint()
    failed = scope.counts.get("fetch_failures", 0) + scope.counts.get("parse_failures", 0)
    if not failed:
        skipped = scope.counts.get("skipped", 0)
        note = f"공개 수집 허용 불가·미확인 공고 {skipped}건은 본문을 저장하지 않았습니다" if skipped else None
        if reserved_for_discovery:
            note = "신규 목록 감시 예산을 남기고 기존 공고 자동 재확인을 유예했습니다"
        raise Halt("succeeded", None, note if targets else "재확인할 대상이 없습니다")
    state = "failed" if failed >= len(targets) else "partial"
    raise Halt(state, "recheck_errors", f"재확인 실패 {failed}건 (접근 오류는 마감으로 바꾸지 않음)")


# ── 수동 입력 ───────────────────────────────────────────────────────


def run_import(app: AppContext, run: Run, owner: str) -> None:
    scope = RunScope(app, run, owner)
    adapter: Any = app.adapters["manual"]
    data = run.input
    now = app.clock()
    try:
        posts = adapter.parse_import(data.get("format", "text"), data.get("content", ""), now, data.get("original_url"))
    except ParseError as exc:
        scope.bump("parse_failures")
        scope.checkpoint()
        raise Halt("failed", "import_invalid", str(exc)) from exc
    scope.total = len(posts)
    lead_ids: list[str] = []
    for p in posts:
        with app.db.session() as s:
            r = ingest(s, "manual", p, parser_version=adapter.parser_version, now=now, run_id=run.id, source_kind="manual", provider=app.provider)
        lead_ids.append(r.lead_id)
        scope.bump({"created": "created", "updated": "updated"}.get(r.kind, "duplicates"))
        if r.kind == "created" and r.recommendation == "excluded":
            scope.bump("excluded")
        _analyze_after_commit(app, r.lead_id, scope)
        if getattr(app.provider, "requires_deferred", False):
            with app.db.session() as s:
                lead = s.get(Lead, r.lead_id)
                if lead and lead.analysis_status == "failed":
                    scope.bump("ai_failures")
        scope.done += 1
        scope.checkpoint()
    with app.db.session() as s:
        r_ = s.get(Run, run.id)
        if r_:
            r_.input = {k: v for k, v in r_.input.items() if k != "content"} | {"content_size": len(data.get("content", ""))}
            r_.result = {"lead_ids": lead_ids}
    raise Halt("succeeded")


# ── 내보내기 ───────────────────────────────────────────────────────

_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> str:
    """CSV 수식 주입 방지: 수식으로 해석될 수 있는 값 앞에 ' 를 붙인다."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIX) else text


EXPORT_FIELDS = [
    "id",
    "title",
    "source_id",
    "recommendation",
    "priority_total",
    "demand_intent",
    "work_mode",
    "collaboration_mode",
    "applicant_scope",
    "pay_unit",
    "pay_min",
    "pay_max",
    "source_status",
    "access_status",
    "last_checked_at",
    "sales_stage",
    "user_mark",
    "memo",
]


def run_export(app: AppContext, run: Run, owner: str) -> None:
    scope = RunScope(app, run, owner)
    kind = run.input.get("kind", "leads_csv")
    stamp = app.clock().strftime("%Y%m%dT%H%M%SZ")
    out_dir: Path = app.config.export_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == "diagnostics":
        suffix = hashlib.sha256(run.id.encode('utf-8')).hexdigest()[:16]
        target = out_dir / f"worklead-diagnostics-{stamp}-{suffix}.zip"
        scope.total = 1
        scope.done = 0
        scope.checkpoint()

        def diagnostic_complete() -> None:
            scope.done = 1
            scope.checkpoint()

        write_diagnostics(app, target, scope.checkpoint, diagnostic_complete)
        rows = None
    else:
        extension = "json" if kind == "leads_json" else "csv"
        target = out_dir / f"worklead-leads-{stamp}-{run.id}.{extension}"
        temporary = target.with_suffix(target.suffix + ".part")
        rows = 0
        try:
            with app.db.session() as s:
                # A read snapshot keeps the total and rows consistent while
                # collection workers continue writing to the WAL database.
                s.connection().exec_driver_sql("BEGIN")
                scope.total = s.scalar(select(func.count()).select_from(Lead)) or 0
                scope.done = 0
                scope.label = f"내보내기 0/{scope.total}건"
                scope.checkpoint()
                encoding = "utf-8" if kind == "leads_json" else "utf-8-sig"
                # Close the streaming cursor before returning its connection
                # even when cancellation interrupts iteration. Otherwise the
                # pooled connection can retain a stale read snapshot and fail
                # the next worker's BEGIN IMMEDIATE with SQLITE_BUSY_SNAPSHOT.
                with s.scalars(select(Lead).order_by(Lead.created_at.desc(), Lead.id.desc())
                               .execution_options(yield_per=500)) as leads, \
                        temporary.open("w", encoding=encoding, newline="") as stream:
                    writer = csv.writer(stream) if kind != "leads_json" else None
                    if writer:
                        writer.writerow(EXPORT_FIELDS)
                    else:
                        stream.write("[\n")
                    for lead in leads:
                        record = {f: getattr(lead, f) for f in EXPORT_FIELDS}
                        if writer:
                            writer.writerow([csv_safe(record[f]) for f in EXPORT_FIELDS])
                        else:
                            if rows:
                                stream.write(",\n")
                            json.dump(record, stream, ensure_ascii=False, default=str)
                        rows += 1
                        if rows % 500 == 0:
                            scope.done = rows
                            scope.label = f"내보내기 {rows}/{scope.total}건"
                            scope.checkpoint()
                    if writer is None:
                        stream.write("\n]\n")
            scope.done = rows
            scope.label = f"내보내기 {rows}/{scope.total}건"
            scope.checkpoint()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    with app.db.session() as s:
        r_ = s.get(Run, run.id)
        if r_:
            r_.result = {"file_name": target.name, "rows": rows}
    raise Halt("succeeded")


# ── 재분석 ─────────────────────────────────────────────────────────


def run_reanalyze(app: AppContext, run: Run, owner: str) -> None:
    scope = RunScope(app, run, owner)
    now = app.clock()
    with app.db.session() as s:
        ids = [run.lead_id] if run.kind == "reanalyze_lead" else list(s.scalars(select(Lead.id)))
    scope.total = len(ids)
    for lead_id in ids:
        scope.checkpoint()
        with app.db.session() as s:
            lead = s.get(Lead, lead_id)
            rec = s.get(SourceRecord, lead.primary_record_id) if lead and lead.primary_record_id else None
            if lead is None or rec is None:
                continue
            src = s.get(Source, lead.source_id)
            retry = bool((run.input or {}).get("retry_ai", False)) if getattr(app.provider, "requires_deferred", False) else run.kind == "reanalyze_lead"
            apply_analysis(s, lead, rec, now=now, provider=app.provider, source_kind=src.kind if src else "site", retry_ai=retry)
            lead.active_job = None
            if lead.analysis_status == "failed" and not getattr(app.provider, "requires_deferred", False):
                scope.bump("ai_failures")
            emit(s, "analysis.completed", {"lead_id": lead.id, "status": lead.analysis_status})
            emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["analysis"]})
        _analyze_after_commit(app, lead_id, scope, retry=retry)
        if getattr(app.provider, "requires_deferred", False):
            with app.db.session() as s:
                current = s.get(Lead, lead_id)
                if current and current.analysis_status == "failed":
                    scope.bump("ai_failures")
        scope.done += 1
        if scope.done % 50 == 0:
            scope.checkpoint()
    scope.checkpoint()
    raise Halt("succeeded")


def _reload(app: AppContext, run_id: str) -> Run:
    with app.db.session() as s:
        run = s.get(Run, run_id)
        assert run is not None
        return run


def get_setting_session(app: AppContext, key: str) -> Any:
    with app.db.session() as s:
        return get_setting(s, key)


EXECUTORS = {
    "discovery": run_discovery,
    "recheck_recent": run_recheck,
    "recheck_stale": run_recheck,
    "recheck_lead": run_recheck,
    "import": run_import,
    "export": run_export,
    "reanalyze_lead": run_reanalyze,
    "reanalyze_all": run_reanalyze,
}


def execute(app: AppContext, run: Run, owner: str) -> None:
    fn = EXECUTORS.get(run.kind)
    try:
        cafe_source = run.source_id == 'daangn_cafe'
        if run.lead_id and run.kind == 'recheck_lead':
            with app.db.session() as s:
                lead = s.get(Lead, run.lead_id)
                cafe_source = cafe_source or bool(lead and lead.source_id == 'daangn_cafe')
        if cafe_source and run.kind in (*runs.COLLECTION_KINDS, 'recheck_lead'):
            raise Halt('failed', 'cafe_browser_queue_required', '카페는 전용 Edge 큐에서 실행합니다. 익명 HTTP 수집을 사용하지 않습니다')
        if fn is None:
            raise Halt("failed", "unknown_kind", f"알 수 없는 작업 종류: {run.kind}")
        fn(app, run, owner)
        raise Halt("succeeded")
    except Halt as h:
        runs.finish(app.db, run.id, h.state, error=(h.code, h.message or "") if h.code else None, retry_after=h.retry_after, note=None if h.code else h.message)
    except Exception as exc:  # noqa: BLE001
        log.exception("run failed run_id=%s kind=%s source_id=%s", run.id, run.kind, run.source_id)
        runs.finish(app.db, run.id, "failed", error=("internal_error", f"{type(exc).__name__}: {exc}"[:500]))
    finally:
        if run.lead_id:
            with app.db.session() as s:
                lead = s.get(Lead, run.lead_id)
                if lead and lead.active_job:
                    lead.active_job = None
                    emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["job_finished"]})
        app.hub.notify()
