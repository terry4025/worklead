"""작업 실행기: 신규 탐색(전국 순환), 재확인, 수동 입력, 내보내기, 재분석."""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, update

from ..config import APP_VERSION, CONTRACT_VERSION
from ..context import AppContext
from ..models import CollectionTask, CoverageUnit, Lead, Run, Source, SourcePolicy, SourceRecord, new_id
from ..services.settings import get_setting, ttl_hours
from ..sources.base import ParseError, PostRef, SourceNotReady, TaskSpec
from ..sources.profiled import TaskFailed, canonicalize
from . import runs
from .events import emit, notify
from .http import PolicyStop, RunHalt
from .pipeline import apply_analysis, ingest, record_path

log = logging.getLogger("worklead.executor")

MAX_TASKS_PER_RUN = 60
DETAIL_REFRESH_HOURS = 12
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

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def checkpoint(self) -> None:
        pause, cancel = runs.heartbeat(
            self.app.db,
            self.run_id,
            self.owner,
            self.app.config.lease_seconds,
            {"counts": dict(self.counts), "progress_done": self.done, "progress_total": self.total, "progress_label": self.label},
        )
        self.app.hub.notify()
        if cancel:
            raise Halt("cancelled", None, "사용자 취소 — 진행 중 요청 종료 후 마지막 저장 지점에서 중단")
        if pause:
            raise Halt("paused", None, "사용자 일시정지")


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


def _current_cycle(s, source_id: str, unit_keys: set[str]) -> int:
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
    if unit_keys and unit_keys <= done_units:
        return cycle + 1
    return cycle


def run_discovery(app: AppContext, run: Run, owner: str) -> None:
    source_id = run.source_id or ""
    adapter: Any = app.adapters[source_id]
    _gate(app, source_id)
    scope = RunScope(app, run, owner)
    now = app.clock()

    if not run.checkpoint.get("cycle"):
        groups = get_setting_session(app, "query_groups")["groups"]
        try:
            plan = adapter.plan_discovery(groups, now)
        except SourceNotReady as exc:
            _set_health(app, source_id, "unknown", exc.message, exc.code)
            raise Halt("failed", exc.code, exc.message) from exc
        with app.db.session() as s:
            unit_keys = {_unit_key(t.region_scope, t.query_group) for t in plan.tasks}
            cycle = _current_cycle(s, source_id, unit_keys)
            existing_units = {u.unit_key: u for u in s.scalars(select(CoverageUnit).where(CoverageUnit.source_id == source_id))}
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
                .where(CollectionTask.source_id == source_id, CollectionTask.scan_cycle == cycle, CollectionTask.state.in_(("pending", "failed", "blocked", "running")))
                .values(run_id=run.id, state="pending")
            )
            have = {
                _unit_key(r, q)
                for r, q in s.execute(
                    select(CollectionTask.region_scope, CollectionTask.query_group).where(
                        CollectionTask.source_id == source_id, CollectionTask.scan_cycle == cycle, CollectionTask.depth == 0
                    )
                )
            }
            adopted = s.scalar(select(func.count()).select_from(CollectionTask).where(CollectionTask.run_id == run.id, CollectionTask.state == "pending")) or 0
            capacity = max(0, MAX_TASKS_PER_RUN - adopted)
            # 공정 순환: 가장 오래 방문하지 않은 단위부터 (미방문 우선)
            fresh = [t for t in plan.tasks if _unit_key(t.region_scope, t.query_group) not in have]
            fresh.sort(key=lambda t: (existing_units[_unit_key(t.region_scope, t.query_group)].last_visited_at is not None, existing_units[_unit_key(t.region_scope, t.query_group)].last_visited_at or now, _unit_key(t.region_scope, t.query_group)))
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
                    "units": len(unit_keys),
                    "region_list_status": plan.region_list_status,
                    "target_label": plan.target_label,
                    "target_units_total": plan.target_units_total,
                    "target_units_covered": plan.target_units_covered,
                    "depth_limit": plan.depth_limit,
                    "notes": plan.notes,
                    "queries": {t.query_group: t.query for t in plan.tasks},
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
    fetcher.on_request = lambda: scope.bump("requests")
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
            scope.label = f"{spec.region_label or spec.region_scope} · {spec.query or spec.query_group}" + (f" · {spec.cursor}쪽" if spec.cursor else "")
            result_state, last_result, error, found = "done", "ok", None, 0
            try:
                result = adapter.discover(spec, fetcher)
                found = len(result.refs)
                last_result = "ok" if found else "empty"
                for ref in result.refs:
                    _process_ref(app, adapter, fetcher, ref, spec, run.id, scope)
                if result.next_cursor and spec.depth + 1 < int(plan_info["depth_limit"]):
                    nxt = TaskSpec(spec.query_group, spec.region_scope, spec.region_label, spec.query, result.next_cursor, spec.depth + 1)
                    with app.db.session() as s:
                        key = _task_key(run.source_id or "", nxt, cycle)
                        if s.scalar(select(CollectionTask.id).where(CollectionTask.task_key == key)) is None:
                            s.add(CollectionTask(id=new_id("task"), run_id=run.id, source_id=run.source_id or "", task_key=key, query_group=nxt.query_group, region_scope=nxt.region_scope, cursor=nxt.cursor, scan_cycle=cycle, depth=nxt.depth, order_no=order_no, state="pending"))
                            scope.total = (scope.total or 0) + 1
            except ParseError as exc:
                scope.bump("parse_failures")
                result_state, last_result, error = "failed", "failed", f"parse_error: {exc}"
            except TaskFailed as exc:
                scope.bump("fetch_failures")
                result_state, last_result, error = "failed", "failed", f"{exc.code}: {exc.message}"
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
                _set_health(app, run.source_id or "", "degraded", exc.message, exc.code)
                raise Halt("partial" if scope.done else "failed", exc.code, exc.message, exc.retry_after) from exc
            with app.db.session() as s:
                t = s.get(CollectionTask, task_id)
                if t:
                    t.state = result_state
                    t.last_error = error
                    t.result_count = found
                if spec.depth == 0 or result_state != "done":
                    u = s.get(CoverageUnit, (run.source_id, _unit_key(spec.region_scope, spec.query_group)))
                    if u:
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
    _set_health(app, run.source_id or "", "ok" if not failed else "degraded", None if not failed else f"작업 {failed}건 실패 — 다음 실행에서 다시 시도")
    note = None
    if plan_info.get("region_list_status") != "verified" or (plan_info.get("target_units_covered") or 0) < (plan_info.get("target_units_total") or 0):
        note = "탐색 범위가 전국 전체로 확인되지 않았습니다 (부분 탐색)"
    raise Halt("partial" if failed else "succeeded", "tasks_failed" if failed else None, f"작업 {failed}건 실패" if failed else note)


def _process_ref(app: AppContext, adapter: Any, fetcher: Any, ref: PostRef, spec: TaskSpec, run_id: str, scope: RunScope) -> None:
    now = app.clock()
    source_id = adapter.source_id
    canonical = canonicalize(ref.url)
    with app.db.session() as s:
        q = select(SourceRecord).where(SourceRecord.source_id == source_id)
        q = q.where(SourceRecord.source_post_id == ref.source_post_id) if ref.source_post_id else q.where(SourceRecord.canonical_url == canonical)
        rec = s.scalar(q)
        if rec is not None and rec.last_checked_at and now - rec.last_checked_at < timedelta(hours=DETAIL_REFRESH_HOURS):
            # 최근 상세를 확인한 공고: 요청하지 않고 발견 경로만 기록 (다른 지역에서 같은 공고 발견 포함)
            rec.last_seen_at = now
            record_path(s, rec, (spec.region_scope, spec.region_label, spec.query_group), run_id, now)
            scope.bump("duplicates")
            return
    result = adapter.fetch_detail(ref, fetcher)
    if result.outcome != "ok":
        scope.bump("fetch_failures")
        return
    try:
        parsed = adapter.parse(ref, result, now)
    except ParseError:
        scope.bump("parse_failures")
        return
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
        if lead is not None and lead.analysis_status == "failed" and r.kind != "unchanged":
            scope.bump("ai_failures")
    if r.kind == "created":
        scope.bump("created")
        if r.recommendation == "excluded":
            scope.bump("excluded")
    elif r.kind == "updated":
        scope.bump("updated")
    else:
        scope.bump("duplicates")
    app.hub.notify()


# ── 재확인 ─────────────────────────────────────────────────────────


def _recheck_targets(app: AppContext, run: Run) -> list[str]:
    now = app.clock()
    with app.db.session() as s:
        ttl = ttl_hours(s)
        if run.kind == "recheck_lead":
            lead = s.get(Lead, run.lead_id)
            return [lead.primary_record_id] if lead and lead.primary_record_id else []
        recent_cut = now - timedelta(days=RECHECK_RECENT_DAYS)
        q = select(Lead.primary_record_id).where(
            Lead.source_id == run.source_id,
            Lead.source_status.in_(("open", "unknown")),
            or_(Lead.last_checked_at.is_(None), Lead.last_checked_at < now - timedelta(hours=ttl / 2)),
        )
        if run.kind == "recheck_recent":
            q = q.where(Lead.first_seen_at >= recent_cut, Lead.recommendation.in_(("recommended", "needs_review")))
        else:
            q = q.where(Lead.first_seen_at < recent_cut)
        q = q.order_by(Lead.last_checked_at.is_not(None), Lead.last_checked_at).limit(RECHECK_BATCH[run.kind])
        return [rid for rid in s.scalars(q) if rid]


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
    fetcher.on_request = lambda: scope.bump("requests")
    try:
        for rec_id in targets:
            scope.checkpoint()
            now = app.clock()
            with app.db.session() as s:
                rec = s.get(SourceRecord, rec_id)
                url = rec.original_url if rec else None
            if not url:
                scope.done += 1
                continue
            try:
                rv = adapter.revalidate(url, fetcher, now)
            except PolicyStop as exc:
                with app.db.session() as s:
                    rec = s.get(SourceRecord, rec_id)
                    if rec:
                        rec.access_status = "blocked" if exc.code != "login_required" else "login_required"
                        lead = s.get(Lead, rec.lead_id) if rec.lead_id else None
                        if lead:
                            apply_analysis(s, lead, rec, now=now, provider=app.provider)
                            emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["access_status"]})
                scope.bump("policy_stops")
                _stop_source(app, source_id, exc)
                raise Halt("failed" if scope.done == 0 else "partial", exc.code, exc.message) from exc
            except RunHalt as exc:
                raise Halt("partial", exc.code, exc.message, exc.retry_after) from exc
            except ParseError:
                scope.bump("parse_failures")
                with app.db.session() as s:
                    rec = s.get(SourceRecord, rec_id)
                    if rec:
                        rec.access_status = "error"
                        lead = s.get(Lead, rec.lead_id) if rec.lead_id else None
                        if lead:
                            apply_analysis(s, lead, rec, now=now, provider=app.provider)
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
            if rv.access_status != "accessible":
                scope.bump("fetch_failures")
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
        raise Halt("succeeded", None, None if targets else "재확인할 대상이 없습니다")
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
        target = out_dir / f"worklead-diagnostics-{stamp}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("versions.json", json.dumps({"app": APP_VERSION, "contract": CONTRACT_VERSION}, ensure_ascii=False, indent=2))
            with app.db.session() as s:
                settings = {k: get_setting(s, k) for k in ("recheck", "notifications", "ai", "retention", "query_groups")}
                sources = [
                    {"id": x.id, "health": x.health_status, "code": x.health_code, "stopped": bool(x.stopped_reason)} for x in s.scalars(select(Source))
                ]
                recent = [
                    {"id": r.id, "kind": r.kind, "state": r.state, "error": r.error_code, "counts": r.counts}
                    for r in s.scalars(select(Run).order_by(Run.created_at.desc()).limit(50))
                ]
            z.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=2, default=str))
            z.writestr("sources.json", json.dumps(sources, ensure_ascii=False, indent=2))
            z.writestr("runs.json", json.dumps(recent, ensure_ascii=False, indent=2, default=str))
            for log_file in sorted(app.config.log_dir.glob("*.log*"))[:10]:
                z.write(log_file, f"logs/{log_file.name}")
        rows = None
    else:
        with app.db.session() as s:
            leads = s.scalars(select(Lead).order_by(Lead.created_at.desc()).limit(10000)).all()
            records = [{f: getattr(lead, f) for f in EXPORT_FIELDS} for lead in leads]
        rows = len(records)
        if kind == "leads_json":
            target = out_dir / f"worklead-leads-{stamp}.json"
            target.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        else:
            target = out_dir / f"worklead-leads-{stamp}.csv"
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(EXPORT_FIELDS)
            for r in records:
                w.writerow([csv_safe(r[f]) for f in EXPORT_FIELDS])
            target.write_text("﻿" + buf.getvalue(), encoding="utf-8")
    with app.db.session() as s:
        r_ = s.get(Run, run.id)
        if r_:
            r_.result = {"file_name": target.name, "rows": rows}
    scope.done = 1
    scope.total = 1
    scope.checkpoint()
    raise Halt("succeeded")


# ── 재분석 ─────────────────────────────────────────────────────────


def run_reanalyze(app: AppContext, run: Run, owner: str) -> None:
    scope = RunScope(app, run, owner)
    now = app.clock()
    with app.db.session() as s:
        ids = [run.lead_id] if run.kind == "reanalyze_lead" else list(s.scalars(select(Lead.id)))
    scope.total = len(ids)
    for lead_id in ids:
        with app.db.session() as s:
            lead = s.get(Lead, lead_id)
            rec = s.get(SourceRecord, lead.primary_record_id) if lead and lead.primary_record_id else None
            if lead is None or rec is None:
                continue
            src = s.get(Source, lead.source_id)
            apply_analysis(s, lead, rec, now=now, provider=app.provider, source_kind=src.kind if src else "site", retry_ai=run.kind == "reanalyze_lead")
            lead.active_job = None
            if lead.analysis_status == "failed":
                scope.bump("ai_failures")
            emit(s, "analysis.completed", {"lead_id": lead.id, "status": lead.analysis_status})
            emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["analysis"]})
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
