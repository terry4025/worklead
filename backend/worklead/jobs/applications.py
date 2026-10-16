"""Evaluate current facts and prepare local drafts without submitting anything."""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from ..analysis.service import analyze, inquiry_draft
from ..api import schemas as S
from ..engine.events import emit
from ..engine.pipeline import feedback_map, post_input_from_record
from ..models import Lead, LeadActivity, SourcePolicy, SourceRecord, new_id
from ..services.settings import get_profile, ttl_hours


class ApplicationError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def _lead(s: Session, lead_id: str) -> Lead:
    lead = s.get(Lead, lead_id)
    if lead is None:
        raise ApplicationError(404, "lead_not_found", "리드를 찾을 수 없습니다")
    return lead


def _candidate_clause():
    return (
        Lead.source_id == "daangn-alba", Lead.recommendation.in_(("recommended", "needs_review")),
        Lead.demand_intent.in_(("buyer_project", "buyer_ongoing")), Lead.engagement_type == "project",
        Lead.pay_unit == "project", Lead.source_status == "open", Lead.access_status == "accessible",
        Lead.sales_stage.in_(("new", "reviewing")), or_(Lead.user_mark.is_(None), Lead.user_mark != "dismissed"),
    )


def evaluate(s: Session, lead: Lead, now: datetime) -> S.JobApplicationCandidate:
    rec = s.get(SourceRecord, lead.primary_record_id)
    holds: list[S.JobApplicationHold] = []

    def hold(code: str, message: str):
        if not any(item.code == code for item in holds):
            holds.append(S.JobApplicationHold(code=code, message=message))

    candidate = bool(rec is not None and lead.source_id == "daangn-alba"
        and lead.recommendation in ("recommended", "needs_review")
        and lead.demand_intent in ("buyer_project", "buyer_ongoing")
        and lead.engagement_type == "project" and lead.pay_unit == "project"
        and lead.source_status == "open" and lead.access_status == "accessible"
        and rec.source_status == "open" and rec.access_status == "accessible"
        and lead.sales_stage in ("new", "reviewing") and lead.user_mark != "dismissed")
    if not candidate:
        hold("not_candidate", "모집 중인 당근 건당 제작 구매 의뢰이며 지원 전인 리드만 초안을 준비합니다")
    profile = get_profile(s)
    questions: list[str] = []
    if rec is not None:
        if not rec.body_text:
            hold("source_body_missing", "보존된 원문이 없어 원문 재확인이 필요합니다")
        if (rec.last_checked_at is None or rec.last_checked_at > now
                or rec.last_checked_at < now - timedelta(hours=ttl_hours(s))):
            hold("recheck_required", "모집 확인이 만료되었거나 유효하지 않아 원문 재확인이 필요합니다")
        if (rec.published_precision != "exact" or rec.published_at is None
                or rec.published_at > now or rec.published_at < now - timedelta(hours=24)):
            hold("publication_not_recent", "정확한 게시 시각으로 최근 24시간 공고인지 확인하지 못했습니다")
        try:
            url = urlsplit(rec.original_url or "")
            valid_url = (url.scheme == "https" and url.hostname == "jobs.daangn.com"
                and url.port in (None, 443) and url.path.startswith("/job-posts/") and not url.username and not url.password)
        except ValueError:
            valid_url = False
        if not valid_url:
            hold("source_url_unverified", "확인된 당근 알바 원문 주소가 아닙니다")
        ra = analyze(post_input_from_record(rec, "site"), profile, now, ttl_hours(s), feedback_map(s, lead.id))
        questions = ra.questions
        if ra.recommendation == "excluded" or not ra.dev_relevant or ra.pay.unit != "project":
            candidate = False
            hold("rules_ineligible", "현재 규칙·프로필로 재판정하면 제작 의뢰 적격 조건을 충족하지 않습니다")
        for item in ra.reasons:
            if item.tone in ("caution", "negative"):
                hold("condition_review", item.text)
        if ra.judgements["work_mode"].value != "fully_remote":
            hold("remote_unconfirmed", "원격 진행 가능 여부를 확인해야 합니다")
        if ra.judgements["collaboration_mode"].value != "online_only":
            hold("meeting_unconfirmed", "현장 방문·대면 미팅 조건을 확인해야 합니다")
        if ra.pay.min is None or ra.pay.currency != "KRW":
            hold("budget_unconfirmed", "확정 건당 금액·통화를 확인해야 합니다")
        pay_factor = next((factor for factor in ra.factors if factor.key == "pay_priority"), None)
        if pay_factor is not None and (pay_factor.score or 0) < 0:
            hold("low_pay_review", "낮은 보수 또는 작업 범위 대비 수익성을 확인해야 합니다")
        if profile.min_contract is not None and ra.pay.min is not None and ra.pay.min < profile.min_contract:
            hold("below_min_contract", "게시 보수가 내 최소 계약금액보다 낮습니다")
        if questions:
            hold("questions_unanswered", "범위·일정·진행 조건의 확인 질문에 답이 없어 제출을 보류합니다")
        if ra.profitability.status != "calculated":
            hold("profitability_unconfirmed", "범위·작업시간·비용이 확정되지 않아 수익성을 확인해야 합니다")
        elif ra.profitability.assumptions:
            hold("profitability_assumptions", "표시 수익성은 규칙상 추정 시간·비용에 따른 계산이며 실제 범위와 투입 시간을 확인해야 합니다")
    policy = s.get(SourcePolicy, "daangn-alba")
    if policy is None or policy.status != "allowed":
        hold("source_permission_unverified", "당근 소스의 공개 수집 허용 상태를 확인해야 합니다")
    if not profile.skills.strip():
        hold("profile_incomplete", "저장된 소개·기술 정보가 비어 있어 직접 작성해야 합니다")
    hold("profile_not_approved", "기존 프로필은 초안에만 사용하도록 선택되어 제출용 프로필이 승인되지 않았습니다")
    hold("submission_path_unverified", "지원 화면·필수 질문·완료 확인 경로가 검증되지 않았습니다")
    hold("draft_only", "현재 설정은 초안 작성 전용이며 실제 지원을 전송하지 않습니다")
    return S.JobApplicationCandidate(
        lead_id=lead.id, title=lead.title, original_url=rec.original_url if rec else None,
        recommendation=lead.recommendation, published_at=rec.published_at if rec else None,
        last_checked_at=rec.last_checked_at if rec else None, candidate=candidate,
        can_prepare=candidate and bool(rec and rec.body_text), hold_reasons=holds, questions=questions,
        draft=S.Draft(text=lead.draft_text, generated_at=lead.draft_generated_at, edited_by_user=lead.draft_edited) if lead.draft_text else None,
    )


def listing(s: Session, now: datetime, limit: int) -> S.JobApplicationList:
    query = select(Lead).where(*_candidate_clause()).order_by(Lead.published_at.desc(), Lead.first_seen_at.desc(), Lead.id)
    total = s.scalar(select(func.count()).select_from(Lead).where(*_candidate_clause())) or 0
    return S.JobApplicationList(total=total, items=[evaluate(s, lead, now) for lead in s.scalars(query.limit(limit))])


def get(s: Session, lead_id: str, now: datetime) -> S.JobApplicationCandidate:
    return evaluate(s, _lead(s, lead_id), now)


def prepare(s: Session, lead_id: str, now: datetime) -> S.JobApplicationCandidate:
    s.execute(text("BEGIN IMMEDIATE"))
    lead = _lead(s, lead_id)
    entry = evaluate(s, lead, now)
    if not entry.can_prepare:
        raise ApplicationError(409, "application_not_candidate", "현재 조건에서는 지원 초안을 준비할 수 없습니다", {"sent": 0, "reasons": [h.model_dump() for h in entry.hold_reasons]})
    if lead.draft_text is not None or lead.draft_edited:
        return entry  # Preserve even an intentionally cleared/user-edited draft.
    rec = s.get(SourceRecord, lead.primary_record_id)
    profile = get_profile(s)
    analysis = analyze(post_input_from_record(rec, "site"), profile, now, ttl_hours(s), feedback_map(s, lead.id))
    draft = inquiry_draft(lead.title, analysis)
    if profile.skills.strip():
        draft = draft.replace("[간단한 소개와 비슷한 작업 경험 — 직접 작성]", f"[저장된 소개·기술 — 제출 전 확인]\n{profile.skills.strip()}")
    lead.draft_text, lead.draft_generated_at, lead.draft_edited = draft, now, False
    s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now, kind="draft", text="당근 지원 초안 준비 — 전송하지 않음"))
    emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["draft"]})
    s.flush()
    return evaluate(s, lead, now)


def save(s: Session, lead_id: str, body: S.JobApplicationDraftPatch, now: datetime) -> S.JobApplicationCandidate:
    s.execute(text("BEGIN IMMEDIATE"))
    lead = _lead(s, lead_id)
    if lead.draft_text != body.expected_text:
        raise ApplicationError(409, "draft_changed", "다른 화면에서 초안이 바뀌었습니다. 다시 읽은 후 저장하세요")
    if lead.source_id != "daangn-alba":
        raise ApplicationError(409, "application_not_candidate", "당근 알바 초안이 아닙니다")
    lead.draft_text, lead.draft_edited = body.text, True
    s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now, kind="draft", text="당근 지원 초안 직접 수정 — 전송하지 않음"))
    emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["draft"]})
    s.flush()
    return evaluate(s, lead, now)


def submit(s: Session, lead_id: str, now: datetime):
    entry = get(s, lead_id, now)
    raise ApplicationError(409, "application_submission_unavailable", "초안 전용입니다. 프로필과 실제 지원 경로를 확인하기 전에는 전송할 수 없습니다", {
        "mode": "draft_only", "sent": 0, "reasons": [item.model_dump() for item in entry.hold_reasons],
    })
