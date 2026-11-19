"""Prepare -> durable reserve -> external request -> guarded result application.

The semantic cache remains stable across credentials, budget, consent and DB
restore. A separate execution guard includes those mutable permissions. No
network request or ledger transaction runs inside the source write session.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Callable

from sqlalchemy import select

from ..models import AnalysisResult, Lead, Source, SourceRecord
from ..services.credentials import CredentialError
from ..services.settings import get_setting
from .ledger import CostLedger
from .openai_provider import MODEL, PRICE_DATE, PRICE_VERSION, OpenAIProvider, estimated_cost, prepare_request, price_metadata
from .provider import AIInput, AIOutput, AIFailure, assist_eligible, mask_personal

if TYPE_CHECKING:
    from ..context import AppContext

CONFIG_KEYS = ("enabled", "external_transfer_consent", "provider", "model", "usd_to_krw_rate",
               "max_output_tokens", "pricing_confirmed", "daily_cost_cap", "monthly_cost_cap")
CONFIG_KEYS = (*CONFIG_KEYS, "price_version")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def guard_hash(cache_key: str, config: dict, revision: int, generation: int) -> str:
    return digest({"cache": cache_key, "config": {k: config.get(k) for k in CONFIG_KEYS},
                   "revision": revision, "database_generation": generation})


class ManagedProvider:
    engine = "openai"
    model = MODEL
    requires_deferred = True

    def __init__(self, app: AppContext):
        self.app = app

    def available(self) -> bool:
        if self.app.config.demo:
            return False
        state = self.app.credential_store.status(self.app.config.data_dir, "openai")
        return bool(state.supported and state.configured is True and state.error_code is None)

    def analyze(self, data: AIInput) -> AIOutput:
        raise AIFailure("deferred_required", "외부 AI는 저장 트랜잭션 밖에서 실행해야 합니다")


@dataclass(frozen=True)
class Ticket:
    lead_id: str
    record_id: str
    analysis_id: str
    cache_key: str
    guard: str
    config: dict
    data: AIInput = field(repr=False)


class AnalysisCoordinator:
    def __init__(self, app: AppContext):
        self.app = app
        self.ledger = CostLedger(app.config.data_dir / ("ai-ledger-demo.sqlite3" if app.config.demo else "ai-ledger.sqlite3"))

    def catalog_status(self) -> dict:
        return {"provider": "openai", "model": MODEL, "provider_available": self.app.provider.available(),
                "pricing_checked_at": PRICE_DATE, "price_metadata": price_metadata()}

    def status(self) -> dict:
        return {**self.catalog_status(), "ledger": self.ledger.status(self.app.clock())}

    def _prepare(self, lead_id: str) -> Ticket | None:
        from ..engine.pipeline import apply_analysis
        if self.app.config.demo:
            return None
        with self.app.ai_control_lock, self.app.db.session() as s:
            cfg = get_setting(s, "ai")
            if not cfg.get("enabled") or not cfg.get("external_transfer_consent"):
                return None
            lead = s.get(Lead, lead_id)
            rec = s.get(SourceRecord, lead.primary_record_id) if lead and lead.primary_record_id else None
            if lead is None or rec is None:
                return None
            source = s.get(Source, lead.source_id)
            ra = apply_analysis(s, lead, rec, now=self.app.clock(), provider=self.app.provider, source_kind=source.kind if source else "site")
            if not assist_eligible(ra):
                return None
            row = s.get(AnalysisResult, lead.analysis_id)
            if row is None or row.engine != "ai:openai":
                return None
            data = AIInput(mask_personal(rec.title), mask_personal(rec.body_text or ""),
                           list(ra.categories), mask_personal(ra.summary) if ra.summary else None)
            return Ticket(lead.id, rec.id, row.id, row.cache_key,
                          guard_hash(row.cache_key, cfg, get_setting(s, "ai_revision"), self.app.db.gate.generation), cfg, data)

    def _current(self, ticket: Ticket, s) -> tuple[Lead, SourceRecord, AnalysisResult] | None:
        from ..engine.pipeline import apply_analysis
        lead, rec = s.get(Lead, ticket.lead_id), s.get(SourceRecord, ticket.record_id)
        if lead is None or rec is None or lead.primary_record_id != rec.id:
            return None
        cfg = get_setting(s, "ai")
        if not cfg.get("enabled") or not cfg.get("external_transfer_consent"):
            return None
        source = s.get(Source, lead.source_id)
        apply_analysis(s, lead, rec, now=self.app.clock(), provider=self.app.provider, source_kind=source.kind if source else "site")
        row = s.get(AnalysisResult, lead.analysis_id)
        if row is None or row.cache_key != ticket.cache_key:
            return None
        if guard_hash(row.cache_key, cfg, get_setting(s, "ai_revision"), self.app.db.gate.generation) != ticket.guard:
            return None
        return lead, rec, row

    @staticmethod
    def _ready(cfg: dict) -> None:
        if cfg.get("provider") != "openai" or cfg.get("model") != MODEL:
            raise AIFailure("unsupported_model", "지원하는 AI 모델을 확인하세요")
        if not cfg.get("pricing_confirmed") or cfg.get("price_version") != PRICE_VERSION or type(cfg.get("usd_to_krw_rate")) is not int or not 1 <= cfg["usd_to_krw_rate"] <= 100000:
            raise AIFailure("pricing_required", "공식 요금과 환산 추정값 확인이 필요합니다")
        if any(type(cfg.get(k)) is not int or cfg[k] <= 0 for k in ("daily_cost_cap", "monthly_cost_cap")):
            raise AIFailure("budget_required", "양수 일일·월간 비용 상한이 필요합니다")

    def _apply(self, ticket: Ticket, payload: dict | None, failure: str | None) -> bool:
        from ..engine.events import emit
        with self.app.ai_control_lock, self.app.db.session() as s:
            current = self._current(ticket, s)
            if current is None:
                # A changed permission/profile/content must not leave a
                # permanently pending old analysis or overwrite current data.
                from ..engine.pipeline import apply_analysis
                lead = s.get(Lead, ticket.lead_id)
                rec = s.get(SourceRecord, lead.primary_record_id) if lead and lead.primary_record_id else None
                if lead is not None and rec is not None:
                    source = s.get(Source, lead.source_id)
                    apply_analysis(s, lead, rec, now=self.app.clock(), provider=self.app.provider, source_kind=source.kind if source else "site")
                    row = s.get(AnalysisResult, lead.analysis_id)
                    if row is not None and row.status == "pending":
                        row.status = lead.analysis_status = "stale"
                return False
            lead, rec, row = current
            body = dict(row.payload or {})
            ai = payload or {}
            if not failure:
                body.update({"summary": ai.get("summary") or body.get("summary"),
                             "summary_engine": "ai" if ai.get("summary") else "rules",
                             "deliverables": ai.get("deliverables") or body.get("deliverables", []),
                             "tech": ai.get("tech") or body.get("tech", []),
                             "uncertain": list(dict.fromkeys(body.get("uncertain", []) + ai.get("uncertain", []))),
                             "questions": list(dict.fromkeys(body.get("questions", []) + ai.get("questions", [])))[:5]})
            body["ai"] = ai
            row.payload = body
            row.status = "failed" if failure else "complete"
            row.failure_code, row.failure_message = failure, "AI 분석 실패 — 규칙 결과는 유지됩니다" if failure else None
            row.tokens_in, row.tokens_out = ai.get("tokens_in"), ai.get("tokens_out")
            row.latency_ms, row.cost_krw = ai.get("latency_ms"), None
            lead.analysis_status = row.status
            if failure and len(lead.reasons or []) < 3:
                lead.reasons = list(lead.reasons or []) + [{"tone": "negative", "text": "AI 분석 실패 — 규칙 결과만"}]
            emit(s, "analysis.completed", {"lead_id": lead.id, "status": lead.analysis_status})
            emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["analysis"]})
        self.app.hub.notify()
        return True

    def _discard_preflight(self, ticket: Ticket) -> bool:
        # Recompute current rule status when consent or inputs changed before
        # transmission, so the old pending ticket does not hang indefinitely.
        return self._apply(ticket, None, "stale_result")

    def analyze_lead(self, lead_id: str, *, retry: bool = False, checkpoint: Callable[[], None] | None = None) -> bool:
        if not getattr(self.app.provider, "requires_deferred", False):
            return False
        checkpoint = checkpoint or (lambda: None)
        with self.app.db.gate.operation():
            ticket = self._prepare(lead_id)
            if ticket is None:
                return False
            claim = None
            sent = False
            try:
                checkpoint()
                cached = self.ledger.lookup(ticket.cache_key)
                if cached is not None:
                    if cached.kind == "complete":
                        return self._apply(ticket, cached.payload, None)
                    if not retry:
                        return self._apply(ticket, None, cached.error_code or "billing_uncertain")
                request = prepare_request(ticket.data, ticket.config.get("model"), ticket.config.get("max_output_tokens"))
                self._ready(ticket.config)
                rate = ticket.config["usd_to_krw_rate"]
                _, reserve = estimated_cost((request.input_bound, request.max_output, 0), rate)
                # Config/key read, cache claim and reservation commit linearize
                # against AI controls. The lock is released before HTTP.
                with self.app.ai_control_lock:
                    with self.app.db.session() as s:
                        valid = self._current(ticket, s) is not None
                    if not valid:
                        return self._discard_preflight(ticket)
                    try:
                        key = self.app.credential_store.get_key(self.app.config.data_dir, "openai")
                    except CredentialError as exc:
                        raise AIFailure(exc.code, "AI 비밀 저장소를 확인할 수 없습니다") from None
                    if key is None:
                        raise AIFailure("not_configured", "AI API 키가 설정되지 않았습니다")
                    claim = self.ledger.reserve(ticket.cache_key, ticket.guard, reserve,
                                                day_cap=ticket.config["daily_cost_cap"], month_cap=ticket.config["monthly_cost_cap"], now=self.app.clock(), retry=retry)
                if claim.kind == "complete":
                    return self._apply(ticket, claim.payload, None)
                if claim.kind != "reserved":
                    return self._apply(ticket, None, claim.error_code or "billing_uncertain")
                try:
                    checkpoint()
                    with self.app.ai_control_lock:
                        with self.app.db.session() as s:
                            valid = self._current(ticket, s) is not None
                        if not valid:
                            self.ledger.release(claim.attempt_id)
                            return self._discard_preflight(ticket)
                        self.ledger.sent(claim.attempt_id)
                        sent = True
                except BaseException:
                    self.ledger.release(claim.attempt_id)
                    raise
                output = OpenAIProvider(transport=self.app.ai_transport).execute(ticket.data, request, key)
                usage = (output.tokens_in, output.tokens_out, output.cached_tokens or 0)
                usd, cost = estimated_cost(usage, rate)
                output.estimated_usd_micros, output.estimated_cost_krw = usd, cost
                output.reserved_cost_krw, output.cost_basis = reserve, "published_rates_user_fx_estimate"
                output.price_version, output.usd_to_krw_rate = PRICE_VERSION, rate
                payload = asdict(output)
                self.ledger.settle(claim.attempt_id, payload=payload, error_code=None, usage=usage, estimate=(usd, cost))
                applied = self._apply(ticket, payload, None)
                checkpoint()
                return applied
            except AIFailure as exc:
                if claim is not None and claim.kind == "reserved":
                    if sent:
                        estimate = estimated_cost(exc.usage, ticket.config["usd_to_krw_rate"]) if exc.usage is not None and exc.code not in {"model_mismatch", "pricing_mismatch"} else None
                        self.ledger.settle(claim.attempt_id, payload=None, error_code=exc.code, usage=exc.usage, estimate=estimate)
                    else:
                        self.ledger.release(claim.attempt_id)
                return self._apply(ticket, None, exc.code)
