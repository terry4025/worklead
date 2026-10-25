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

from sqlalchemy import select, text

from ..analysis.service import analyze
from ..db import MaintenanceBusy
from ..engine.events import emit
from ..engine.pipeline import feedback_map, post_input_from_record
from ..models import AppSetting, JobAutoAttempt, Lead, LeadActivity, SourcePolicy, SourceRecord, new_id
from ..services.settings import get_profile, ttl_hours
from .applications import ApplicationError
from .auto_apply_models import AutoApplyStartIn, AutoApplyStatus, AutoAttemptOut

LEASE_SECONDS = 90
PERMIT_SECONDS = 3
CONNECTED_SECONDS = 90


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


def condition(s, lead, now, min_score):
    if (lead.source_id != "daangn-alba" or (lead.priority_total or 0) < min_score
            or lead.recommendation not in ("recommended", "needs_review")
            or lead.demand_intent not in ("buyer_project", "buyer_ongoing")
            or lead.engagement_type != "project" or lead.pay_unit != "project" or lead.sales_stage not in ("new", "reviewing")
            or lead.user_mark == "dismissed" or lead.source_status != "open" or lead.access_status != "accessible"):
        return "condition_changed"
    rec = s.get(SourceRecord, lead.primary_record_id)
    if not rec or not rec.body_text or rec.source_status != "open" or rec.access_status != "accessible" or not job_identity(rec):
        return "source_unverified"
    if (rec.published_precision != "exact" or rec.published_at is None or not now - timedelta(hours=24) <= rec.published_at <= now
            or rec.last_checked_at is None or not now - timedelta(hours=ttl_hours(s)) <= rec.last_checked_at <= now):
        return "source_stale"
    profile = get_profile(s)
    current = analyze(post_input_from_record(rec, "site"), profile, now, ttl_hours(s), feedback_map(s, lead.id))
    if current.recommendation == "excluded" or not current.dev_relevant or current.pay.unit != "project" or current.total is None or current.total < min_score:
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
        self._generation = app.db.gate.generation
        self._connected = None
        self._journal = app.config.data_dir / "auto-apply-submit.journal"
        self._recover()

    def _recover(self):
        with self.app.db.session() as s:
            for row in s.scalars(select(JobAutoAttempt).where(JobAutoAttempt.state.in_(("claimed", "ready", "submitting")))):
                row.state = "uncertain" if row.state == "submitting" else "held"
                row.reason_code, row.finished_at, row.expires_at = "connection_ended", self.app.clock(), None

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
            row.state = "uncertain" if row.state == "submitting" else "held"
            row.reason_code, row.finished_at, row.expires_at = "lease_expired", now, None
            self._leases.pop(row.id, None)

    def _status_in(self, s):
        self._expire(s)
        rows = list(s.scalars(select(JobAutoAttempt).order_by(JobAutoAttempt.created_at.desc(), JobAutoAttempt.id.desc())))
        today = [row for row in rows if row.permitted_at and row.permitted_at >= self._day_start(self.app.clock())]
        result = self._status.model_copy(deep=True)
        result.today_submitted = sum(row.state == "submitted" for row in today)
        journal_count = sum(day == self._day_start(self.app.clock()).date().isoformat() for day, key in self._journal_entries())
        result.today_reserved = max(sum(row.state != "submitted" for row in today), journal_count - result.today_submitted)
        result.held = sum(row.state == "held" for row in rows)
        result.uncertain = sum(row.state == "uncertain" for row in rows)
        result.attempts = [AutoAttemptOut(id=row.id, lead_id=row.lead_id, title=(s.get(Lead, row.lead_id).title), state=row.state,
            reason_code=row.reason_code, created_at=row.created_at, finished_at=row.finished_at) for row in rows[:30]]
        if result.enabled and self._connected is not None and self.monotonic() - self._connected >= CONNECTED_SECONDS:
            result.state, result.message = "waiting", "크롬 연결이 늦어지고 있습니다. 탭과 확장 연결을 확인하세요"
        return result

    def status(self):
        with self._lock, self.app.db.session() as s:
            if not self._status.enabled:
                setting = s.get(AppSetting, "job_auto_apply")
                if setting:
                    self._status.min_score = setting.value.get("min_score", 50)
                    self._status.daily_limit = setting.value.get("daily_limit", 20)
            return self._status_in(s)

    def start(self, body: AutoApplyStartIn):
        from .auto_apply_bridge import BridgeServer, Handler
        self.stop()
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
            self._account = self._profile = None
            self._leases.clear()
            self._connected = None
            self._generation = self.app.db.gate.generation
            self._server = BridgeServer(("127.0.0.1", 0), Handler)
            self._server.runtime = self
            self._token = secrets.token_urlsafe(32)
            self._url = f"http://127.0.0.1:{self._server.server_port}"
            self._status = AutoApplyStatus(enabled=True, state="waiting", min_score=body.min_score, daily_limit=body.daily_limit,
                message="자동 지원 전용 크롬 연결을 기다립니다. 재시작 시 다시 연결해야 합니다")
            self._thread = threading.Thread(target=self._run, daemon=True, name="jobs-auto-apply")
            self._thread.start()
            return {"connection": {"url": self._url, "token": self._token}, "status": self.status()}

    def _run(self):
        server = self._server
        try:
            while not self._stop.is_set():
                server.handle_request()
        except (OSError, ValueError):
            pass
        finally:
            server.server_close()

    def authorized(self, header):
        with self._lock:
            return bool(self._token and not self._stop.is_set() and hmac.compare_digest(header.encode(), f"Bearer {self._token}".encode()))

    def stop(self):
        self._stop.set()
        with self._lock:
            self._token, self._url = "", ""
            self._status.enabled, self._status.state, self._status.message = False, "stopped", "자동 지원이 꺼져 있습니다"
            self._account = self._profile = None
            self._leases.clear()
            server, thread = self._server, self._thread
        if server: server.server_close()
        if thread and thread is not threading.current_thread(): thread.join(timeout=3)
        self._recover()
        return self.status()

    def _check(self, body):
        if not self._status.enabled or self._stop.is_set() or self._status.state == "paused":
            raise ApplicationError(409, "auto_apply_stopped", "자동 지원이 중지되었습니다")
        if self._generation != self.app.db.gate.generation:
            self._status.state, self._status.error_code = "paused", "database_restored"
            raise ApplicationError(409, "database_restored", "복원 후에는 크롬을 다시 연결하세요")
        if self._account is not None and self._account != body.account_hash:
            self._status.state, self._status.error_code = "paused", "account_changed"
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

    def _eligible(self, s, row):
        policy = s.get(SourcePolicy, "daangn-alba")
        if not policy or policy.status != "allowed": return "source_permission_unverified"
        lead = s.get(Lead, row.lead_id)
        if not lead: return "condition_changed"
        return condition(s, lead, self.app.clock(), self._status.min_score)

    def next(self, body):
        with self._lock, self.app.db.session() as s:
            s.execute(text("BEGIN IMMEDIATE"))
            self._check(body)
            self._expire(s)
            state = self._status_in(s)
            if state.today_submitted + state.today_reserved >= state.daily_limit: return {"job": None}
            if s.scalar(select(JobAutoAttempt.id).where(JobAutoAttempt.state.in_(("claimed", "ready", "submitting"))).limit(1)):
                return {"job": None}
            policy = s.get(SourcePolicy, "daangn-alba")
            if not policy or policy.status != "allowed": return {"job": None}
            seen = set(s.scalars(select(JobAutoAttempt.job_key))) | self._journal_keys()
            candidates = s.scalars(select(Lead).where(Lead.source_id == "daangn-alba", Lead.priority_total >= self._status.min_score,
                Lead.published_at >= self.app.clock() - timedelta(hours=24)).order_by(Lead.published_at.desc(), Lead.first_seen_at.desc()).limit(1000))
            for lead in candidates:
                rec = s.get(SourceRecord, lead.primary_record_id)
                key = job_identity(rec) if rec else None
                if not key or key in seen: continue
                reason = condition(s, lead, self.app.clock(), self._status.min_score)
                if reason and reason != "condition_conflict": continue
                now = self.app.clock()
                row = JobAutoAttempt(id=new_id("auto"), job_key=key, lead_id=lead.id, state="held" if reason else "claimed",
                    reason_code=reason, created_at=now, finished_at=now if reason else None,
                    expires_at=None if reason else now + timedelta(seconds=LEASE_SECONDS))
                s.add(row)
                seen.add(key)
                if reason: continue
                token = secrets.token_urlsafe(32)
                self._leases[row.id] = token
                return {"job": {"attempt_id": row.id, "lead_id": lead.id, "title": lead.title, "url": rec.original_url, "lease_token": token}}
            return {"job": None}

    def review(self, body):
        with self._lock, self.app.db.session() as s:
            s.execute(text("BEGIN IMMEDIATE"))
            self._check(body)
            self._expire(s)
            row = self._attempt(s, body)
            if row.state != "claimed": raise ApplicationError(409, "review_invalid", "검토 가능한 지원이 아닙니다")
            reason = self._eligible(s, row)
            if body.outcome == "ready" and self._profile is not None and self._profile != body.profile_hash: reason = "profile_changed"
            if reason:
                row.state, row.reason_code, row.finished_at = "held", reason, self.app.clock()
            elif body.outcome == "ready" and body.reason_code == "ready":
                self._profile = body.profile_hash
                row.state, row.reason_code = "ready", "ready"
            else:
                row.state = "already_applied" if body.outcome == "already_applied" and body.reason_code == "already_applied" else "held"
                row.reason_code, row.finished_at = body.reason_code, self.app.clock()
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
                row.state, row.reason_code, row.finished_at = "held", reason or "daily_limit", self.app.clock()
                return {"permit": False, "expires_in_seconds": 0}
            if row.job_key in self._journal_keys():
                row.state, row.reason_code, row.finished_at = "uncertain", "previous_permit", self.app.clock()
                return {"permit": False, "expires_in_seconds": 0}
            # Persist outside restorable DB before the permit can leave this process.
            # Losing either HTTP reply or SQL commit must never cause another click.
            self._remember_permit(row.job_key)
            row.state, row.permitted_at = "submitting", self.app.clock()
            row.expires_at = self.app.clock() + timedelta(seconds=15)
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
            if row.state == "submitted":
                lead = s.get(Lead, row.lead_id)
                if lead.sales_stage in ("new", "reviewing"): lead.sales_stage = "contacted"
                s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=self.app.clock(), kind="contact", text="당근 자동 지원 — 지원 취소 버튼으로 완료 확인"))
                emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["sales_stage"]})
            emit(s, "notification.created", {"kind": "lead_changed", "title": "당근 지원 완료" if row.state == "submitted" else "당근 지원 결과 확인 필요", "lead_id": row.lead_id}, notify=True)
            outcome = {"status": row.state}
        self.app.hub.notify()
        return outcome


def runtime(app):
    with app.ai_control_lock:
        if getattr(app, "_jobs_auto_apply_runtime", None) is None:
            app._jobs_auto_apply_runtime = AutoApplyRuntime(app)
        return app._jobs_auto_apply_runtime
