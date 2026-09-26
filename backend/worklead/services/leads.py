"""리드 목록 조회(필터·대기열·안정 커서)와 직렬화."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, not_, or_, select
from sqlalchemy.orm import Session

from ..api import schemas as S
from ..db import utcnow
from ..models import AnalysisResult, DiscoveryPath, DuplicateLink, FieldEvidence, Lead, LeadActivity, Outcome, ScoreBreakdown, SourceRecord
from .settings import ttl_hours

QUEUES: list[tuple[str, str, str]] = [
    ("recommended", "추천", "자동 판정 추천 · 내가 제외하지 않음 · 진행 전"),
    ("needs_review", "확인 필요", "유망하지만 재택·모집·외주 여부 등 확인 필요 · 진행 전"),
    ("interested", "관심", "관심 표시 · 진행 전"),
    ("active", "진행 중", "연락함·협상 중·수주·보류"),
    ("dismissed", "내가 제외", "내가 제외한 리드"),
    ("auto_excluded", "자동 제외", "마감·판매자 홍보·출근 필수·위험 신호 등"),
    ("all", "전체", "모든 리드"),
]

PRE_STAGES = ("new", "reviewing")
ACTIVE_STAGES = ("contacted", "negotiating", "won", "on_hold")


class CursorError(ValueError):
    pass


def _not_dismissed():
    return and_(or_(Lead.user_mark.is_(None), Lead.user_mark != "dismissed"), Lead.sales_stage != "ignored")


def queue_clause(queue: str):
    if queue == "recommended":
        return and_(Lead.recommendation == "recommended", _not_dismissed(), Lead.sales_stage.in_(PRE_STAGES))
    if queue == "needs_review":
        return and_(Lead.recommendation == "needs_review", _not_dismissed(), Lead.sales_stage.in_(PRE_STAGES))
    if queue == "auto_excluded":
        return and_(Lead.recommendation == "excluded", _not_dismissed(), Lead.sales_stage.in_(PRE_STAGES))
    if queue == "interested":
        return and_(Lead.user_mark == "interested", Lead.sales_stage.in_(PRE_STAGES))
    if queue == "active":
        return Lead.sales_stage.in_(ACTIVE_STAGES)
    if queue == "dismissed":
        return or_(Lead.user_mark == "dismissed", Lead.sales_stage == "ignored")
    return None


def filter_clauses(f: dict[str, Any], now: datetime, ttl: int) -> list[Any]:
    out: list[Any] = []
    kw = (f.get("keyword") or "").strip().lower()
    for tok in kw.split()[:5]:
        esc = tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        out.append(Lead.search_text.like(f"%{esc}%", escape="\\"))
    if f.get("source_id"):
        out.append(Lead.source_id == f["source_id"])
    if f.get("category"):
        out.append(Lead.categories.like(f'%"{f["category"]}"%'))
    remote = f.get("remote") or "any"
    if remote == "confirmed":
        out.append(and_(Lead.work_mode == "fully_remote", Lead.work_mode_basis.in_(("explicit", "user_confirmed"))))
    elif remote == "confirmed_or_inferred":
        out.append(Lead.work_mode == "fully_remote")
    elif remote == "unknown":
        out.append(Lead.work_mode == "unknown")
    elif remote == "onsite":
        out.append(or_(Lead.work_mode == "onsite", Lead.collaboration_mode == "onsite_required"))
    cutoff = now - timedelta(hours=ttl)
    recruit = f.get("recruit") or "any"
    closed = Lead.source_status.in_(("closed", "deleted"))
    ok = Lead.access_status == "accessible"
    if recruit == "not_closed":
        out.append(not_(closed))
    elif recruit == "open_fresh":
        out.append(and_(Lead.source_status == "open", ok, Lead.last_checked_at >= cutoff))
    elif recruit == "recheck":
        out.append(and_(Lead.source_status == "open", ok, or_(Lead.last_checked_at.is_(None), Lead.last_checked_at < cutoff)))
    elif recruit == "unknown":
        out.append(and_(Lead.source_status == "unknown", ok))
    elif recruit == "closed":
        out.append(closed)
    elif recruit == "access_issue":
        out.append(and_(not_(ok), not_(closed)))
    intent = f.get("intent") or "any"
    if intent == "buyer":
        out.append(Lead.demand_intent.in_(("buyer_project", "buyer_ongoing", "short_gig")))
    elif intent == "hiring":
        out.append(Lead.demand_intent == "employee_hiring")
    elif intent == "seller":
        out.append(Lead.demand_intent == "seller_service")
    elif intent == "other":
        out.append(Lead.demand_intent.in_(("job_seeker", "information", "unknown")))
    for key, col in (
        ("work_mode", Lead.work_mode),
        ("recommendation", Lead.recommendation),
        ("source_status", Lead.source_status),
        ("sales_stage", Lead.sales_stage),
        ("pay_unit", Lead.pay_unit),
    ):
        vals = f.get(key)
        if vals:
            out.append(col.in_(vals))
    if f.get("checked_since"):
        out.append(Lead.last_checked_at >= f["checked_since"])
    return out


def _sort_expr(sort: str):
    if sort == "published":
        return func.coalesce(func.strftime("%Y%m%d%H%M%f", Lead.published_at), "0")
    if sort == "checked":
        return func.coalesce(func.strftime("%Y%m%d%H%M%f", Lead.last_checked_at), "0")
    return func.coalesce(Lead.priority_total, -1)


def _filter_hash(queue: str, f: dict[str, Any]) -> str:
    raw = json.dumps({"q": queue, **{k: v for k, v in f.items() if k != "cursor"}}, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def encode_cursor(sort: str, value: Any, lead_id: str, fh: str) -> str:
    raw = json.dumps({"s": sort, "v": value, "id": lead_id, "h": fh}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        pad = "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(cursor + pad))
    except (ValueError, json.JSONDecodeError) as exc:
        raise CursorError("잘못된 커서입니다") from exc


def base_query(queue: str, f: dict[str, Any], now: datetime, ttl: int) -> Select[tuple[Lead]]:
    q = select(Lead)
    qc = queue_clause(queue)
    if qc is not None:
        q = q.where(qc)
    for c in filter_clauses(f, now, ttl):
        q = q.where(c)
    return q


def list_leads(s: Session, queue: str, f: dict[str, Any], cursor: str | None, limit: int) -> tuple[list[Lead], str | None]:
    now = utcnow()
    ttl = ttl_hours(s)
    sort = f.get("sort") or "priority"
    fh = _filter_hash(queue, f)
    expr = _sort_expr(sort)
    q = base_query(queue, f, now, ttl)
    if cursor:
        c = decode_cursor(cursor)
        if c.get("h") != fh or c.get("s") != sort:
            raise CursorError("필터·정렬이 바뀌어 커서를 사용할 수 없습니다. 처음부터 다시 조회하세요.")
        q = q.where(or_(expr < c["v"], and_(expr == c["v"], Lead.id < c["id"])))
    rows = s.execute(q.add_columns(expr.label("sortv")).order_by(expr.desc(), Lead.id.desc()).limit(limit + 1)).all()
    items = [r[0] for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(sort, last[1], last[0].id, fh)
    return items, next_cursor


def queue_counts(s: Session, f: dict[str, Any]) -> dict[str, int]:
    now = utcnow()
    ttl = ttl_hours(s)
    out: dict[str, int] = {}
    for qid, _, _ in QUEUES:
        q = base_query(qid, f, now, ttl).with_only_columns(func.count(Lead.id))
        out[qid] = s.scalar(q) or 0
    return out


# ── 직렬화 ─────────────────────────────────────────────────────────


def _fuzzy(at: datetime | None, precision: str, raw: str | None) -> S.FuzzyDate:
    return S.FuzzyDate(at=at, precision=precision if at or precision == "unknown" else "unknown", raw=raw)  # type: ignore[arg-type]


def summary(lead: Lead, rec: SourceRecord | None, now: datetime, ttl: int) -> S.LeadSummary:
    recheck_due = lead.source_status == "open" and (lead.last_checked_at is None or now - lead.last_checked_at > timedelta(hours=ttl))
    prov = (rec.provenance or {}) if rec else {}
    return S.LeadSummary(
        id=lead.id,
        source_id=lead.source_id,
        title=lead.title,
        categories=list(lead.categories or []),
        demand_intent=S.IntentAssessed(value=lead.demand_intent, basis=lead.demand_intent_basis),  # type: ignore[arg-type]
        engagement_type=S.EngagementAssessed(value=lead.engagement_type, basis=lead.engagement_type_basis),  # type: ignore[arg-type]
        work_mode=S.WorkModeAssessed(value=lead.work_mode, basis=lead.work_mode_basis),  # type: ignore[arg-type]
        collaboration_mode=S.CollaborationAssessed(value=lead.collaboration_mode, basis=lead.collaboration_mode_basis),  # type: ignore[arg-type]
        applicant_scope=S.ScopeAssessed(value=lead.applicant_scope, basis=lead.applicant_scope_basis),  # type: ignore[arg-type]
        pay=S.Pay(
            raw=rec.pay_raw if rec else None,
            currency=rec.pay_currency if rec else "KRW",
            min=lead.pay_min,
            max=lead.pay_max,
            unit=lead.pay_unit,  # type: ignore[arg-type]
            negotiable=bool(rec.pay_negotiable) if rec else False,
        ),
        source_status=lead.source_status,  # type: ignore[arg-type]
        access_status=lead.access_status,  # type: ignore[arg-type]
        analysis_status=lead.analysis_status,  # type: ignore[arg-type]
        recheck_due=recheck_due,
        last_checked_at=lead.last_checked_at,
        published=_fuzzy(rec.published_at, rec.published_precision, rec.published_raw) if rec else _fuzzy(None, "unknown", None),
        first_seen_at=lead.first_seen_at,
        deadline=_fuzzy(rec.deadline_at, rec.deadline_precision, rec.deadline_raw) if rec else _fuzzy(None, "unknown", None),
        posted_region=rec.posted_region_raw if rec else None,
        found_in=list(prov.get("found_in", [])),
        recommendation=lead.recommendation,  # type: ignore[arg-type]
        priority=S.Priority(total=lead.priority_total, unknown_factors=lead.priority_unknown),
        reasons=[S.Reason(**r) for r in (lead.reasons or [])],
        user_mark=lead.user_mark,  # type: ignore[arg-type]
        sales_stage=lead.sales_stage,  # type: ignore[arg-type]
        has_memo=bool(lead.memo),
        active_job=lead.active_job,  # type: ignore[arg-type]
    )


def summaries(s: Session, leads: list[Lead]) -> list[S.LeadSummary]:
    now = utcnow()
    ttl = ttl_hours(s)
    ids = [lead.primary_record_id for lead in leads if lead.primary_record_id]
    recs = {r.id: r for r in s.scalars(select(SourceRecord).where(SourceRecord.id.in_(ids)))} if ids else {}
    return [summary(lead, recs.get(lead.primary_record_id or ""), now, ttl) for lead in leads]


def _span(a: int | None, b: int | None) -> S.Span | None:
    return S.Span(start=a, end=b) if a is not None and b is not None else None


def _quick_message(s: Session, title: str, questions: list[str]) -> str:
    from ..analysis.service import quick_message
    from .settings import DEFAULT_PROFILE, get_profile_row

    data = {**DEFAULT_PROFILE, **(get_profile_row(s).data or {})}
    return quick_message(title, questions, data.get("intro") or "", data.get("portfolio_url"))


def detail(s: Session, lead: Lead) -> S.LeadDetail:
    now = utcnow()
    ttl = ttl_hours(s)
    rec = s.get(SourceRecord, lead.primary_record_id) if lead.primary_record_id else None
    assert rec is not None, "리드에 원천 레코드가 없습니다"
    base = summary(lead, rec, now, ttl)
    evidence: dict[str, list[S.Evidence]] = {}
    for e in s.scalars(select(FieldEvidence).where(FieldEvidence.lead_id == lead.id).order_by(FieldEvidence.id)):
        key = {
            "demand_intent": "demand_intent",
            "engagement_type": "engagement_type",
            "work_mode": "work_mode",
            "collaboration_mode": "collaboration_mode",
            "applicant_scope": "applicant_scope",
        }.get(e.field, e.field)
        evidence.setdefault(key, []).append(
            S.Evidence(
                id=e.id,
                quote=e.quote,
                span=_span(e.span_start, e.span_end),
                basis=e.basis,  # type: ignore[arg-type]
                confidence=e.confidence,  # type: ignore[arg-type]
                observed_at=e.observed_at,
                source_record_id=e.source_record_id,
                version=e.version,
                note=e.note,
            )
        )
    analysis_row = s.get(AnalysisResult, lead.analysis_id) if lead.analysis_id else None
    payload = analysis_row.payload if analysis_row else {}
    sb = s.scalar(select(ScoreBreakdown).where(ScoreBreakdown.lead_id == lead.id))
    score = (
        S.ScoreBreakdown(
            total=sb.total,
            factors=[S.ScoreFactor(**f) for f in sb.factors],
            risk_penalty=S.RiskPenalty(**sb.risk_penalty),
            rule_version=sb.rule_version,
        )
        if sb
        else None
    )
    prof = payload.get("profitability")
    profitability = (
        S.Profitability(
            status=prof["status"],
            basis=prof["basis"],
            reason=prof["reason"],
            assumptions=prof["assumptions"],
            scenarios=[S.ProfitScenario(**sc) for sc in prof["scenarios"]],
            revenue_type=prof["revenue_type"],
            target_hourly=prof["target_hourly"],
        )
        if prof
        else None
    )
    analysis = S.Analysis(
        status=lead.analysis_status,  # type: ignore[arg-type]
        summary=payload.get("summary"),
        summary_engine=payload.get("summary_engine"),
        fit=payload.get("fit", []),
        unfit=payload.get("unfit", []),
        uncertain=payload.get("uncertain", []),
        deliverables=payload.get("deliverables", []),
        tech_requirements=payload.get("tech", []),
        questions=payload.get("questions", []),
        next_action=payload.get("next_action"),
        conversion_opportunity=payload.get("conversion_opportunity"),
        engine=analysis_row.engine if analysis_row else None,
        version=analysis_row.rule_version if analysis_row else None,
        analyzed_at=analysis_row.created_at if analysis_row else None,
        failure=S.AnalysisFailure(code=analysis_row.failure_code, message=analysis_row.failure_message or "")
        if analysis_row and analysis_row.failure_code
        else None,
    )
    risks = [S.RiskSignal(id=r["id"], label=r["label"], quote=r.get("quote"), span=_span(r.get("start"), r.get("end"))) for r in payload.get("risks", [])]
    outcomes = [
        S.Outcome(
            id=o.id,
            kind=o.kind,  # type: ignore[arg-type]
            amount=o.amount,
            currency=o.currency,
            occurred_on=date.fromisoformat(o.occurred_on),
            note=o.note,
            evidence_ref=o.evidence_ref,
            recorded_at=o.created_at,
        )
        for o in s.scalars(select(Outcome).where(Outcome.lead_id == lead.id).order_by(Outcome.occurred_on, Outcome.created_at))
    ]
    related: list[S.RelatedRecord] = []
    own_records = [r for r in s.scalars(select(SourceRecord.id).where(SourceRecord.lead_id == lead.id))]
    links = s.scalars(select(DuplicateLink).where(or_(DuplicateLink.lead_id == lead.id, DuplicateLink.source_record_id.in_(own_records)))).all()
    for link in links:
        if link.lead_id == lead.id:
            other = s.get(SourceRecord, link.source_record_id)
            other_lead = other.lead_id if other else None
        else:
            other_lead = link.lead_id
            other_lead_row = s.get(Lead, link.lead_id)
            other = s.get(SourceRecord, other_lead_row.primary_record_id) if other_lead_row and other_lead_row.primary_record_id else None
        if other is None or other_lead == lead.id:
            continue
        related.append(
            S.RelatedRecord(
                id=link.id,
                lead_id=other_lead,
                source_id=other.source_id,
                title=other.title,
                url=other.original_url,
                relation=link.relation,  # type: ignore[arg-type]
                basis=link.basis,
                seen_at=other.last_seen_at or other.first_seen_at,
            )
        )
    labels = (rec.provenance or {}).get("found_in", [])
    paths = [
        S.DiscoveryPath(
            region_scope=p.region_scope,
            region_label=None,
            query_group=p.query_group,
            first_seen_at=p.first_seen_at,
            last_seen_at=p.last_seen_at,
            times_seen=p.times_seen,
        )
        for p in s.scalars(select(DiscoveryPath).where(DiscoveryPath.source_record_id == rec.id).order_by(DiscoveryPath.first_seen_at))
    ]
    for i, p in enumerate(paths):
        if i < len(labels):
            p.region_label = labels[i]
    activity = [
        S.Activity(at=a.at, kind=a.kind, text=a.text)
        for a in s.scalars(select(LeadActivity).where(LeadActivity.lead_id == lead.id).order_by(LeadActivity.at.desc()).limit(50))
    ]
    from ..engine.pipeline import feedback_map

    fb = feedback_map(s, lead.id)
    return S.LeadDetail(
        **base.model_dump(),
        body_text=rec.body_text,
        body_retained_until=rec.raw_retained_until,
        original_url=rec.original_url,
        contact_channel=rec.contact_channel,
        workplace=rec.workplace_raw,
        applicant_region=rec.applicant_region_raw,
        source_updated=_fuzzy(rec.source_updated_at, rec.source_updated_precision, rec.source_updated_raw),
        last_seen_at=rec.last_seen_at,
        work_period=S.WorkPeriod(
            start=_fuzzy(rec.work_start_at, rec.work_start_precision, rec.work_start_raw),
            end=_fuzzy(rec.work_end_at, rec.work_end_precision, rec.work_end_raw),
        ),
        evidence=evidence,
        score=score,
        analysis=analysis,
        risks=risks,
        profitability=profitability,
        draft=S.Draft(text=lead.draft_text, generated_at=lead.draft_generated_at, edited_by_user=lead.draft_edited) if lead.draft_text else None,
        quick_message=_quick_message(s, rec.title, payload.get("questions", [])),
        memo=lead.memo or "",
        outcomes=outcomes,
        related=related,
        discovery_paths=paths,
        activity=activity,
        feedback=S.FeedbackState(remote=fb.get("remote"), real_request=fb.get("real_request")),  # type: ignore[arg-type]
        parser_version=rec.parser_version,
        content_hash=rec.content_hash,
    )
