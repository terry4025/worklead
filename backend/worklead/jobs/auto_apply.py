"""One-shot UI submission permits. No remote submission or browser credentials."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import timedelta, timezone
from urllib.parse import unquote, urlsplit

from sqlalchemy import func, or_, select, text

from ..analysis.service import analyze
from ..db import MaintenanceBusy
from ..engine.events import emit
from ..engine.pipeline import feedback_map, post_input_from_record
from ..models import AppSetting, DetailFetchState, JobAutoAttempt, Lead, LeadActivity, Run, Source, SourcePolicy, SourceRecord, new_id
from ..services.settings import get_profile, ttl_hours
from .applications import ApplicationError
from .auto_apply_models import AutoApplyStartIn, AutoApplyStatus, AutoAttemptOut, AutoCandidateOut

LEASE_SECONDS = 90
PERMIT_SECONDS = 3
CONNECTED_SECONDS = 90
RECEIPT_SECONDS = 15 * 60
RETRY_DELAYS = (60, 300, 900)
TRANSIENT_REASONS = frozenset(("navigation_failed", "browser_timeout", "connection_ended", "lease_expired"))
DIAGNOSTIC_LIMIT = 100
DIAGNOSTIC_SECONDS = 15
PREPARE_SECONDS = 30
PREPARE_LIMIT = 100


def job_identity(rec):
    try:
        url = urlsplit(rec.original_url or "")
        path = unquote(url.path)
        match = re.fullmatch(r"/job-posts/(?:[^/?#]+-)?([A-Za-z0-9]{12})/?", path)
        if (url.scheme != "https" or url.netloc != "jobs.daangn.com" or not match
                or url.query or url.fragment or (rec.source_post_id and rec.source_post_id != match[1])):
            return None
        return hashlib.sha256(("daangn-alba:" + match[1]).encode()).hexdigest()
    except (ValueError, TypeError):
        return None


def condition(s, lead, now, min_score, *, queue=False, visible_form=False):
    if (lead.source_id != "daangn-alba" or (lead.priority_total or 0) < min_score
            or lead.recommendation not in ("recommended", "needs_review")
            or lead.demand_intent not in ("buyer_project", "buyer_ongoing")
            or lead.engagement_type != "project" or lead.pay_unit != "project" or lead.sales_stage not in ("new", "reviewing")
            or lead.user_mark == "dismissed" or lead.source_status != "open" or lead.access_status != "accessible"):
        return "condition_changed"
    rec = s.get(SourceRecord, lead.primary_record_id)
    if not rec or not rec.body_text or rec.source_status != "open" or rec.access_status != "accessible" or not job_identity(rec):
        return "source_unverified"
    if rec.published_precision != "exact" or rec.published_at is None or rec.published_at > now:
        return "source_stale"
    fresh = rec.last_checked_at is not None and now - timedelta(hours=ttl_hours(s)) <= rec.last_checked_at <= now
    if not fresh and not queue:
        return "source_stale"
    if not queue and rec.published_at < now - timedelta(hours=24) and not visible_form:
        return "source_stale"
    profile = get_profile(s)
    post = post_input_from_record(rec, "site")
    # An open form must not replace a fresh public detail check: old terms/pay
    # may have changed even when the same title and application button remain.
    current = analyze(post, profile, now, ttl_hours(s), feedback_map(s, lead.id))
    if (current.recommendation == "excluded" or not current.dev_relevant or current.pay.unit != "project"
            or current.total is None or (not queue and current.total < min_score)):
        return "condition_changed"
    if profile.min_contract is not None and rec.pay_min is not None and rec.pay_min < profile.min_contract:
        return "condition_conflict"
    if re.search(r"(?:사업자(?:등록|등록증)?|세금\s*계산서).{0,16}(?:필수|반드시|가능한\s*분|가능하신\s*분|가능해야|발행\s*요청)", rec.body_text):
        return "condition_conflict"
    return None


class AutoApplyRuntime:
    def __init__(self, app, *, monotonic=time.monotonic):
        self.app = app
        self.monotonic = monotonic
        self._lock = threading.RLock()
        self._token = ""
        self._url = ""
        self._server = self._thread = None
        self._stop = threading.Event()
        self._status = AutoApplyStatus()
        self._account = self._profile = None
        self._leases = {}
        self._receipts = {}
        self._generation = app.db.gate.generation
        self._connected = None
        self._refresh_after = 0
        self._prepare_after = 0
        self._prepare_offset = 0
        self._started = None
        self._diagnostic_cache = None
        self._journal = app.config.data_dir / "auto-apply-submit.journal"
        self._recover()

    def _recover(self, reason="connection_ended"):
        with self.app.db.session() as s:
            for row in s.scalars(select(JobAutoAttempt).where(JobAutoAttempt.state.in_(("claimed", "ready", "submitting")))):
                self._finish(s, row, self._status.error_code if self._status.state == "paused" else reason)
            # 0008 held rows predate retry metadata. Adopt only an unambiguous
            # pre-permit transient, once; never move an already scheduled due time.
            legacy = list(s.scalars(select(JobAutoAttempt).where(JobAutoAttempt.state == "held",
                JobAutoAttempt.reason_code.in_(TRANSIENT_REASONS), JobAutoAttempt.retry_count == 0,
                JobAutoAttempt.retry_after.is_(None), JobAutoAttempt.permitted_at.is_(None))))
            if legacy:
                journal = self._journal_keys()
                for row in legacy:
                    if row.job_key not in journal:
                        row.retry_after = self.app.clock() + timedelta(seconds=RETRY_DELAYS[0])
                        self._leases.pop(row.id, None); self._receipts.pop(row.id, None)
                        self._diagnostic_cache = None

    def _finish(self, s, row, reason):
        """Retire authority first; only demonstrably pre-permit failures may retry."""
        was_submitting = row.state == "submitting"
        permitted = row.permitted_at is not None or row.job_key in self._journal_keys()
        row.state = "uncertain" if was_submitting or permitted else "held"
        row.reason_code, row.finished_at, row.expires_at = reason, self.app.clock(), None
        row.retry_after = None
        self._leases.pop(row.id, None)
        if not permitted and not was_submitting:
            self._receipts.pop(row.id, None)
            if reason in TRANSIENT_REASONS:
                if 0 <= (row.retry_count or 0) < len(RETRY_DELAYS):
                    row.retry_after = self.app.clock() + timedelta(seconds=RETRY_DELAYS[row.retry_count or 0])
                else:
                    row.reason_code = "retry_exhausted"
        self._diagnostic_cache = None

    def _journal_entries(self):
        if self._journal.is_symlink():
            raise ApplicationError(503, "journal_unavailable", "자동 지원 기록 경로를 확인하세요")
        if not self._journal.exists():
            return []
        # A corrupted journal stops permits, rather than silently forgetting a send.
        lines = self._journal.read_text(encoding="ascii").splitlines()
        if any(not re.fullmatch(r"\d{4}-\d{2}-\d{2} [a-f0-9]{64}", line) for line in lines):
            raise ApplicationError(503, "journal_unavailable", "자동 지원 기록을 확인할 수 없어 중지합니다")
        return [line.split(" ", 1) for line in lines]

    def _journal_keys(self):
        return {key for day, key in self._journal_entries()}

    def _remember_permit(self, key):
        self._journal.parent.mkdir(parents=True, exist_ok=True)
        if self._journal.is_symlink():
            raise ApplicationError(503, "journal_unavailable", "자동 지원 기록 경로를 확인하세요")
        with self._journal.open("a", encoding="ascii", newline="\n") as stream:
            day = self._day_start(self.app.clock()).date().isoformat()
            stream.write(day + " " + key + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _day_start(self, now):
        local = now.astimezone(timezone(timedelta(hours=9)))
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    def _expire(self, s):
        now = self.app.clock()
        for row in s.scalars(select(JobAutoAttempt).where(JobAutoAttempt.state.in_(("claimed", "ready", "submitting")), JobAutoAttempt.expires_at <= now)):
            self._finish(s, row, self._status.error_code if self._status.state == "paused" else "lease_expired")

    def _status_in(self, s):
        self._expire(s)
        self._receipts = {key: value for key, value in self._receipts.items() if value[3] > self.monotonic()}
        rows = list(s.scalars(select(JobAutoAttempt).order_by(JobAutoAttempt.created_at.desc(), JobAutoAttempt.id.desc())))
        today = [row for row in rows if row.permitted_at and row.permitted_at >= self._day_start(self.app.clock())]
        result = self._status.model_copy(deep=True)
        result.today_submitted = sum(row.state == "submitted" for row in today)
        journal_count = sum(day == self._day_start(self.app.clock()).date().isoformat() for day, key in self._journal_entries())
        result.today_reserved = max(sum(row.state != "submitted" for row in today), journal_count - result.today_submitted)
        result.held = sum(row.state == "held" for row in rows)
        result.uncertain = sum(row.state == "uncertain" for row in rows)
        result.retry_waiting = sum(row.state == "held" and row.retry_after is not None for row in rows)
        result.attempts = [AutoAttemptOut(id=row.id, lead_id=row.lead_id, title=(s.get(Lead, row.lead_id).title), state=row.state,
            reason_code=row.reason_code, created_at=row.created_at, finished_at=row.finished_at,
            retry_count=row.retry_count or 0, retry_after=row.retry_after) for row in rows[:30]]
        if result.enabled and self._connected is not None and self.monotonic() - self._connected >= CONNECTED_SECONDS:
            result.state, result.message = "waiting", "크롬 연결이 늦어지고 있습니다. 탭과 확장 연결을 확인하세요"
        result.candidates = self._diagnostics(s, rows)
        result.queue_reason, result.needs_user_action = self._queue_reason(s, result)
        messages = {"connection_wait": "크롬 작업 요청을 기다립니다. 확장 연결과 별도 당근 탭을 확인하세요",
            "connection_stale": "크롬 작업 요청이 90초 이상 없습니다. 연결과 전용 탭을 확인하세요",
            "daily_limit": "오늘 제출 상한에 도달했습니다", "processing": "공고의 지원 양식 또는 제출 결과를 확인 중입니다",
            "source_permission_unverified": "당근 소스 허용 상태 확인이 필요합니다", "source_refresh": "저장 공고의 원문 재확인 상태를 기다립니다",
            "retry_wait": "제출 전 일시 실패를 제한된 횟수로 재시도합니다", "no_candidates": "현재 조건에 맞는 미지원 공고가 없습니다",
            "ready": "조건에 맞는 공고를 연결한 크롬에 전달할 준비가 됐습니다"}
        if result.queue_reason in messages: result.message = messages[result.queue_reason]
        return result

    def _diagnostics(self, s, rows):
        now, tick = self.app.clock(), self.monotonic()
        signature = (self._status.min_score, self.app.db.gate.generation,
            tuple((r.id, r.state, r.reason_code, r.retry_count, r.retry_after) for r in rows))
        cached = self._diagnostic_cache
        if cached and cached[0] == signature and tick - cached[1] < DIAGNOSTIC_SECONDS and abs((now-cached[2]).total_seconds()) < DIAGNOSTIC_SECONDS:
            return [item.model_copy(deep=True) for item in cached[3]]
        # Never scan or analyze the entire national lead store on a two-second poll.
        leads = list(s.scalars(select(Lead).where(Lead.source_id == "daangn-alba",
            Lead.priority_total >= self._status.min_score, Lead.sales_stage.in_(("new", "reviewing")))
            .order_by(Lead.published_at.desc(), Lead.first_seen_at.desc()).limit(DIAGNOSTIC_LIMIT)))
        by_lead = {row.lead_id: row for row in reversed(rows)}
        lead_map = {lead.id: lead for lead in leads}
        for row in rows[:30]:
            if row.lead_id not in lead_map:
                lead = s.get(Lead, row.lead_id)
                if lead: leads.append(lead); lead_map[lead.id] = lead
        ids = [lead.id for lead in leads]
        records = {row.id: row for row in s.scalars(select(SourceRecord).where(SourceRecord.id.in_(
            [lead.primary_record_id for lead in leads if lead.primary_record_id])))} if ids else {}
        refreshes = {}
        if ids:
            ranked = select(Run.id, func.row_number().over(partition_by=Run.lead_id,
                order_by=(Run.created_at.desc(), Run.id.desc())).label("position")).where(
                Run.kind.in_(("recheck_stale", "recheck_lead", "recheck_recent")), Run.lead_id.in_(ids)).subquery()
            refreshes = {run.lead_id: run for run in s.scalars(select(Run).join(ranked, Run.id == ranked.c.id).where(ranked.c.position == 1))}
        policy = s.get(SourcePolicy, "daangn-alba")
        journal = self._journal_keys()
        candidates = []
        for lead in leads:
            rec, row = records.get(lead.primary_record_id), by_lead.get(lead.id)
            item = AutoCandidateOut(lead_id=lead.id, title=lead.title,
                stage="queued", last_checked_at=rec.last_checked_at if rec else None,
                updated_at=row.finished_at or row.created_at if row else lead.last_checked_at or lead.first_seen_at,
                retry_count=(row.retry_count or 0) if row else 0, retry_after=row.retry_after if row else None)
            run = refreshes.get(lead.id)
            if run:
                item.refresh_state, item.refresh_error_code = run.state, run.error_code
            stale = rec and not (rec.last_checked_at is not None and now-timedelta(hours=ttl_hours(s)) <= rec.last_checked_at <= now)
            if row:
                item.stage = {"claimed":"form_review", "ready":"form_review", "submitting":"permit_issued",
                    "submitted":"submitted", "already_applied":"already_applied", "uncertain":"uncertain"}.get(row.state, "held")
                item.reason_code = row.reason_code
                if row.state == "held" and row.retry_after is not None:
                    item.stage = "retry_wait"
                    if row.retry_after <= now and stale:
                        item.stage, item.reason_code = "source_refresh", "source_refresh_due"
            else:
                key = job_identity(rec) if rec else None
                reason = "source_permission_unverified" if not policy or policy.status != "allowed" else condition(s, lead, now, self._status.min_score, queue=True)
                if key in journal:
                    item.stage, item.reason_code = "uncertain", "previous_permit"
                elif reason:
                    item.stage, item.reason_code = "rejected", reason
                elif stale:
                    item.stage, item.reason_code = "source_refresh", "source_refresh_due"
            if item.stage == "source_refresh":
                if run: item.reason_code = "refresh_" + run.state; item.updated_at = run.finished_at or run.started_at or run.created_at
                from ..engine.executors import _ref_key
                from ..sources.base import PostRef
                detail = s.get(DetailFetchState, (rec.source_id, _ref_key(PostRef(rec.original_url, rec.source_post_id))))
                if detail and detail.retry_at and detail.retry_at > now:
                    item.reason_code, item.retry_after = "refresh_backoff", detail.retry_at
                if detail and detail.outcome in ("collection_disallowed", "collection_permission_unknown"):
                    item.reason_code = "detail_permission_blocked"
            candidates.append(item)
        # Active work, retry and source waits remain visible alongside newest candidates.
        order = {"permit_issued":0, "form_review":1, "retry_wait":2, "source_refresh":3, "queued":4,
            "held":5, "uncertain":5, "rejected":6, "submitted":7, "already_applied":7}
        candidates.sort(key=lambda item: order[item.stage])
        result = candidates[:30]
        self._diagnostic_cache = (signature, tick, now, result)
        return [item.model_copy(deep=True) for item in result]

    def _queue_reason(self, s, result):
        if not result.enabled: return "stopped", False
        if result.state == "paused": return "paused", True
        if self._connected is None:
            overdue = self._started is not None and self.monotonic()-self._started >= CONNECTED_SECONDS
            return ("connection_stale", True) if overdue else ("connection_wait", False)
        if self.monotonic()-self._connected >= CONNECTED_SECONDS: return "connection_stale", True
        if any(row.state in ("claimed", "ready", "submitting") for row in result.attempts): return "processing", False
        if result.today_submitted + result.today_reserved >= result.daily_limit: return "daily_limit", False
        policy = s.get(SourcePolicy, "daangn-alba")
        if not policy or policy.status != "allowed": return "source_permission_unverified", True
        if any(item.stage == "queued" for item in result.candidates): return "ready", False
        if any(item.stage == "source_refresh" for item in result.candidates):
            delayed = any(item.stage == "source_refresh" and (item.reason_code in ("refresh_failed", "refresh_cancelled", "detail_permission_blocked") or
                item.updated_at and self.app.clock()-item.updated_at >= timedelta(minutes=15)) for item in result.candidates)
            return "source_refresh", delayed
        if result.retry_waiting: return "retry_wait", False
        return "no_candidates", any(item.stage in ("held", "uncertain", "rejected") for item in result.candidates)

    def status(self):
        with self._lock, self.app.db.session() as s:
            if not self._status.enabled:
                setting = s.get(AppSetting, "job_auto_apply")
                if setting:
                    self._status.min_score = setting.value.get("min_score", 50)
                    self._status.daily_limit = setting.value.get("daily_limit", 20)
            return self._status_in(s)

    def start(self, body: AutoApplyStartIn):
        if body.remember_connection:
            from .auto_pairing import runtime as pairing_runtime
            return pairing_runtime(self.app).start(body)
        # Explicitly opting out must not leave a previous remembered grant armed.
        if self.app._jobs_auto_pairing_runtime is not None:
            self.app._jobs_auto_pairing_runtime.disable()
        return self._start_session(body)

    def _start_session(self, body: AutoApplyStartIn, *, pair_state=None):
        from .auto_apply_bridge import BridgeServer, Handler
        self.shutdown()
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise ApplicationError(409, "auto_apply_stopping", "이전 연결을 정리 중입니다. 잠시 후 다시 시작하세요")
            with self.app.db.session() as s:
                policy = s.get(SourcePolicy, "daangn-alba")
                if not policy or policy.status != "allowed":
                    raise ApplicationError(409, "source_permission_unverified", "당근 소스의 수집 허용 설정을 확인하세요")
                setting = s.get(AppSetting, "job_auto_apply")
                values = {"min_score": body.min_score, "daily_limit": body.daily_limit}
                if setting: setting.value = values
                else: s.add(AppSetting(key="job_auto_apply", value=values))
                self._journal_keys()
            self._stop.clear()
            self._account = pair_state["account"] if pair_state else None
            self._profile = pair_state["profile"] if pair_state else None
            self._leases.clear()
            self._receipts.clear()
            self._connected = None
            self._started = self.monotonic()
            self._diagnostic_cache = None
            self._refresh_after = 0
            self._prepare_after = 0
            self._prepare_offset = 0
            self._generation = self.app.db.gate.generation
            self._server = BridgeServer(("127.0.0.1", 0), Handler)
            self._server.runtime = self
            self._token = secrets.token_urlsafe(32)
            self._url = f"http://127.0.0.1:{self._server.server_port}"
            self._status = AutoApplyStatus(enabled=True, state="waiting", min_score=body.min_score, daily_limit=body.daily_limit,
                remembers_connection=bool(pair_state), paired=bool(pair_state and pair_state["account"]),
                message="저장한 크롬 연결의 자동 복귀를 기다립니다" if pair_state else "자동 지원 전용 크롬 연결을 기다립니다. 재시작 시 다시 연결해야 합니다")
            self._thread = threading.Thread(target=self._run, daemon=True, name="jobs-auto-apply")
            self._thread.start()
            return {"connection": {"url": self._url, "token": self._token}, "status": self.status()}

    def _run(self):
        server = self._server
        try:
            while not self._stop.is_set():
                try:
                    self.prepare_candidates()
                except MaintenanceBusy:
                    pass  # A restore owns the database; retry on the next bounded tick.
                except Exception:
                    # Preparing a public recheck must not kill the capability listener
                    # or expose a record, journal path or database exception.
                    with self._lock:
                        if self._status.enabled and self._status.state != "paused":
                            self._status.error_code = "candidate_prepare_failed"
                            self._status.message = "지원 후보의 원문 준비를 다시 시도합니다"
                server.handle_request()
        except (OSError, ValueError):
            pass
        finally:
            server.server_close()

    def authorized(self, header):
        with self._lock:
            return bool(self._token and not self._stop.is_set() and hmac.compare_digest(header.encode(), f"Bearer {self._token}".encode()))

    def stop(self):
        pair = self.app._jobs_auto_pairing_runtime
        try:
            if pair is not None: pair.disable()
        except ApplicationError:
            self.shutdown(retry_reason="cancelled")
            raise
        return self.shutdown(retry_reason="cancelled")

    def forget(self):
        from .auto_pairing import runtime as pairing_runtime
        pair = pairing_runtime(self.app)
        # Shut off the live capability even if Windows rejects credential removal.
        self.shutdown(retry_reason="cancelled")
        pair.disable()
        pair.forget()
        pair.close()
        with self._lock:
            self._status.remembers_connection = self._status.paired = False
        return self.status()

    def shutdown(self, *, retry_reason="connection_ended"):
        """Process shutdown clears live authority but preserves explicit resume intent."""
        self._stop.set()
        with self._lock:
            recover_reason = self._status.error_code if self._status.state == "paused" else retry_reason
            self._token, self._url = "", ""
            self._status.enabled, self._status.state, self._status.message = False, "stopped", "자동 지원이 꺼져 있습니다"
            self._account = self._profile = None
            self._leases.clear()
            self._receipts.clear()
            server, thread = self._server, self._thread
        if server: server.server_close()
        if thread and thread is not threading.current_thread(): thread.join(timeout=3)
        self._recover(recover_reason)
        return self.status()

    def _check(self, body):
        if not self._status.enabled or self._stop.is_set() or self._status.state == "paused":
            raise ApplicationError(409, "auto_apply_stopped", "자동 지원이 중지되었습니다")
        if self._generation != self.app.db.gate.generation:
            self._pause_session("database_restored")
            if self.app._jobs_auto_pairing_runtime is not None:
                self.app._jobs_auto_pairing_runtime.disable("database_restored")
            raise ApplicationError(409, "database_restored", "복원 후에는 크롬을 다시 연결하세요")
        if self._account is not None and self._account != body.account_hash:
            self._pause_session("account_changed")
            if self.app._jobs_auto_pairing_runtime is not None:
                self.app._jobs_auto_pairing_runtime.disable("account_changed")
            raise ApplicationError(409, "account_changed", "로그인 계정이 바뀌어 중지되었습니다")
        self._account = body.account_hash
        self._connected = self.monotonic()
        self._status.state, self._status.last_connected_at = "connected", self.app.clock()
        self._status.message = "선택한 크롬 탭에서 조건에 맞는 공고를 자동 지원합니다"

    def _attempt(self, s, body):
        row = s.get(JobAutoAttempt, body.attempt_id)
        expected = self._leases.get(body.attempt_id)
        if row is None or expected is None or not hmac.compare_digest(body.lease_token, expected):
            raise ApplicationError(409, "lease_invalid", "지난 지원 연결입니다")
        return row

    def _eligible(self, s, row, *, visible_form=None, queue=False):
        policy = s.get(SourcePolicy, "daangn-alba")
        if not policy or policy.status != "allowed": return "source_permission_unverified"
        lead = s.get(Lead, row.lead_id)
        if not lead: return "condition_changed"
        receipt = self._receipts.get(row.id)
        if visible_form is None:
            visible_form = bool(receipt and len(receipt) > 4 and receipt[4] and receipt[3] > self.monotonic())
        return condition(s, lead, self.app.clock(), self._status.min_score, visible_form=visible_form, queue=queue)

    def _empty_next(self, s):
        state = self._status_in(s)
        return {"job": None, "reason_code": state.queue_reason, "message": state.message}

    def _request_refresh(self, s, lead, rec):
        from ..engine.executors import _ref_key
        from ..sources.base import PostRef
        detail = s.get(DetailFetchState, (rec.source_id, _ref_key(PostRef(rec.original_url, rec.source_post_id))))
        if detail and ((detail.retry_at and detail.retry_at > self.app.clock()) or detail.outcome in ("collection_disallowed", "collection_permission_unknown")):
            return False
        if self.monotonic() < self._refresh_after: return False
        from ..engine.runs import enqueue, RunConflict
        try:
            _, created = enqueue(s, "recheck_stale", source_id=lead.source_id, lead_id=lead.id,
                trigger="schedule", idempotency_key=f"auto-refresh:{lead.id}:{int(self.app.clock().timestamp() // 3600)}")
            if created: self._refresh_after = self.monotonic()+60
        except RunConflict:
            return False
        self._diagnostic_cache = None
        return created

    def prepare_candidates(self):
        """Prepare stale public records without a Chrome request or submission lease.

        A remembered, active user grant is required. The normal collector still
        owns network access and host budgets; this only enqueues one bounded,
        idempotent recheck through its existing queue.
        """
        created = 0
        with self._lock:
            if (self._stop.is_set() or not self._status.enabled or not self._status.remembers_connection
                    or self._status.state == "paused" or self.monotonic() < self._prepare_after):
                return 0
            self._prepare_after = self.monotonic() + PREPARE_SECONDS
            with self.app.db.gate.operation():
                if self._generation != self.app.db.gate.generation:
                    self._pause_session("database_restored")
                    if self.app._jobs_auto_pairing_runtime is not None:
                        self.app._jobs_auto_pairing_runtime.disable("database_restored")
                    return 0
                with self.app.db.session() as s:
                    policy = s.get(SourcePolicy, "daangn-alba")
                    source = s.get(Source, "daangn-alba")
                    if (not policy or policy.status != "allowed" or not source
                            or not source.auto_collect_enabled or source.stopped_reason):
                        return 0
                    now = self.app.clock()
                    due = select(JobAutoAttempt.lead_id).where(JobAutoAttempt.state == "held",
                        JobAutoAttempt.retry_after <= now, JobAutoAttempt.permitted_at.is_(None),
                        JobAutoAttempt.reason_code.in_(TRANSIENT_REASONS),
                        JobAutoAttempt.retry_count < len(RETRY_DELAYS))
                    candidates = list(s.scalars(select(Lead).join(SourceRecord, SourceRecord.id == Lead.primary_record_id).where(
                        Lead.source_id == "daangn-alba", Lead.priority_total >= self._status.min_score,
                        Lead.recommendation.in_(("recommended", "needs_review")), Lead.source_status == "open",
                        Lead.sales_stage.in_(("new", "reviewing")), Lead.access_status == "accessible",
                        Lead.demand_intent.in_(("buyer_project", "buyer_ongoing")), Lead.engagement_type == "project",
                        Lead.pay_unit == "project", or_(Lead.user_mark.is_(None), Lead.user_mark != "dismissed"),
                        SourceRecord.source_status == "open", SourceRecord.access_status == "accessible",
                        or_(SourceRecord.last_checked_at.is_(None), SourceRecord.last_checked_at < now - timedelta(hours=ttl_hours(s)),
                            SourceRecord.last_checked_at > now),
                        (~Lead.id.in_(select(JobAutoAttempt.lead_id))) | Lead.id.in_(due))
                        .order_by(Lead.published_at.desc(), Lead.first_seen_at.desc(), Lead.id)
                        .offset(self._prepare_offset).limit(PREPARE_LIMIT)))
                    # Rotate bounded pages so old candidates cannot be starved by
                    # a page full of detail backoffs or profile conflicts.
                    self._prepare_offset = self._prepare_offset + len(candidates) if len(candidates) == PREPARE_LIMIT else 0
                    journal = self._journal_keys()
                    for lead in candidates:
                        if self._stop.is_set(): break
                        rec = s.get(SourceRecord, lead.primary_record_id)
                        if rec is None or job_identity(rec) in journal or condition(s, lead, now, self._status.min_score, queue=True):
                            continue
                        if self._request_refresh(s, lead, rec):
                            created = 1
                            break
                if self._status.error_code == "candidate_prepare_failed":
                    self._status.error_code = None
        if created: self.app.hub.notify()
        return created

    def next(self, body):
        with self._lock, self.app.db.session() as s:
            s.execute(text("BEGIN IMMEDIATE"))
            self._check(body)
            self._expire(s)
            state = self._status_in(s)
            if state.today_submitted + state.today_reserved >= state.daily_limit: return self._empty_next(s)
            if s.scalar(select(JobAutoAttempt.id).where(JobAutoAttempt.state.in_(("claimed", "ready", "submitting"))).limit(1)):
                return self._empty_next(s)
            policy = s.get(SourcePolicy, "daangn-alba")
            if not policy or policy.status != "allowed": return self._empty_next(s)
            known = {row.job_key: row for row in s.scalars(select(JobAutoAttempt))}
            journal = self._journal_keys()
            due = select(JobAutoAttempt.lead_id).where(JobAutoAttempt.state == "held", JobAutoAttempt.retry_after <= self.app.clock())
            candidates = s.scalars(select(Lead).where(Lead.source_id == "daangn-alba", Lead.priority_total >= self._status.min_score,
                Lead.recommendation.in_(("recommended", "needs_review")), Lead.source_status == "open",
                Lead.sales_stage.in_(("new", "reviewing")),
                (~Lead.id.in_(select(JobAutoAttempt.lead_id))) | Lead.id.in_(due))
                .order_by(Lead.published_at.desc(), Lead.first_seen_at.desc()).limit(1000))
            for lead in candidates:
                rec = s.get(SourceRecord, lead.primary_record_id)
                key = job_identity(rec) if rec else None
                if not key: continue
                row = known.get(key)
                if row is not None:
                    if row.state != "held" or row.retry_after is None or row.retry_after > self.app.clock(): continue
                    if row.permitted_at is not None or key in journal:
                        row.state, row.reason_code, row.retry_after = "uncertain", "previous_permit", None
                        self._diagnostic_cache = None
                        continue
                    if row.reason_code not in TRANSIENT_REASONS or not 0 <= (row.retry_count or 0) < len(RETRY_DELAYS):
                        row.retry_after = None
                        if row.reason_code in TRANSIENT_REASONS: row.reason_code = "retry_exhausted"
                        self._diagnostic_cache = None
                        continue
                elif key in journal: continue
                reason = condition(s, lead, self.app.clock(), self._status.min_score, queue=True)
                if reason and reason != "condition_conflict":
                    if row is not None: self._finish(s, row, reason)
                    continue
                fresh = rec.last_checked_at is not None and self.app.clock() - timedelta(hours=ttl_hours(s)) <= rec.last_checked_at <= self.app.clock()
                if not fresh and not reason:
                    self._request_refresh(s, lead, rec)
                    continue
                now = self.app.clock()
                if row is None:
                    row = JobAutoAttempt(id=new_id("auto"), job_key=key, lead_id=lead.id, state="held" if reason else "claimed",
                        reason_code=reason, created_at=now, finished_at=now if reason else None,
                        expires_at=None if reason else now + timedelta(seconds=LEASE_SECONDS), retry_count=0)
                    s.add(row); known[key] = row
                elif reason:
                    self._finish(s, row, reason)
                else:
                    # Reuse the durable unique job identity; never an old lease/receipt.
                    self._leases.pop(row.id, None); self._receipts.pop(row.id, None)
                    row.retry_count = (row.retry_count or 0)+1
                    row.state, row.reason_code, row.finished_at, row.retry_after = "claimed", None, None, None
                    row.expires_at = now+timedelta(seconds=LEASE_SECONDS)
                self._diagnostic_cache = None
                if reason: continue
                token = secrets.token_urlsafe(32)
                self._leases[row.id] = token
                return {"job": {"attempt_id": row.id, "lead_id": lead.id, "title": lead.title, "url": rec.original_url, "lease_token": token}}
            return self._empty_next(s)

    def review(self, body):
        with self._lock, self.app.db.session() as s:
            s.execute(text("BEGIN IMMEDIATE"))
            self._check(body)
            self._expire(s)
            row = self._attempt(s, body)
            if row.state != "claimed": raise ApplicationError(409, "review_invalid", "검토 가능한 지원이 아닙니다")
            if body.outcome == "already_applied" and body.reason_code == "already_applied":
                row.state, row.reason_code, row.finished_at = "already_applied", "already_applied", self.app.clock()
                row.retry_after = None
                self._leases.pop(row.id, None); self._receipts.pop(row.id, None)
                self._diagnostic_cache = None
                return {"status": row.state}
            reason = self._eligible(s, row, visible_form=body.outcome == "ready" and body.evidence == "application_form",
                queue=body.outcome == "held" and body.reason_code in TRANSIENT_REASONS)
            if body.outcome == "ready" and self._profile is not None and self._profile != body.profile_hash:
                reason = "profile_changed"
                if self._status.remembers_connection:
                    self.app._jobs_auto_pairing_runtime.disable("profile_changed")
                    self._pause_session("profile_changed")
            if reason:
                self._finish(s, row, reason)
            elif body.outcome == "ready" and body.reason_code == "ready":
                if self._status.remembers_connection:
                    try: self.app._jobs_auto_pairing_runtime.bind_profile(body.profile_hash)
                    except ApplicationError:
                        self._pause_session("profile_changed")
                        raise
                self._profile = body.profile_hash
                row.state, row.reason_code = "ready", "ready"
                self._diagnostic_cache = None
                receipt = secrets.token_urlsafe(32)
                self._receipts[row.id] = (receipt, body.account_hash, body.profile_hash, self.monotonic() + RECEIPT_SECONDS,
                    body.evidence == "application_form")
                return {"status": "ready", "receipt_token": receipt}
            else:
                self._finish(s, row, body.reason_code)
            return {"status": row.state}

    def permit(self, body):
        with self._lock, self.app.db.session() as s:
            s.execute(text("BEGIN IMMEDIATE"))
            self._check(body)
            self._expire(s)
            row = self._attempt(s, body)
            if row.state != "ready" or self._profile != body.profile_hash:
                raise ApplicationError(409, "permit_invalid", "새 제출 허가가 필요합니다")
            reason = self._eligible(s, row)
            state = self._status_in(s)
            if reason or state.today_submitted + state.today_reserved >= state.daily_limit:
                self._finish(s, row, reason or "daily_limit")
                return {"permit": False, "expires_in_seconds": 0}
            if row.job_key in self._journal_keys():
                row.state, row.reason_code, row.finished_at = "uncertain", "previous_permit", self.app.clock()
                row.retry_after = None
                self._diagnostic_cache = None
                return {"permit": False, "expires_in_seconds": 0}
            # Persist outside restorable DB before the permit can leave this process.
            # Losing either HTTP reply or SQL commit must never cause another click.
            self._remember_permit(row.job_key)
            row.state, row.permitted_at = "submitting", self.app.clock()
            row.expires_at = self.app.clock() + timedelta(seconds=15)
            row.retry_after = None
            self._diagnostic_cache = None
            return {"permit": True, "expires_in_seconds": PERMIT_SECONDS}

    def result(self, body):
        with self._lock, self.app.db.session() as s:
            s.execute(text("BEGIN IMMEDIATE"))
            self._check(body)
            row = self._attempt(s, body)
            if row.state != "submitting" or self._profile != body.profile_hash:
                raise ApplicationError(409, "result_invalid", "제출 결과를 대조할 수 없습니다")
            if body.outcome == "submitted" and (body.evidence != "cancel_button" or body.reason_code != "submitted"):
                raise ApplicationError(422, "completion_evidence_required", "지원 완료 표시가 필요합니다")
            row.state = "submitted" if body.outcome == "submitted" else "uncertain"
            row.reason_code, row.finished_at, row.expires_at = body.reason_code, self.app.clock(), None
            row.retry_after = None
            self._diagnostic_cache = None
            if row.state == "submitted":
                self._completed(s, row)
            else:
                emit(s, "notification.created", {"kind": "lead_changed", "title": "당근 지원 결과 확인 필요", "lead_id": row.lead_id}, notify=True)
            outcome = {"status": row.state}
        self.app.hub.notify()
        return outcome

    def _completed(self, s, row):
        lead = s.get(Lead, row.lead_id)
        if lead.sales_stage in ("new", "reviewing"): lead.sales_stage = "contacted"
        s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=self.app.clock(), kind="contact", text="당근 자동 지원 — 지원 취소 버튼으로 완료 확인"))
        emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["sales_stage"]})
        emit(s, "notification.created", {"kind": "lead_changed", "title": "당근 지원 완료", "lead_id": row.lead_id}, notify=True)

    def reconcile(self, body):
        # This capability can only confirm an existing send. It cannot issue a
        # permit, revive a lease or authorize a new click after a lost reply.
        with self._lock, self.app.db.session() as s:
            s.execute(text("BEGIN IMMEDIATE"))
            self._check(body)
            proof = self._receipts.get(body.attempt_id)
            if (proof is None or proof[3] <= self.monotonic()
                    or not hmac.compare_digest(body.receipt_token, proof[0])
                    or body.account_hash != proof[1] or body.profile_hash != proof[2]):
                raise ApplicationError(409, "receipt_invalid", "현재 연결에서 확인할 수 있는 제출 기록이 아닙니다")
            row = s.get(JobAutoAttempt, body.attempt_id)
            if (not row or row.state not in ("submitting", "uncertain", "submitted")
                    or not row.permitted_at or row.job_key not in self._journal_keys()):
                raise ApplicationError(409, "reconcile_invalid", "제출 허가 기록을 대조할 수 없습니다")
            if row.state == "submitted": return {"status": "submitted"}
            row.state, row.reason_code = "submitted", "completion_reconciled"
            row.finished_at, row.expires_at = self.app.clock(), None
            self._completed(s, row)
        self.app.hub.notify()
        return {"status": "submitted"}

    def pause(self, body):
        with self._lock:
            if not self._status.enabled or self._stop.is_set():
                raise ApplicationError(409, "auto_apply_stopped", "자동 지원이 중지되었습니다")
            self._pause_session(body.reason)
            if self.app._jobs_auto_pairing_runtime is not None:
                self.app._jobs_auto_pairing_runtime.disable(body.reason)
            self._recover()
        return {"status": "paused"}

    def _pause_session(self, reason):
        with self._lock:
            self._receipts.clear()
            self._leases.clear()
            # Callers may be inside a write transaction. Recovery is deferred to
            # expiry/shutdown instead of opening a nested SQLite write session.
            self._status.state, self._status.error_code = "paused", reason
            self._status.message = "계정·소개 또는 데이터 상태가 바뀌어 중지했습니다. 확인 후 다시 시작하세요"


def runtime(app):
    with app.ai_control_lock:
        if getattr(app, "_jobs_auto_apply_runtime", None) is None:
            app._jobs_auto_apply_runtime = AutoApplyRuntime(app)
        return app._jobs_auto_apply_runtime
