"""로컬 API (FastAPI).

보안
- 127.0.0.1 에서만 열고, 실행마다 바뀌는 Bearer 토큰을 요구한다 (URL·로그에 토큰을 넣지 않음).
- Host 헤더와 Origin 을 검증한다 (와일드카드 허용 없음).
- 오류는 {error:{code,message,details,request_id,retryable}} 형식.
- 장기 작업은 202 + {job_id, run_id}.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import API_VERSION, APP_VERSION, CONTRACT_VERSION
from ..context import AppContext
from ..db import MaintenanceBusy, utcnow
from ..engine import runs as runq
from ..engine.events import emit
from ..engine.pipeline import apply_analysis, generate_draft
from ..models import EventOutbox, Lead, LeadActivity, Outcome, Run, Source, SourcePolicy, SourceRecord, UserFeedback, new_id
from ..services import leads as lead_svc
from ..services import relations as relation_svc
from ..services import backups as backup_svc
from ..services import metrics as metrics_svc
from ..services import native_notifications as notification_svc
from ..services import settings as settings_svc
from ..services import sources as source_svc
from ..services.credentials import CredentialError, CredentialStatus
from ..analysis.service import CATEGORY_LABEL as CATEGORIES
from ..analysis.openai_provider import PRICE_VERSION
from . import schemas as S

log = logging.getLogger("worklead.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None, *, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable


def _error(request: Request, status: int, code: str, message: str, details: Any = None, retryable: bool = False) -> JSONResponse:
    rid = getattr(request.state, "request_id", None) or secrets.token_hex(8)
    body = {"error": {"code": code, "message": message, "details": details, "request_id": rid, "retryable": retryable}}
    return JSONResponse(body, status_code=status, headers={"X-Request-Id": rid, "Cache-Control": "no-store"})


ERRORS = {
    400: {"model": S.ErrorResponse},
    401: {"model": S.ErrorResponse},
    404: {"model": S.ErrorResponse},
    409: {"model": S.ErrorResponse},
    422: {"model": S.ErrorResponse},
    500: {"model": S.ErrorResponse},
    503: {"model": S.ErrorResponse},
}


def _session(request: Request):  # type: ignore[no-untyped-def]
    ctx: AppContext = request.app.state.ctx
    with ctx.db.session() as s:
        s.info["database_generation"] = request.app.state.relation_generation_base + ctx.db.gate.generation
        yield s


Sess = Annotated[Any, Depends(_session)]


def lead_filter(
    keyword: str | None = Query(default=None, max_length=200),
    source_id: str | None = None,
    category: str | None = None,
    remote: S.RemoteFilter = "any",
    recruit: S.RecruitFilter = "any",
    intent: S.IntentFilter = "any",
    work_mode: Annotated[list[S.WorkMode] | None, Query()] = None,
    recommendation: Annotated[list[S.Recommendation] | None, Query()] = None,
    source_status: Annotated[list[S.SourceStatus] | None, Query()] = None,
    sales_stage: Annotated[list[S.SalesStage] | None, Query()] = None,
    pay_unit: Annotated[list[S.PayUnit] | None, Query()] = None,
    checked_since: datetime | None = None,
    sort: S.SortKey | None = None,
) -> dict[str, Any]:
    return {
        "keyword": keyword,
        "source_id": source_id,
        "category": category,
        "remote": remote,
        "recruit": recruit,
        "intent": intent,
        "work_mode": work_mode,
        "recommendation": recommendation,
        "source_status": source_status,
        "sales_stage": sales_stage,
        "pay_unit": pay_unit,
        "checked_since": checked_since,
        "sort": sort,
    }



Filter = Annotated[dict[str, Any], Depends(lead_filter)]


def create_app(ctx: AppContext, *, trusted_hosts: set[str] | None = None, workers_running: Any = None) -> FastAPI:
    app = FastAPI(
        title="Worklead Local API",
        version=CONTRACT_VERSION,
        description="Worklead 데스크톱 앱의 로컬 API. 127.0.0.1 전용, 실행별 Bearer 토큰 필요.",
        docs_url=None,
        redoc_url=None,
        openapi_url="/v1/openapi.json" if ctx.config.dev else None,
    )
    app.state.ctx = ctx
    from .cafe_routes import router as cafe_router
    from ..services.cafe import CafeError
    app.include_router(cafe_router(_session))
    from .job_routes import router as job_router
    from .job_message_routes import router as job_message_router
    from ..jobs.applications import ApplicationError
    app.include_router(job_router(_session))
    app.include_router(job_message_router())

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError):
        return _error(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(CafeError)
    async def cafe_error(request: Request, exc: CafeError):
        return _error(request, exc.status, exc.code, exc.message)
    # A relation decision from an earlier sidecar must also fail after restart.
    # Leave ample counter space while keeping the exposed integer JS-safe.
    app.state.relation_generation_base = (secrets.randbits(40) + 1) << 12
    origins = ctx.config.origins()

    # Middleware is wrapped in reverse registration order: host/origin guard →
    # CORS → authentication. Auth errors must remain readable by the desktop UI.
    @app.middleware("http")
    async def authenticate(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method != "OPTIONS" and request.url.path.startswith("/v1") and request.url.path != "/v1/openapi.json":
            auth = request.headers.get("authorization") or ""
            token = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not ctx.token or not hmac.compare_digest(token.encode(), ctx.token.encode()):
                return _error(request, 401, "unauthorized", "인증 토큰이 없거나 올바르지 않습니다")
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "Idempotency-Key"],
        expose_headers=["X-Request-Id"],
        allow_credentials=False,
        allow_private_network=True,  # Only the exact origins validated below can reach preflight.
        max_age=600,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = secrets.token_hex(8)
        host = (request.headers.get("host") or "").split(":")[0].lower()
        allowed_hosts = trusted_hosts or {"127.0.0.1", "localhost"}
        if host not in allowed_hosts:
            return _error(request, 400, "invalid_host", "허용되지 않은 Host 헤더입니다")
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return _error(request, 403, "origin_not_allowed", "허용되지 않은 Origin 입니다")
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):  # type: ignore[no-untyped-def]
        return _error(request, exc.status, exc.code, exc.message, exc.details, exc.retryable)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/v1/ai/credentials"):
            # Unknown JSON property names can themselves contain a secret.
            # No input, location, validator text or submitted key is reflected.
            return _error(request, 422, "validation_error", "키 요청 형식이 올바르지 않습니다")
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _error(request, 422, "validation_error", "요청 형식이 올바르지 않습니다", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):  # type: ignore[no-untyped-def]
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(lead_svc.CursorError)
    async def _cursor(request: Request, exc: lead_svc.CursorError):  # type: ignore[no-untyped-def]
        return _error(request, 400, "invalid_cursor", str(exc))

    @app.exception_handler(runq.RunConflict)
    async def _conflict(request: Request, exc: runq.RunConflict):  # type: ignore[no-untyped-def]
        return _error(request, 409, exc.code, exc.message, {"run_id": exc.run_id} if exc.run_id else None)

    @app.exception_handler(MaintenanceBusy)
    async def _maintenance(request: Request, exc: MaintenanceBusy):  # type: ignore[no-untyped-def]
        return _error(request, 503, "maintenance_busy", str(exc), retryable=True)

    @app.exception_handler(CredentialError)
    async def _credential_error(request: Request, exc: CredentialError):  # type: ignore[no-untyped-def]
        return _error(request, 422 if exc.code == "credential_invalid_key" else 503,
                      exc.code, "Windows 자격 증명 저장소에서 키 작업을 완료하지 못했습니다. 상태를 다시 확인하세요")

    @app.exception_handler(settings_svc.AISettingsChanged)
    async def _ai_changed(request: Request, exc: settings_svc.AISettingsChanged):  # type: ignore[no-untyped-def]
        return _error(request, 409, "ai_settings_changed", "AI 설정이 변경되었습니다. 상태를 다시 불러오세요")

    @app.exception_handler(backup_svc.BackupError)
    async def _backup_error(request: Request, exc: backup_svc.BackupError):  # type: ignore[no-untyped-def]
        return _error(request, exc.status, exc.code, exc.message, retryable=exc.retryable)

    @app.exception_handler(notification_svc.NotificationError)
    async def _notification_error(request: Request, exc: notification_svc.NotificationError):  # type: ignore[no-untyped-def]
        return _error(request, exc.status, exc.code, exc.message)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
        log.exception("unhandled error path=%s", request.url.path)
        return _error(request, 500, "internal_error", "처리 중 오류가 발생했습니다", retryable=True)

    def get_lead(s: Any, lead_id: str) -> Lead:
        lead = s.get(Lead, lead_id)
        if lead is None:
            raise ApiError(404, "not_found", "리드를 찾을 수 없습니다")
        return lead

    # ── 상태 ────────────────────────────────────────────────────────
    @app.get("/v1/health", response_model=S.HealthOut, tags=["system"], responses=ERRORS)
    def health(s: Sess) -> S.HealthOut:
        try:
            s.execute(select(1))
            db_ok = "ok"
        except Exception:  # noqa: BLE001
            db_ok = "error"
        running = bool(workers_running()) if workers_running else False
        return S.HealthOut(status="ok" if db_ok == "ok" else "degraded", version=APP_VERSION, db=db_ok, workers="running" if running else "stopped", time=utcnow())  # type: ignore[arg-type]

    @app.get("/v1/bootstrap", response_model=S.BootstrapOut, tags=["system"], responses=ERRORS)
    def bootstrap(s: Sess) -> S.BootstrapOut:
        seq = s.scalar(select(func.max(EventOutbox.seq))) or 0
        notices = s.scalars(select(EventOutbox).where(
            EventOutbox.type == "notification.created", EventOutbox.notify.is_(False),
            EventOutbox.dedupe_key.like("notify:quiet_summary:%"),
            EventOutbox.created_at >= utcnow() - timedelta(hours=24),
        ).order_by(EventOutbox.created_at.desc(), EventOutbox.seq.desc()).limit(10)).all()
        return S.BootstrapOut(
            mode="demo" if ctx.config.demo else "live",
            app_version=APP_VERSION,
            api_version=API_VERSION,
            contract_version=CONTRACT_VERSION,
            server_time=utcnow(),
            categories=[S.CategoryOut(id=k, label=v) for k, v in CATEGORIES.items()],
            queues=[S.QueueOut(id=q, label=label, description=d) for q, label, d in lead_svc.QUEUES],  # type: ignore[arg-type]
            recheck_ttl_hours=settings_svc.ttl_hours(s),
            event_seq=seq,
            notices=[S.AppNotice(id=row.id, kind=row.payload["kind"], title=row.payload["title"], created_at=row.created_at)
                     for row in notices if row.payload.get("kind") in ("new_lead", "new_review_lead")],
        )

    # ── 소스 ────────────────────────────────────────────────────────
    @app.get("/v1/sources", response_model=S.SourceList, tags=["sources"], responses=ERRORS)
    def list_sources(s: Sess) -> S.SourceList:
        rows = s.scalars(select(Source).order_by(Source.kind.desc(), Source.created_at)).all()
        return S.SourceList(items=[source_svc.serialize(s, ctx.db, r, ctx.adapters.get(r.id)) for r in rows])

    @app.patch("/v1/sources/{source_id}", response_model=S.SourceOut, tags=["sources"], responses=ERRORS)
    def patch_source(source_id: str, body: S.SourcePatch, s: Sess) -> S.SourceOut:
        source = s.get(Source, source_id)
        if source is None:
            raise ApiError(404, "not_found", "소스를 찾을 수 없습니다")
        policy = s.get(SourcePolicy, source_id)
        assert policy is not None
        now = utcnow()
        if body.policy is not None:
            if source.kind != "site":
                raise ApiError(409, "not_applicable", "수동 입력 소스에는 수집 정책이 없습니다")
            if body.policy.status in ("allowed", "restricted") and not (body.policy.basis or "").strip():
                raise ApiError(422, "policy_basis_required", "허용·제한 판단에는 근거(약관 조항·허가 문서 등)가 필요합니다")
            policy.status = body.policy.status
            policy.basis = body.policy.basis
            policy.note = body.policy.note
            policy.reviewed_at = now
            policy.reviewed_by = "user"
            if body.policy.status in ("permission_pending", "blocked"):
                source.auto_collect_enabled = False
        if body.clear_stop:
            source.stopped_reason = None
            source.health_status = "unknown"
            source.health_code = None
            source.health_message = "사용자가 정지를 해제함 — 다음 실행에서 상태 확인"
        if body.interval_minutes is not None:
            source.interval_minutes = body.interval_minutes
        if body.auto_collect_enabled is not None:
            was_enabled = source.auto_collect_enabled
            if body.auto_collect_enabled:
                if source.kind != "site":
                    raise ApiError(409, "not_applicable", "수동 입력은 자동 수집이 없습니다")
                if policy.status not in ("allowed", "restricted"):
                    raise ApiError(409, "policy_not_allowed", f"자동 수집 권한이 확인되지 않았습니다 ({policy.status})")
                research = source_svc.research_state(ctx.adapters.get(source_id))
                if not research.ready:
                    raise ApiError(409, "research_incomplete", "사이트 조사가 끝나지 않았습니다", {"missing": research.missing})
                if source.stopped_reason:
                    raise ApiError(409, "source_stopped", "정지된 소스입니다. 원인을 확인한 뒤 정지를 해제하세요.")
            source.auto_collect_enabled = body.auto_collect_enabled
            if body.auto_collect_enabled and not was_enabled:
                # An explicit enable starts one scan; later scans use the
                # persisted interval. Existing work is never duplicated.
                try:
                    if source_id == 'daangn_cafe':
                        from ..services import cafe as cafe_svc
                        cafe_svc.enqueue_job(s, 'collect', now, {'scheduled': True})
                    else:
                        runq.enqueue(s, "discovery", source_id=source_id, trigger="schedule", idempotency_key=f"enable:{source_id}:{now.isoformat()}")
                except runq.RunConflict:
                    pass
            elif not body.auto_collect_enabled:
                for pending in s.scalars(select(Run).where(Run.source_id == source_id, Run.trigger == "schedule", Run.state == "queued")):
                    runq.request_action(s, pending, "cancel")
                if source_id == 'daangn_cafe':
                    from ..models import CafeJob
                    from ..services import cafe as cafe_svc
                    for pending in s.scalars(select(CafeJob).where(CafeJob.kind == 'collect', CafeJob.state == 'queued')):
                        if pending.input.get('scheduled'):
                            cafe_svc.finish_job(s, pending.id, 'cancelled', now)
        emit(s, "source.health_changed", {"source_id": source_id, "health": source.health_status})
        s.flush()
        ctx.hub.notify()
        return source_svc.serialize(s, ctx.db, source, ctx.adapters.get(source_id))

    # ── 실행 ────────────────────────────────────────────────────────
    @app.post("/v1/runs", status_code=202, response_model=S.JobAccepted, tags=["runs"], responses=ERRORS)
    def create_run(body: S.RunCreate, s: Sess) -> S.JobAccepted:
        source = s.get(Source, body.source_id)
        if source is None:
            raise ApiError(404, "not_found", "소스를 찾을 수 없습니다")
        if source.kind != "site":
            raise ApiError(409, "not_applicable", "수동 입력은 /v1/imports 를 사용합니다")
        policy = s.get(SourcePolicy, source.id)
        if policy is None or policy.status not in ("allowed", "restricted"):
            raise ApiError(409, "policy_not_allowed", f"자동 수집 권한이 확인되지 않았습니다 ({policy.status if policy else 'permission_pending'})")
        research = source_svc.research_state(ctx.adapters.get(source.id))
        if source.id == 'daangn_cafe':
            raise ApiError(409, 'cafe_browser_queue_required', '카페 작업 공간의 수집 기능을 사용하세요. 전용 Edge 큐에서 실행합니다')
        if body.kind == "discovery" and not research.ready:
            raise ApiError(409, "research_incomplete", "사이트 조사가 끝나지 않아 탐색할 수 없습니다", {"missing": research.missing})
        if source.stopped_reason:
            raise ApiError(409, "source_stopped", f"정지된 소스입니다: {source.stopped_reason}")
        run, _ = runq.enqueue(s, body.kind, source_id=source.id, idempotency_key=body.idempotency_key)
        s.flush()
        ctx.hub.notify()
        return S.JobAccepted(job_id=run.id, run_id=run.id)

    @app.get("/v1/runs", response_model=S.RunPage, tags=["runs"], responses=ERRORS)
    def list_runs(
        s: Sess,
        source_id: str | None = None,
        kind: Annotated[list[S.RunKind] | None, Query()] = None,
        state: Annotated[list[S.RunState] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
    ) -> S.RunPage:
        q = select(Run)
        if source_id:
            q = q.where(Run.source_id == source_id)
        if kind:
            q = q.where(Run.kind.in_(kind))
        if state:
            q = q.where(Run.state.in_(state))
        if cursor:
            q = q.where(Run.id < cursor)
        rows = s.scalars(q.order_by(Run.id.desc()).limit(limit + 1)).all()
        items = rows[:limit]
        return S.RunPage(items=[source_svc.run_out(r) for r in items], next_cursor=items[-1].id if len(rows) > limit else None, has_more=len(rows) > limit)

    @app.get("/v1/runs/{run_id}", response_model=S.RunOut, tags=["runs"], responses=ERRORS)
    def get_run(run_id: str, s: Sess) -> S.RunOut:
        run = s.get(Run, run_id)
        if run is None:
            raise ApiError(404, "not_found", "실행을 찾을 수 없습니다")
        return source_svc.run_out(run)

    @app.post("/v1/runs/{run_id}/actions", response_model=S.RunOut, tags=["runs"], responses=ERRORS)
    def run_action(run_id: str, body: S.RunActionIn, s: Sess) -> S.RunOut:
        run = s.get(Run, run_id)
        if run is None:
            raise ApiError(404, "not_found", "실행을 찾을 수 없습니다")
        runq.request_action(s, run, body.action)
        s.flush()
        ctx.hub.notify()
        return source_svc.run_out(run)

    # ── 리드 ────────────────────────────────────────────────────────
    @app.get("/v1/leads", response_model=S.LeadPage, tags=["leads"], responses=ERRORS)
    def list_leads(
        s: Sess,
        f: Filter,
        queue: S.QueueId = "all",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
    ) -> S.LeadPage:
        items, nxt = lead_svc.list_leads(s, queue, f, cursor, limit)
        return S.LeadPage(items=lead_svc.summaries(s, items), next_cursor=nxt, has_more=nxt is not None)

    @app.get("/v1/leads/queue-counts", response_model=S.QueueCounts, tags=["leads"], responses=ERRORS)
    def counts(s: Sess, f: Filter) -> S.QueueCounts:
        return S.QueueCounts(counts=lead_svc.queue_counts(s, f))  # type: ignore[arg-type]

    @app.get("/v1/leads/{lead_id}", response_model=S.LeadDetail, tags=["leads"], responses=ERRORS)
    def lead_detail(lead_id: str, s: Sess) -> S.LeadDetail:
        return lead_svc.detail(s, get_lead(s, lead_id))

    @app.patch("/v1/leads/{lead_id}", response_model=S.LeadDetail, tags=["leads"], responses=ERRORS)
    def patch_lead(lead_id: str, body: S.LeadPatch, s: Sess) -> S.LeadDetail:
        lead = get_lead(s, lead_id)
        now = utcnow()
        fields = body.model_fields_set
        if "user_mark" in fields and body.user_mark != lead.user_mark:
            lead.user_mark = body.user_mark
            text = {"interested": "관심 표시", "dismissed": "제외 표시", None: "관심·제외 해제"}[body.user_mark]
            s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now, kind="user_mark", text=text))
        if "sales_stage" in fields and body.sales_stage and body.sales_stage != lead.sales_stage:
            s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now, kind="sales_stage", text=f"진행 상태 {lead.sales_stage} → {body.sales_stage}"))
            lead.sales_stage = body.sales_stage
        if "memo" in fields and body.memo is not None and body.memo != lead.memo:
            lead.memo = body.memo
            s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now, kind="memo", text="메모 수정"))
        if "draft_text" in fields and body.draft_text is not None:
            lead.draft_text = body.draft_text
            lead.draft_edited = True
        lead.updated_at = now
        emit(s, "lead.updated", {"lead_id": lead.id, "changes": sorted(fields)})
        s.flush()
        ctx.hub.notify()
        return lead_svc.detail(s, lead)

    def _lead_job(s: Any, lead: Lead, kind: str, job: str) -> S.JobAccepted:
        run, created = runq.enqueue(s, kind, lead_id=lead.id, source_id=lead.source_id if kind == "recheck_lead" else None)
        if created:
            lead.active_job = job
            emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["active_job"]})
        s.flush()
        ctx.hub.notify()
        return S.JobAccepted(job_id=run.id, run_id=run.id)

    @app.post("/v1/leads/{lead_id}/recheck", status_code=202, response_model=S.JobAccepted, tags=["leads"], responses=ERRORS)
    def recheck(lead_id: str, s: Sess) -> S.JobAccepted:
        lead = get_lead(s, lead_id)
        source = s.get(Source, lead.source_id)
        if source is not None and source.kind == "manual":
            raise ApiError(409, "unsupported", "수동 입력 리드는 원문을 대신 확인하지 않습니다. 원문을 직접 열어 확인하세요.")
        policy = s.get(SourcePolicy, lead.source_id)
        if policy is None or policy.status not in ("allowed", "restricted"):
            raise ApiError(409, "policy_not_allowed", "이 소스는 자동 접근 권한이 확인되지 않아 재확인할 수 없습니다. 원문을 직접 확인하세요.")
        return _lead_job(s, lead, "recheck_lead", "recheck")

    @app.post("/v1/leads/{lead_id}/reanalyze", status_code=202, response_model=S.JobAccepted, tags=["leads"], responses=ERRORS)
    def reanalyze(lead_id: str, s: Sess) -> S.JobAccepted:
        return _lead_job(s, get_lead(s, lead_id), "reanalyze_lead", "reanalyze")

    @app.post("/v1/leads/{lead_id}/feedback", response_model=S.LeadDetail, tags=["leads"], responses=ERRORS)
    def feedback(lead_id: str, body: S.FeedbackIn, s: Sess) -> S.LeadDetail:
        lead = get_lead(s, lead_id)
        valid = {"remote": {"confirmed", "denied", "clear"}, "real_request": {"yes", "no", "clear"}}
        if body.value not in valid[body.kind]:
            raise ApiError(422, "invalid_feedback", f"{body.kind} 에 쓸 수 없는 값입니다: {body.value}")
        now = utcnow()
        s.add(UserFeedback(id=new_id("fb"), lead_id=lead.id, kind=body.kind, value=body.value, note=body.note, created_at=now))
        label = {"confirmed": "재택 가능 확인", "denied": "재택 불가 확인", "yes": "실제 의뢰로 확인", "no": "실제 의뢰 아님으로 표시", "clear": "내 확인 취소"}[body.value]
        s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now, kind="feedback", text=label))
        s.flush()
        rec = s.get(SourceRecord, lead.primary_record_id)
        src = s.get(Source, lead.source_id)
        if rec is not None:
            apply_analysis(s, lead, rec, now=now, provider=ctx.provider, source_kind=src.kind if src else "site")
            if getattr(ctx.provider, "requires_deferred", False):
                runq.enqueue(s, "reanalyze_lead", lead_id=lead.id, input={"retry_ai": False}, trigger="feedback")
        emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["feedback"]})
        s.flush()
        ctx.hub.notify()
        return lead_svc.detail(s, lead)

    @app.post("/v1/leads/{lead_id}/relations/{relation_id}", response_model=S.RelationUpdateOut, tags=["leads"], responses=ERRORS)
    def relation(lead_id: str, relation_id: str, body: S.RelationUpdateIn, s: Sess) -> S.RelationUpdateOut:
        lead = get_lead(s, lead_id)
        if body.expected_generation != s.info["database_generation"]:
            raise ApiError(409, "relation_changed", "연결 정보가 갱신되었습니다. 관련 글을 다시 불러온 뒤 확인하세요")
        try:
            other = relation_svc.update_decision(s, lead, relation_id, body.action, body.expected_revision)
        except relation_svc.RelationMissing:
            raise ApiError(404, "not_found", "이 리드의 관련 글 연결을 찾을 수 없습니다") from None
        except relation_svc.RelationConflict:
            raise ApiError(409, "relation_changed", "관련 글 연결 상태가 변경되었습니다. 다시 불러온 뒤 확인하세요") from None
        ctx.hub.notify()
        return S.RelationUpdateOut(lead=lead_svc.detail(s, lead), related_lead=lead_svc.detail(s, other))

    @app.post("/v1/leads/{lead_id}/outcomes", response_model=S.LeadDetail, tags=["leads"], responses=ERRORS)
    def add_outcome(lead_id: str, body: S.OutcomeIn, s: Sess) -> S.LeadDetail:
        lead = get_lead(s, lead_id)
        now = utcnow()
        s.add(
            Outcome(
                id=new_id("out"),
                lead_id=lead.id,
                kind=body.kind,
                amount=body.amount,
                currency=body.currency,
                occurred_on=body.occurred_on.isoformat(),
                note=body.note,
                evidence_ref=body.evidence_ref,
                created_at=now,
            )
        )
        label = {"contract_confirmed": "계약 확인", "payment_received": "수금", "refund": "환불", "direct_cost": "직접 비용"}[body.kind]
        amount_text = (f"{body.amount:,}원" if body.currency == "KRW" else f"{body.currency} {body.amount:,} (최소 통화 단위)") if body.amount is not None else None
        s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now, kind="outcome", text=f"{label} 기록" + (f" {amount_text}" if amount_text is not None else "")))
        emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["outcomes"]})
        s.flush()
        ctx.hub.notify()
        return lead_svc.detail(s, lead)

    @app.post("/v1/leads/{lead_id}/draft", response_model=S.LeadDetail, tags=["leads"], responses=ERRORS)
    def make_draft(lead_id: str, s: Sess) -> S.LeadDetail:
        """문의 초안 생성 (규칙 템플릿). 발송하지 않는다."""
        lead = get_lead(s, lead_id)
        if lead.draft_text and lead.draft_edited:
            raise ApiError(409, "draft_edited", "직접 수정한 초안이 있어 덮어쓰지 않았습니다")
        now = utcnow()
        lead.draft_text = generate_draft(s, lead, now)
        lead.draft_generated_at = now
        lead.draft_edited = False
        s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now, kind="draft", text="문의 초안 생성"))
        s.flush()
        return lead_svc.detail(s, lead)

    # ── 설정 ────────────────────────────────────────────────────────
    def credential_status() -> CredentialStatus:
        if ctx.config.demo:
            return CredentialStatus(False, None, "demo_credential_unsupported")
        return ctx.credential_store.status(ctx.config.data_dir, "openai")

    def settings_out(s: Any) -> S.SettingsOut:
        prof = settings_svc.get_profile_row(s)
        data = {**settings_svc.DEFAULT_PROFILE, **(prof.data or {})}
        ai = settings_svc.get_setting(s, "ai")
        ai["pricing_confirmed"] = bool(ai.get("pricing_confirmed") and ai.get("price_version") == PRICE_VERSION)
        key = credential_status()
        return S.SettingsOut(
            profile=S.ProfileSettings(**data),
            recheck=settings_svc.get_setting(s, "recheck"),
            notifications=S.NotificationSettings(**settings_svc.get_setting(s, "notifications")),
            ai=S.AISettings(**ai, key_configured=key.configured,
                            provider_available=bool(key.supported and key.configured),
                            revision=settings_svc.get_setting(s, "ai_revision"),
                            database_generation=s.info["database_generation"]),
            retention=settings_svc.get_setting(s, "retention"),
            query_groups=S.QueryGroups(**settings_svc.get_setting(s, "query_groups")),
        )

    @app.get("/v1/settings", response_model=S.SettingsOut, tags=["settings"], responses=ERRORS)
    def get_settings(s: Sess) -> S.SettingsOut:
        return settings_out(s)

    @app.patch("/v1/settings", response_model=S.SettingsOut, tags=["settings"], responses=ERRORS)
    def patch_settings(body: S.SettingsPatch, s: Sess) -> S.SettingsOut:
        with ctx.ai_control_lock:
            result = _patch_settings(body, s)
            # All validation and related writes succeed as one transaction.
            s.commit()
            return result

    def _patch_settings(body: S.SettingsPatch, s: Any) -> S.SettingsOut:
        rerun = False
        if body.profile is not None:
            rerun = settings_svc.update_profile(s, body.profile.model_dump()) or rerun
        if body.recheck is not None:
            ttl = int(body.recheck.get("ttl_hours", 24))
            if not 1 <= ttl <= 24 * 30:
                raise ApiError(422, "invalid_ttl", "재확인 주기는 1시간~30일 사이여야 합니다")
            settings_svc.put_setting(s, "recheck", {"ttl_hours": ttl})
            rerun = True
        if body.notifications is not None:
            settings_svc.put_setting(s, "notifications", body.notifications.model_dump())
        if body.ai is not None:
            with ctx.ai_control_lock:
                current = settings_svc.get_setting(s, "ai")
                patch = body.ai.model_dump(exclude_unset=True)
                expected_revision = patch.pop("expected_revision", None)
                expected_generation = patch.pop("expected_generation", None)
                nxt = {**current, **patch}
                if nxt.get("enabled") and not nxt.get("external_transfer_consent"):
                    raise ApiError(422, "consent_required", "외부 AI 전송 고지에 동의해야 AI 분석을 켤 수 있습니다")
                if expected_generation is not None and expected_generation != s.info["database_generation"]:
                    raise settings_svc.AISettingsChanged()
                revision = settings_svc.get_setting(s, "ai_revision")
                if getattr(ctx.provider, "requires_deferred", False) and (current.get("enabled") or nxt.get("enabled")):
                    if expected_revision is None or expected_generation is None:
                        raise ApiError(409, "ai_settings_changed", "최신 AI 상태를 불러온 뒤 설정을 저장하세요")
                if (nxt.get("model"), nxt.get("usd_to_krw_rate")) != (current.get("model"), current.get("usd_to_krw_rate")) and patch.get("pricing_confirmed") is not True:
                    nxt["pricing_confirmed"] = False
                if patch.get("pricing_confirmed") is True:
                    nxt["price_version"] = PRICE_VERSION
                elif not nxt.get("pricing_confirmed"):
                    nxt["price_version"] = None
                if patch:
                    settings_svc.advance_ai_revision(s, revision if expected_revision is None else expected_revision)
                    settings_svc.put_setting(s, "ai", nxt)
                    if not nxt.get("enabled") or not nxt.get("external_transfer_consent"):
                        for lead_id in settings_svc.stop_pending_ai(s):
                            emit(s, "lead.updated", {"lead_id": lead_id, "changes": ["analysis"]})
        if body.retention is not None:
            days = int(body.retention.get("raw_days", 30))
            if not 1 <= days <= 365:
                raise ApiError(422, "invalid_retention", "원문 보존 기간은 1~365일 사이여야 합니다")
            settings_svc.put_setting(s, "retention", {"raw_days": days})
        if body.query_groups is not None:
            current = settings_svc.get_setting(s, "query_groups")
            settings_svc.put_setting(s, "query_groups", {"version": int(current.get("version", 1)) + 1, "groups": [g.model_dump() for g in body.query_groups]})
        if rerun:
            runq.enqueue(s, "reanalyze_all", idempotency_key=f"reanalyze_all:{settings_svc.get_profile_row(s).version}:{settings_svc.ttl_hours(s)}")
        s.flush()
        ctx.hub.notify()
        return settings_out(s)

    def ai_status_out(s: Any) -> S.AIStatus:
        config = settings_svc.get_setting(s, "ai")
        key = credential_status()
        error_code = key.error_code
        try:
            status = ctx.ai_coordinator.status()
        except Exception:
            # Never expose an OS path, SQL diagnostic or provider exception.
            status = ctx.ai_coordinator.catalog_status()
            status["ledger"] = None
            error_code = "ai_ledger_unavailable"
        reason = error_code
        if reason is None:
            if not config.get("enabled"):
                reason = "disabled"
            elif not config.get("external_transfer_consent"):
                reason = "consent_required"
            elif key.configured is not True:
                reason = "not_configured"
            elif not config.get("pricing_confirmed") or config.get("price_version") != PRICE_VERSION or config.get("usd_to_krw_rate") is None:
                reason = "pricing_required"
            elif config.get("daily_cost_cap") is None or config.get("monthly_cost_cap") is None:
                reason = "budget_required"
            elif status.get("ledger"):
                for period, cap in (("day", config["daily_cost_cap"]), ("month", config["monthly_cost_cap"])):
                    ledger = status["ledger"][period]
                    if sum(ledger[k] for k in ("estimated_krw", "reserved_krw", "uncertain_krw")) >= cap:
                        reason = "budget_exhausted"
                        break
        return S.AIStatus(**status, key_supported=key.supported, key_configured=key.configured,
                          error_code=error_code, enabled=config["enabled"], ready=reason is None,
                          blocked_reason=reason, settings_revision=settings_svc.get_setting(s, "ai_revision"),
                          database_generation=s.info["database_generation"])

    @app.get("/v1/ai/status", response_model=S.AIStatus, tags=["settings"], responses=ERRORS)
    def ai_status(s: Sess) -> S.AIStatus:
        return ai_status_out(s)

    def ai_credential_action(body: S.AICredentialAction, s: Any, key: str | None) -> S.AIStatus:
        with ctx.ai_control_lock:
            if ctx.config.demo:
                raise ApiError(409, "demo_credential_unsupported", "데모에서는 실제 AI 키를 저장하거나 삭제하지 않습니다")
            if body.expected_generation != s.info["database_generation"]:
                raise settings_svc.AISettingsChanged()
            settings_svc.advance_ai_revision(s, body.expected_revision)
            config = settings_svc.get_setting(s, "ai")
            settings_svc.put_setting(s, "ai", {**config, "enabled": False})
            for lead_id in settings_svc.stop_pending_ai(s):
                emit(s, "lead.updated", {"lead_id": lead_id, "changes": ["analysis"]})
            # OS and SQLite are separate stores. Commit disabled first so an OS
            # failure or process exit cannot silently leave paid analysis on.
            s.commit()
            ctx.hub.notify()
            if key is None:
                ctx.credential_store.delete_key(ctx.config.data_dir, "openai")
            else:
                ctx.credential_store.set_key(ctx.config.data_dir, "openai", key)
            return ai_status_out(s)

    @app.post("/v1/ai/credentials", response_model=S.AIStatus, tags=["settings"], responses=ERRORS)
    def save_ai_credential(body: S.AICredentialIn, s: Sess) -> S.AIStatus:
        return ai_credential_action(body, s, body.key.get_secret_value())

    @app.post("/v1/ai/credentials/delete", response_model=S.AIStatus, tags=["settings"], responses=ERRORS)
    def delete_ai_credential(body: S.AICredentialAction, s: Sess) -> S.AIStatus:
        return ai_credential_action(body, s, None)

    # ── 가져오기·내보내기 ─────────────────────────────────────────────
    @app.post("/v1/imports", status_code=202, response_model=S.JobAccepted, tags=["imports"], responses=ERRORS)
    def create_import(body: S.ImportIn, s: Sess) -> S.JobAccepted:
        if len(body.content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ApiError(422, "too_large", "입력은 최대 2MB 입니다")
        if not body.content.strip():
            raise ApiError(422, "empty", "내용이 비어 있습니다")
        run, _ = runq.enqueue(s, "import", source_id="manual", input=body.model_dump(), exclusive=False)
        s.flush()
        ctx.hub.notify()
        return S.JobAccepted(job_id=run.id, run_id=run.id)

    @app.post("/v1/exports", status_code=202, response_model=S.JobAccepted, tags=["imports"], responses=ERRORS)
    def create_export(body: S.ExportIn, s: Sess) -> S.JobAccepted:
        run, _ = runq.enqueue(s, "export", input=body.model_dump(), exclusive=False)
        s.flush()
        ctx.hub.notify()
        return S.JobAccepted(job_id=run.id, run_id=None)

    # ── 백업·복원 ──────────────────────────────────────────────────
    @app.get("/v1/backups", response_model=S.BackupList, tags=["backups"], responses=ERRORS)
    def list_backups() -> S.BackupList:
        return backup_svc.list_backups(ctx)

    @app.post("/v1/backups", response_model=S.BackupOut, status_code=201, tags=["backups"], responses=ERRORS)
    def create_backup() -> S.BackupOut:
        return backup_svc.create_backup(ctx)

    @app.get("/v1/backups/{backup_id}", response_model=S.BackupInspection, tags=["backups"], responses=ERRORS)
    def inspect_backup(backup_id: str) -> S.BackupInspection:
        return backup_svc.inspect_backup(ctx, backup_id)

    @app.post("/v1/backups/{backup_id}/restore", response_model=S.RestoreOut, tags=["backups"], responses=ERRORS)
    def restore_backup(backup_id: str, body: S.RestoreIn) -> S.RestoreOut:
        return backup_svc.restore_backup(ctx, backup_id, body.sha256)

    # ── 네이티브 알림 ────────────────────────────────────────────────
    @app.get("/v1/native-notifications", response_model=S.NativeNotificationList, tags=["notifications"], responses=ERRORS)
    def list_native_notifications(limit: int = Query(default=20, ge=1, le=100), created_after: datetime | None = None) -> S.NativeNotificationList:
        if created_after is not None and created_after.tzinfo is None:
            raise ApiError(422, "invalid_time", "created_after는 UTC 오프셋이 있는 ISO 시각이어야 합니다")
        return notification_svc.list_pending(ctx, limit=limit, created_after=created_after)

    @app.post("/v1/native-notifications/{notification_id}/claim", response_model=S.NativeNotificationClaimOut, tags=["notifications"], responses=ERRORS)
    def claim_native_notification(notification_id: str) -> S.NativeNotificationClaimOut:
        return notification_svc.claim(ctx, notification_id)

    @app.post("/v1/native-notifications/{notification_id}/result", response_model=S.NativeNotificationResultOut, tags=["notifications"], responses=ERRORS)
    def native_notification_result(notification_id: str, body: S.NativeNotificationResultIn) -> S.NativeNotificationResultOut:
        return notification_svc.record_result(ctx, notification_id, claim_token=body.claim_token, state=body.state)

    # ── 지표 ────────────────────────────────────────────────────────
    @app.get("/v1/metrics", response_model=S.MetricsOut, tags=["system"], responses=ERRORS)
    def get_metrics(s: Sess) -> S.MetricsOut:
        return metrics_svc.metrics(s)

    # ── 이벤트 (SSE) ─────────────────────────────────────────────────
    @app.get(
        "/v1/events",
        tags=["events"],
        responses={200: {"content": {"text/event-stream": {}}, "description": "SSE. Authorization 헤더 필요 (fetch 스트리밍). Last-Event-ID 또는 after_seq 로 이어받기."}, **ERRORS},
    )
    async def events_stream(request: Request, after_seq: int | None = None) -> StreamingResponse:
        last_header = request.headers.get("last-event-id")
        start = after_seq if after_seq is not None else int(last_header) if last_header and last_header.isdigit() else None

        async def gen() -> AsyncIterator[bytes]:
            nonlocal start
            generation = ctx.db.gate.generation
            while True:
                try:
                    with ctx.db.session() as s:
                        trimmed = int(settings_svc.get_setting(s, "events_trimmed_through"))
                        head = s.scalar(select(func.max(EventOutbox.seq))) or 0
                    break
                except MaintenanceBusy:
                    if await request.is_disconnected():
                        return
                    await asyncio.sleep(0.1)
            if start is None:
                start = head
            elif start < trimmed or start > head:
                # 보존 범위를 벗어난 이어받기 → 전체 재조회 요청
                yield _sse({"type": "stream.reset", "seq": head, "id": f"reset-{head}", "at": utcnow().isoformat()}, head)
                start = head
            yield b": connected\n\n"
            version = 0
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    with ctx.db.session() as s:
                        if generation != ctx.db.gate.generation:
                            generation = ctx.db.gate.generation
                            start = s.scalar(select(func.max(EventOutbox.seq))) or 0
                            reset = True
                        else:
                            reset = False
                        rows = s.scalars(select(EventOutbox).where(EventOutbox.seq > start).order_by(EventOutbox.seq).limit(200)).all()
                        batch = [(r.seq, r.id, r.type, r.payload, r.created_at, r.suppressed_reason) for r in rows]
                except MaintenanceBusy:
                    await asyncio.sleep(0.1)
                    continue
                if reset:
                    yield _sse({"type": "stream.reset", "seq": start, "id": f"restore-{generation}", "at": utcnow().isoformat()}, start)
                for seq, eid, typ, payload, at, suppressed in batch:
                    if generation != ctx.db.gate.generation:
                        break
                    data = _event_data(seq, eid, typ, payload, at, suppressed)
                    yield _sse(data, seq)
                    start = seq
                if batch:
                    idle = 0.0
                    continue
                version = await asyncio.to_thread(ctx.hub.wait, version, 1.0)
                idle += 1.0
                if idle >= 15:
                    idle = 0.0
                    yield b": keepalive\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return app


def _event_data(seq: int, eid: str, typ: str, payload: dict[str, Any], at: datetime, suppressed: str | None) -> dict[str, Any]:
    # Delivery claims are private to the native consumer, including on SSE replay.
    public = {key: value for key, value in payload.items() if key != "native_delivery"}
    data = {"id": eid, "seq": seq, "type": typ, "at": at.isoformat(), **public}
    if typ == "notification.created":
        data["suppressed_reason"] = suppressed
    return data


def _sse(data: dict[str, Any], seq: int) -> bytes:
    return f"id: {seq}\nevent: {data['type']}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()
