"""SQLAlchemy 모델.

원칙
- SourceRecord 는 원천별 공고, Lead 는 중복 공고를 묶는 검토 단위다.
- 상태 축(source_status / access_status / analysis_status / sales_stage)을 합치지 않는다.
- 모르는 값은 NULL. 금액은 최소 통화 단위 정수(KRW 는 원).
- 사용자 판단(user_mark, memo, sales_stage, feedback)과 자동 판단(분류·추천)을 분리 저장한다.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .db import UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


def new_id(prefix: str) -> str:
    """시간순 정렬 가능한 ID (prefix_타임스탬프ms16진수+난수)."""
    return f"{prefix}_{int(time.time() * 1000):012x}{os.urandom(5).hex()}"


# ── 소스 ─────────────────────────────────────────────────────────────


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))  # site | manual
    adapter_version: Mapped[str] = mapped_column(String(32))
    scope_note: Mapped[str | None] = mapped_column(Text)
    auto_collect_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    health_status: Mapped[str] = mapped_column(String(16), default="unknown")
    health_message: Mapped[str | None] = mapped_column(Text)
    health_code: Mapped[str | None] = mapped_column(String(64))
    health_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: 401/403/CAPTCHA 등으로 자동 정지된 사유. 사용자가 해제하기 전까지 자동 수집하지 않는다.
    stopped_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class SourcePolicy(Base):
    __tablename__ = "source_policies"

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="permission_pending")
    basis: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reviewed_by: Mapped[str | None] = mapped_column(String(16))  # user | system
    robots_status: Mapped[str] = mapped_column(String(16), default="unchecked")
    robots_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    robots_summary: Mapped[str | None] = mapped_column(Text)


# ── 실행·작업 ─────────────────────────────────────────────────────────


class Run(Base):
    """장기 작업(수집 실행, 재확인, 재분석, 가져오기, 내보내기). job_id == run.id"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    lead_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(16), default="queued")
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    scan_cycle: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    progress_label: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_after: Mapped[datetime | None] = mapped_column(UTCDateTime)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_runs_state_created", "state", "created_at"),
        Index("ix_runs_source_created", "source_id", "created_at"),
    )


class CollectionTask(Base):
    """탐색 작업 단위. task_key = source_id + query_group + region_scope + cursor + scan_cycle"""

    __tablename__ = "collection_tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(String(64))
    task_key: Mapped[str] = mapped_column(String(400), unique=True)
    query_group: Mapped[str] = mapped_column(String(64))
    region_scope: Mapped[str] = mapped_column(String(128))
    cursor: Mapped[str | None] = mapped_column(Text)
    scan_cycle: Mapped[int] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    order_no: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_tasks_run_state", "run_id", "state", "order_no"),)


class CoverageUnit(Base):
    """탐색 범위 단위(지역×검색어 묶음). 공정 순환을 위해 마지막 방문 시각을 기록한다."""

    __tablename__ = "coverage_units"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    unit_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    region_scope: Mapped[str] = mapped_column(String(128))
    region_label: Mapped[str | None] = mapped_column(String(128))
    query_group: Mapped[str] = mapped_column(String(64))
    last_visited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_result: Mapped[str | None] = mapped_column(String(16))
    last_scan_cycle: Mapped[int | None] = mapped_column(Integer)
    visits: Mapped[int] = mapped_column(Integer, default=0)


class HostBudget(Base):
    """호스트별 일일 요청 예산. 같은 소스의 모든 작업이 공유한다."""

    __tablename__ = "host_budgets"

    host: Mapped[str] = mapped_column(String(255), primary_key=True)
    window: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD (UTC)
    used: Mapped[int] = mapped_column(Integer, default=0)
    limit: Mapped[int] = mapped_column(Integer)
    last_request_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RobotsCache(Base):
    __tablename__ = "robots_cache"

    host: Mapped[str] = mapped_column(String(255), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16))  # ok | missing | unreachable
    http_status: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str | None] = mapped_column(Text)


# ── 원천 공고 ─────────────────────────────────────────────────────────


class SourceRecord(Base):
    __tablename__ = "source_records"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    source_post_id: Mapped[str | None] = mapped_column(String(200))
    original_url: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    posted_region_raw: Mapped[str | None] = mapped_column(Text)
    workplace_raw: Mapped[str | None] = mapped_column(Text)
    applicant_region_raw: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    published_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    published_raw: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source_updated_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    source_updated_raw: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deadline_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    deadline_raw: Mapped[str | None] = mapped_column(Text)
    work_start_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    work_start_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    work_start_raw: Mapped[str | None] = mapped_column(Text)
    work_end_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    work_end_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    work_end_raw: Mapped[str | None] = mapped_column(Text)
    pay_raw: Mapped[str | None] = mapped_column(Text)
    pay_currency: Mapped[str] = mapped_column(String(8), default="KRW")
    pay_min: Mapped[int | None] = mapped_column(Integer)
    pay_max: Mapped[int | None] = mapped_column(Integer)
    pay_unit: Mapped[str] = mapped_column(String(16), default="unknown")
    pay_negotiable: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_channel: Mapped[str | None] = mapped_column(Text)
    source_status: Mapped[str] = mapped_column(String(16), default="unknown")
    access_status: Mapped[str] = mapped_column(String(16), default="accessible")
    content_hash: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_retained_until: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        Index(
            "uq_record_post_id",
            "source_id",
            "source_post_id",
            unique=True,
            sqlite_where=text("source_post_id IS NOT NULL"),
        ),
        Index(
            "uq_record_canonical_url",
            "source_id",
            "canonical_url",
            unique=True,
            sqlite_where=text("source_post_id IS NULL AND canonical_url IS NOT NULL"),
        ),
        Index("ix_record_lead", "lead_id"),
    )


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_record_id: Mapped[str] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer)
    access_status: Mapped[str] = mapped_column(String(16))
    source_status: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    retained_until: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DiscoveryPath(Base):
    """같은 공고가 어느 지역·검색어에서 발견됐는지 보존한다."""

    __tablename__ = "discovery_paths"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_record_id: Mapped[str] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"))
    region_scope: Mapped[str] = mapped_column(String(128))
    query_group: Mapped[str] = mapped_column(String(64))
    first_run_id: Mapped[str | None] = mapped_column(String(40))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (UniqueConstraint("source_record_id", "region_scope", "query_group"),)


# ── 리드 ─────────────────────────────────────────────────────────────


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    primary_record_id: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)

    # 자동 판정 (각 축 독립) + 가장 강한 근거 유형
    work_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    work_mode_basis: Mapped[str | None] = mapped_column(String(16))
    collaboration_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    collaboration_mode_basis: Mapped[str | None] = mapped_column(String(16))
    applicant_scope: Mapped[str] = mapped_column(String(24), default="unknown")
    applicant_scope_basis: Mapped[str | None] = mapped_column(String(16))
    demand_intent: Mapped[str] = mapped_column(String(24), default="unknown")
    demand_intent_basis: Mapped[str | None] = mapped_column(String(16))
    engagement_type: Mapped[str] = mapped_column(String(24), default="unknown")
    engagement_type_basis: Mapped[str | None] = mapped_column(String(16))
    recommendation: Mapped[str] = mapped_column(String(16), default="needs_review")
    priority_total: Mapped[int | None] = mapped_column(Integer)
    priority_unknown: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    analysis_status: Mapped[str] = mapped_column(String(16), default="pending")
    analysis_id: Mapped[str | None] = mapped_column(String(40))

    # 원천 상태 (대표 레코드 기준 비정규화 — 목록 필터 성능용)
    source_status: Mapped[str] = mapped_column(String(16), default="unknown")
    access_status: Mapped[str] = mapped_column(String(16), default="accessible")
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    pay_unit: Mapped[str] = mapped_column(String(16), default="unknown")
    pay_min: Mapped[int | None] = mapped_column(Integer)
    pay_max: Mapped[int | None] = mapped_column(Integer)
    search_text: Mapped[str] = mapped_column(Text, default="")

    # 사용자 판단 — 재수집·재분석이 덮어쓰지 않는다
    sales_stage: Mapped[str] = mapped_column(String(16), default="new")
    user_mark: Mapped[str | None] = mapped_column(String(16))
    memo: Mapped[str] = mapped_column(Text, default="")
    draft_text: Mapped[str | None] = mapped_column(Text)
    draft_generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    draft_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    active_job: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_leads_rec_priority", "recommendation", "priority_total", "id"),
        Index("ix_leads_checked", "last_checked_at", "id"),
        Index("ix_leads_published", "published_at", "id"),
        Index("ix_leads_stage_mark", "sales_stage", "user_mark"),
        Index("ix_leads_source", "source_id"),
    )


class DuplicateLink(Base):
    __tablename__ = "duplicate_links"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    source_record_id: Mapped[str] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(String(16))  # duplicate | repost | similar
    basis: Mapped[str | None] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("lead_id", "source_record_id"),)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    source_record_id: Mapped[str] = mapped_column(String(40))
    #: 원문 해시 + 엔진/모델 + 프롬프트·스키마 버전 + 프로필 버전
    cache_key: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    engine: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(100))
    rule_version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    profile_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    #: 확인 가능한 비용만 기록 (모르면 NULL)
    cost_krw: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (Index("ix_analysis_cache", "cache_key"), Index("ix_analysis_lead", "lead_id", "created_at"))


class FieldEvidence(Base):
    __tablename__ = "field_evidence"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_results.id", ondelete="CASCADE"))
    source_record_id: Mapped[str] = mapped_column(String(40))
    field: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(64))
    quote: Mapped[str] = mapped_column(Text)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)
    basis: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[str | None] = mapped_column(String(8))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    version: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_evidence_analysis", "analysis_id"),)


class ScoreBreakdown(Base):
    __tablename__ = "score_breakdowns"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_results.id", ondelete="CASCADE"))
    rule_version: Mapped[str] = mapped_column(String(32))
    total: Mapped[int | None] = mapped_column(Integer)
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    risk_penalty: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LeadActivity(Base):
    __tablename__ = "lead_activity"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)

    __table_args__ = (Index("ix_activity_lead", "lead_id", "at"),)


class Outcome(Base):
    """계약 확인·수금·환불·직접 비용 기록. won 표시만으로 수금이 늘지 않는다."""

    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    amount: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="KRW")
    occurred_on: Mapped[str] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(Text)
    evidence_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class EventOutbox(Base):
    """DB 변경과 같은 트랜잭션에 기록하는 이벤트·알림. seq 가 이어받기 순서값이다."""

    __tablename__ = "event_outbox"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    #: 네이티브 알림 대상 여부와 처리 결과 (단일 소비자가 처리)
    notify: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    suppressed_reason: Mapped[str | None] = mapped_column(String(64))
