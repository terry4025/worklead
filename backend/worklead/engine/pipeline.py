"""정규화 → 중복 처리 → 규칙(+선택 AI) 분석 → 저장 → 신규/변경 이벤트.

중복 키: source_id + source_post_id 우선, 없으면 정규화 URL, 둘 다 없으면(수동 입력) 내용 해시.
다른 원천 ID·비슷한 본문은 병합하지 않고 후보 관계(DuplicateLink)로만 연결한다.
사용자 메모·영업 상태·관심 표시는 재수집·재분석에서 덮어쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..analysis import provider as ai
from ..analysis.service import analyze, inquiry_draft
from ..analysis.types import DateInfo, PostInput, RuleAnalysis
from ..models import (
    AnalysisResult,
    DiscoveryPath,
    DuplicateLink,
    FieldEvidence,
    Lead,
    LeadActivity,
    ScoreBreakdown,
    Snapshot,
    SourceRecord,
    UserFeedback,
    new_id,
)
from ..services.settings import get_profile, get_setting, ttl_hours
from ..sources.base import ParsedPost
from .events import emit, notify

log = logging.getLogger("worklead.pipeline")

MEANINGFUL = ("source_status", "pay", "work_mode", "deadline", "recommendation")


@dataclass
class IngestResult:
    kind: str  # created | updated | unchanged
    lead_id: str
    record_id: str
    recommendation: str
    changes: list[str] = field(default_factory=list)


def content_hash(p: ParsedPost) -> str:
    h = hashlib.sha256()
    for part in (p.title, p.body, (p.pay.raw if p.pay else "") or "", (p.deadline.raw if p.deadline else "") or "", p.source_status or ""):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _norm_title(t: str) -> str:
    return "".join(ch for ch in t.lower() if ch.isalnum())


def _trigrams(t: str) -> set[str]:
    t = "".join(t.split())
    return {t[i : i + 3] for i in range(max(0, len(t) - 2))}


def _similar(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_existing(s: Session, source_id: str, p: ParsedPost, chash: str) -> SourceRecord | None:
    if p.source_post_id:
        return s.scalar(select(SourceRecord).where(SourceRecord.source_id == source_id, SourceRecord.source_post_id == p.source_post_id))
    if p.canonical_url:
        return s.scalar(
            select(SourceRecord).where(
                SourceRecord.source_id == source_id,
                SourceRecord.source_post_id.is_(None),
                SourceRecord.canonical_url == p.canonical_url,
            )
        )
    return s.scalar(
        select(SourceRecord).where(
            SourceRecord.source_id == source_id,
            SourceRecord.source_post_id.is_(None),
            SourceRecord.canonical_url.is_(None),
            SourceRecord.content_hash == chash,
        )
    )


def _apply_parsed(rec: SourceRecord, p: ParsedPost) -> None:
    rec.title = p.title
    rec.body_text = p.body
    rec.original_url = p.original_url or rec.original_url
    rec.canonical_url = p.canonical_url or rec.canonical_url
    rec.posted_region_raw = p.posted_region_raw or rec.posted_region_raw
    rec.workplace_raw = p.workplace_raw
    rec.applicant_region_raw = p.applicant_region_raw
    rec.published_at, rec.published_precision, rec.published_raw = p.published.at, p.published.precision, p.published.raw
    rec.source_updated_at, rec.source_updated_precision, rec.source_updated_raw = p.source_updated.at, p.source_updated.precision, p.source_updated.raw
    rec.contact_channel = p.contact_channel
    rec.provenance = {**(rec.provenance or {}), "parse_notes": p.parse_notes, "status_evidence": [e.__dict__ for e in p.status_evidence]}


def record_path(s: Session, rec: SourceRecord, path: tuple[str, str | None, str] | None, run_id: str | None, now: datetime) -> None:
    if not path:
        return
    region_scope, region_label, query_group = path
    row = s.scalar(
        select(DiscoveryPath).where(
            DiscoveryPath.source_record_id == rec.id, DiscoveryPath.region_scope == region_scope, DiscoveryPath.query_group == query_group
        )
    )
    if row is None:
        s.add(
            DiscoveryPath(
                id=new_id("dp"),
                source_record_id=rec.id,
                region_scope=region_scope,
                query_group=query_group,
                first_run_id=run_id,
                first_seen_at=now,
                last_seen_at=now,
                times_seen=1,
            )
        )
        prov = dict(rec.provenance or {})
        labels = list(prov.get("found_in", []))
        if region_label and region_label not in labels:
            labels.append(region_label)
        prov["found_in"] = labels
        rec.provenance = prov
    else:
        row.last_seen_at = now
        row.times_seen += 1


def ingest(
    s: Session,
    source_id: str,
    p: ParsedPost,
    *,
    parser_version: str,
    now: datetime,
    run_id: str | None = None,
    path: tuple[str, str | None, str] | None = None,
    source_kind: str = "site",
    provider: ai.AnalysisProvider | None = None,
) -> IngestResult:
    chash = content_hash(p)
    retain_days = int(get_setting(s, "retention")["raw_days"])
    rec = find_existing(s, source_id, p, chash)
    if rec is not None:
        rec.last_seen_at = now
        if source_kind == "site":
            # 원천에서 다시 확인한 경우에만 확인 시각을 갱신한다 (수동 입력은 모집 상태를 확인하지 않음)
            rec.last_checked_at = now
            rec.access_status = "accessible"
        record_path(s, rec, path, run_id, now)
        lead = s.get(Lead, rec.lead_id) if rec.lead_id else None
        if rec.content_hash == chash and lead is not None:
            _sync_lead_from_record(lead, rec)
            before = _snapshot_state(lead)
            analysis = apply_analysis(s, lead, rec, now=now, provider=provider, source_kind=source_kind)
            changes = _diff(before, _snapshot_state(lead))
            return IngestResult("unchanged", lead.id, rec.id, analysis.recommendation, changes)
        _apply_parsed(rec, p)
        rec.content_hash = chash
        rec.parser_version = parser_version
        rec.raw_retained_until = now + timedelta(days=retain_days)
        if p.source_status:
            rec.source_status = p.source_status
        s.add(Snapshot(id=new_id("snap"), source_record_id=rec.id, captured_at=now, content_hash=chash, http_status=200, access_status="accessible", source_status=rec.source_status, title=p.title, body_text=p.body, retained_until=now + timedelta(days=retain_days)))
        if lead is None:
            lead = _new_lead(s, source_id, rec, now)
        before = _snapshot_state(lead)
        _sync_lead_from_record(lead, rec)
        analysis = apply_analysis(s, lead, rec, now=now, provider=provider, source_kind=source_kind, parsed=p)
        changes = _diff(before, _snapshot_state(lead))
        _activity(s, lead.id, now, "content_changed", "원문 내용 변경 감지" + (f" ({', '.join(_label(c) for c in changes)})" if changes else ""))
        if changes:
            emit(s, "lead.updated", {"lead_id": lead.id, "changes": changes}, dedupe_key=f"lead.updated:{lead.id}:{chash}")
            if lead.user_mark == "interested" or lead.sales_stage in ("reviewing", "contacted", "negotiating") or lead.recommendation == "recommended":
                notify(s, "lead_changed", f"조건 변경: {lead.title}", dedupe_key=f"notify:changed:{lead.id}:{chash}", lead_id=lead.id, now=now)
        return IngestResult("updated", lead.id, rec.id, analysis.recommendation, changes)

    rec = SourceRecord(
        id=new_id("rec"),
        source_id=source_id,
        source_post_id=p.source_post_id,
        title=p.title,
        body_text=p.body,
        first_seen_at=now,
        last_seen_at=now,
        last_checked_at=now if source_kind == "site" else None,
        content_hash=chash,
        parser_version=parser_version,
        source_status=p.source_status or "unknown",
        access_status="accessible",
        raw_retained_until=now + timedelta(days=retain_days),
        provenance={},
    )
    _apply_parsed(rec, p)
    s.add(rec)
    s.flush()
    record_path(s, rec, path, run_id, now)
    s.add(Snapshot(id=new_id("snap"), source_record_id=rec.id, captured_at=now, content_hash=chash, http_status=200 if source_kind == "site" else None, access_status="accessible", source_status=rec.source_status, title=p.title, body_text=p.body, retained_until=rec.raw_retained_until))
    lead = _new_lead(s, source_id, rec, now)
    _link_candidates(s, lead, rec, chash)
    analysis = apply_analysis(s, lead, rec, now=now, provider=provider, source_kind=source_kind, parsed=p)
    _activity(s, lead.id, now, "created", "수동 입력으로 추가" if source_kind == "manual" else "신규 탐색에서 발견")
    emit(s, "lead.created", {"lead_id": lead.id, "recommendation": lead.recommendation}, dedupe_key=f"lead.created:{lead.id}")
    if lead.recommendation == "recommended":
        notify(s, "new_lead", f"새 추천 리드: {lead.title}", dedupe_key=f"notify:new_lead:{lead.id}", lead_id=lead.id, now=now)
    return IngestResult("created", lead.id, rec.id, analysis.recommendation)


def _new_lead(s: Session, source_id: str, rec: SourceRecord, now: datetime) -> Lead:
    lead = Lead(id=new_id("lead"), source_id=source_id, primary_record_id=rec.id, title=rec.title, first_seen_at=now, created_at=now, updated_at=now)
    s.add(lead)
    s.flush()
    rec.lead_id = lead.id
    _sync_lead_from_record(lead, rec)
    return lead


def _link_candidates(s: Session, lead: Lead, rec: SourceRecord, chash: str) -> None:
    """재게시(같은 내용, 다른 원천 ID)와 유사 본문을 후보 관계로만 연결한다."""
    same = s.scalars(select(SourceRecord).where(SourceRecord.content_hash == chash, SourceRecord.id != rec.id).limit(5)).all()
    linked: set[str] = set()
    for other in same:
        if other.lead_id and other.lead_id not in linked:
            relation = "repost" if other.source_id == rec.source_id else "duplicate"
            s.add(DuplicateLink(id=new_id("dup"), lead_id=lead.id, source_record_id=other.id, relation=relation, basis="본문·조건 동일 (원천 ID 다름) — 병합하지 않음"))
            linked.add(other.lead_id)
    norm = _norm_title(rec.title)
    candidates = s.scalars(select(SourceRecord).where(SourceRecord.id != rec.id, SourceRecord.title == rec.title).limit(20)).all()
    for other in candidates:
        if other.lead_id in linked or not other.lead_id or _norm_title(other.title) != norm:
            continue
        sim = _similar(rec.body_text or "", other.body_text or "")
        if sim >= 0.6:
            s.add(DuplicateLink(id=new_id("dup"), lead_id=lead.id, source_record_id=other.id, relation="similar", basis=f"제목 동일·본문 유사도 {sim:.2f} — 후보 연결만"))
            linked.add(other.lead_id)


def _sync_lead_from_record(lead: Lead, rec: SourceRecord) -> None:
    lead.title = rec.title
    lead.source_status = rec.source_status
    lead.access_status = rec.access_status
    lead.last_checked_at = rec.last_checked_at
    lead.published_at = rec.published_at
    lead.search_text = f"{rec.title}\n{rec.body_text or ''}".lower()


def _snapshot_state(lead: Lead) -> dict[str, Any]:
    return {
        "source_status": lead.source_status,
        "pay": (lead.pay_unit, lead.pay_min, lead.pay_max),
        "work_mode": lead.work_mode,
        "recommendation": lead.recommendation,
        "deadline": None,
    }


def _diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    return [k for k in MEANINGFUL if a.get(k) != b.get(k)]


def _label(key: str) -> str:
    return {"source_status": "모집 상태", "pay": "보수", "work_mode": "재택 조건", "deadline": "마감일", "recommendation": "추천 판정"}.get(key, key)


def _activity(s: Session, lead_id: str, at: datetime, kind: str, text: str) -> None:
    s.add(LeadActivity(id=new_id("act"), lead_id=lead_id, at=at, kind=kind, text=text))


def feedback_map(s: Session, lead_id: str) -> dict[str, str]:
    rows = s.scalars(select(UserFeedback).where(UserFeedback.lead_id == lead_id).order_by(UserFeedback.created_at)).all()
    out: dict[str, str] = {}
    for r in rows:
        if r.kind in ("remote", "real_request"):
            if r.value == "clear":
                out.pop(r.kind, None)
            else:
                out[r.kind] = r.value
    return out


def post_input_from_record(rec: SourceRecord, source_kind: str, parsed: ParsedPost | None = None) -> PostInput:
    status_ev = []
    if rec.provenance and rec.provenance.get("status_evidence"):
        from ..analysis.types import Ev

        status_ev = [Ev(**e) for e in rec.provenance["status_evidence"]]
    return PostInput(
        title=rec.title,
        body=rec.body_text or "",
        observed_at=rec.last_checked_at or rec.first_seen_at,
        source_status=rec.source_status,
        access_status=rec.access_status,
        status_evidence=status_ev,
        last_checked_at=rec.last_checked_at,
        published=DateInfo(rec.published_at, rec.published_precision, rec.published_raw),  # type: ignore[arg-type]
        first_seen_at=rec.first_seen_at,
        pay_hint=parsed.pay if parsed and parsed.pay else None,
        deadline_hint=parsed.deadline if parsed and parsed.deadline else None,
        source_kind=source_kind,
    )


def apply_analysis(
    s: Session,
    lead: Lead,
    rec: SourceRecord,
    *,
    now: datetime,
    provider: ai.AnalysisProvider | None = None,
    source_kind: str = "site",
    parsed: ParsedPost | None = None,
    retry_ai: bool = False,
) -> RuleAnalysis:
    profile = get_profile(s)
    ttl = ttl_hours(s)
    fb = feedback_map(s, lead.id)
    post = post_input_from_record(rec, source_kind, parsed)
    ra = analyze(post, profile, now, ttl, fb)

    # 원문 규칙이 마감 표시·마감일 경과를 찾으면 원천 상태 반영 (근거 있음). 접근 오류를 마감으로 바꾸지 않는다.
    if ra.closed_marker and rec.source_status != "deleted":
        rec.source_status = "closed"
    elif ra.deadline.at is not None and ra.deadline.at < now - timedelta(days=1) and rec.source_status in ("open", "unknown"):
        rec.source_status = "closed"
    if rec.source_status != post.source_status:
        post = post_input_from_record(rec, source_kind, parsed)
        ra = analyze(post, profile, now, ttl, fb)

    # 원문 보수·마감일 저장 (구조화 데이터가 있으면 그것이 우선)
    rec.pay_raw, rec.pay_currency, rec.pay_min, rec.pay_max, rec.pay_unit, rec.pay_negotiable = (
        ra.pay.raw,
        ra.pay.currency,
        ra.pay.min,
        ra.pay.max,
        ra.pay.unit,
        ra.pay.negotiable,
    )
    rec.deadline_at, rec.deadline_precision, rec.deadline_raw = ra.deadline.at, ra.deadline.precision, ra.deadline.raw
    rec.work_start_at, rec.work_start_precision, rec.work_start_raw = ra.work_start.at, ra.work_start.precision, ra.work_start.raw

    ai_cfg = get_setting(s, "ai")
    ai_on = bool(ai_cfg.get("enabled") and ai_cfg.get("external_transfer_consent") and provider is not None and provider.available())
    engine = f"ai:{provider.engine}" if ai_on and provider else "rules"
    cache_key = f"{rec.content_hash}:{engine}:{ra.rule_version}:{ai.PROMPT_VERSION if ai_on else '-'}:{profile.version}:{sorted(fb.items())}"

    existing = s.scalar(
        select(AnalysisResult).where(AnalysisResult.lead_id == lead.id, AnalysisResult.cache_key == cache_key).order_by(AnalysisResult.created_at.desc())
    )
    ai_payload: dict[str, Any] = {}
    failure: tuple[str, str] | None = None
    reuse = existing is not None and (not ai_on or existing.status == "complete" or (existing.status == "failed" and not retry_ai))
    if reuse:
        # 같은 입력(원문 해시·엔진·규칙/프롬프트·프로필 버전)은 다시 분석 비용을 쓰지 않는다. 실패도 자동 재요청하지 않음.
        assert existing is not None
        result = existing
        status = existing.status
        ai_payload = existing.payload.get("ai", {})
        if existing.status == "failed":
            failure = (existing.failure_code or "failed", existing.failure_message or "")
        result.payload = _analysis_payload(ra, ai_payload)
    else:
        status = "rules_only"
        if ai_on:
            try:
                out = ai.validate_output(
                    provider.analyze(ai.AIInput(rec.title, ai.mask_personal(rec.body_text or ""), ra.categories, ra.summary))  # type: ignore[union-attr]
                )
                ai_payload = {
                    "summary": out.summary,
                    "deliverables": out.deliverables,
                    "tech": out.tech,
                    "questions": out.questions,
                    "uncertain": out.uncertain,
                    "tokens_in": out.tokens_in,
                    "tokens_out": out.tokens_out,
                    "latency_ms": out.latency_ms,
                    "cost_krw": out.cost_krw,
                }
                status = "complete"
            except ai.AIFailure as exc:
                status, failure = "failed", (exc.code, exc.message)
            except Exception as exc:  # noqa: BLE001 - 제공자 오류도 수집 데이터를 잃지 않게
                status, failure = "failed", ("provider_error", str(exc)[:300])
        result = AnalysisResult(
            id=new_id("ana"),
            lead_id=lead.id,
            source_record_id=rec.id,
            cache_key=cache_key,
            content_hash=rec.content_hash,
            engine=engine,
            model=provider.model if ai_on and provider else None,
            rule_version=ra.rule_version,
            prompt_version=ai.PROMPT_VERSION if ai_on else None,
            profile_version=profile.version,
            status=status,
            payload=_analysis_payload(ra, ai_payload),
            failure_code=failure[0] if failure else None,
            failure_message=failure[1] if failure else None,
            tokens_in=ai_payload.get("tokens_in"),
            tokens_out=ai_payload.get("tokens_out"),
            latency_ms=ai_payload.get("latency_ms"),
            cost_krw=ai_payload.get("cost_krw"),
            created_at=now,
        )
        s.add(result)
        s.flush()
    # 근거 기록 (최신 분석 기준)
    s.execute(delete(FieldEvidence).where(FieldEvidence.lead_id == lead.id))
    for j in ra.judgements.values():
        for ev in j.evidence:
            s.add(_evidence_row(lead.id, result.id, rec.id, j.field, j.value, ev, now, ra.rule_version))
    for ev in ra.pay.evidence:
        s.add(_evidence_row(lead.id, result.id, rec.id, "pay", ra.pay.unit, ev, now, ra.rule_version))
    for ev in ra.deadline.evidence:
        s.add(_evidence_row(lead.id, result.id, rec.id, "deadline", ra.deadline.precision, ev, now, ra.rule_version))
    status_evs = list(post.status_evidence) + ([ra.closed_marker] if ra.closed_marker else [])
    for ev in status_evs:
        s.add(_evidence_row(lead.id, result.id, rec.id, "source_status", rec.source_status, ev, now, ra.rule_version))
    sb = s.scalar(select(ScoreBreakdown).where(ScoreBreakdown.lead_id == lead.id))
    factors = [f.__dict__ for f in ra.factors]
    penalty = {"score": ra.risk_penalty, "reasons": ra.risk_reasons}
    if sb is None:
        s.add(ScoreBreakdown(id=new_id("sb"), lead_id=lead.id, analysis_id=result.id, rule_version=ra.rule_version, total=ra.total, factors=factors, risk_penalty=penalty, created_at=now))
    else:
        sb.analysis_id, sb.rule_version, sb.total, sb.factors, sb.risk_penalty, sb.created_at = result.id, ra.rule_version, ra.total, factors, penalty, now

    j = ra.judgements
    lead.categories = ra.categories
    lead.work_mode, lead.work_mode_basis = j["work_mode"].value, j["work_mode"].basis
    lead.collaboration_mode, lead.collaboration_mode_basis = j["collaboration_mode"].value, j["collaboration_mode"].basis
    lead.applicant_scope, lead.applicant_scope_basis = j["applicant_scope"].value, j["applicant_scope"].basis
    lead.demand_intent, lead.demand_intent_basis = j["demand_intent"].value, j["demand_intent"].basis
    lead.engagement_type, lead.engagement_type_basis = j["engagement_type"].value, j["engagement_type"].basis
    lead.recommendation = ra.recommendation
    lead.priority_total, lead.priority_unknown = ra.total, ra.unknown_factors
    reasons = [r.__dict__ for r in ra.reasons]
    if status == "failed" and len(reasons) < 3:
        reasons.append({"tone": "negative", "text": "AI 분석 실패 — 규칙 결과만"})
    lead.reasons = reasons
    lead.analysis_status = status
    lead.analysis_id = result.id
    lead.pay_unit, lead.pay_min, lead.pay_max = ra.pay.unit, ra.pay.min, ra.pay.max
    _sync_lead_from_record(lead, rec)
    lead.updated_at = now
    return ra


def _evidence_row(lead_id: str, analysis_id: str, rec_id: str, field_: str, value: str, ev: Any, now: datetime, version: str) -> FieldEvidence:
    return FieldEvidence(
        id=new_id("ev"),
        lead_id=lead_id,
        analysis_id=analysis_id,
        source_record_id=rec_id,
        field=field_,
        value=str(value),
        quote=ev.quote,
        span_start=ev.start,
        span_end=ev.end,
        basis=ev.basis,
        confidence=ev.confidence,
        observed_at=now,
        version=version,
        note=ev.note,
    )


def _analysis_payload(ra: RuleAnalysis, ai_payload: dict[str, Any]) -> dict[str, Any]:
    p = ra.profitability
    return {
        "summary": ai_payload.get("summary") or ra.summary,
        "summary_engine": "ai" if ai_payload.get("summary") else "rules",
        "fit": ra.fit,
        "unfit": ra.unfit,
        "uncertain": list(dict.fromkeys(ra.uncertain + ai_payload.get("uncertain", []))),
        "deliverables": ai_payload.get("deliverables") or ra.deliverables,
        "tech": ai_payload.get("tech") or ra.tech,
        "questions": list(dict.fromkeys(ra.questions + ai_payload.get("questions", [])))[:5],
        "next_action": ra.next_action,
        "conversion_opportunity": ra.conversion_opportunity,
        "risks": [r.__dict__ for r in ra.risks],
        "profitability": {
            "status": p.status,
            "basis": p.basis,
            "reason": p.reason,
            "assumptions": p.assumptions,
            "scenarios": [sc.__dict__ for sc in p.scenarios],
            "revenue_type": p.revenue_type,
            "target_hourly": p.target_hourly,
        },
        "ai": ai_payload,
        "dev_relevant": ra.dev_relevant,
    }


def generate_draft(s: Session, lead: Lead, now: datetime) -> str:
    rec = s.get(SourceRecord, lead.primary_record_id)
    assert rec is not None
    ra = analyze(post_input_from_record(rec, "site"), get_profile(s), now, ttl_hours(s), feedback_map(s, lead.id))
    return inquiry_draft(rec.title, ra)
