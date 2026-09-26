from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Basis = Literal["explicit", "inferred", "user_confirmed"]
Confidence = Literal["high", "medium", "low"]
Precision = Literal["exact", "day", "approximate", "unknown"]


@dataclass
class Ev:
    """원문 근거. start/end 는 body_text 기준 위치 (제목 근거는 None)."""

    quote: str
    start: int | None
    end: int | None
    basis: Basis
    confidence: Confidence | None = None
    note: str | None = None


@dataclass
class Judgement:
    field: str
    value: str
    basis: Basis | None
    evidence: list[Ev] = field(default_factory=list)


@dataclass
class PayInfo:
    raw: str | None = None
    currency: str = "KRW"
    min: int | None = None
    max: int | None = None
    unit: str = "unknown"
    negotiable: bool = False
    evidence: list[Ev] = field(default_factory=list)


@dataclass
class DateInfo:
    at: datetime | None = None
    precision: Precision = "unknown"
    raw: str | None = None
    evidence: list[Ev] = field(default_factory=list)


@dataclass
class Risk:
    id: str
    label: str
    quote: str | None
    start: int | None
    end: int | None


@dataclass
class Reason:
    tone: Literal["positive", "caution", "negative"]
    text: str


@dataclass
class Factor:
    key: str
    label: str
    max: int
    score: int | None
    reason: str


@dataclass
class Scenario:
    key: Literal["conservative", "base", "optimistic"]
    contract_value: int | None
    direct_cost: int | None
    hours: float | None
    contribution: int | None
    effective_hourly: int | None
    residual_after_target: int | None


@dataclass
class Profitability:
    status: Literal["calculated", "hypothesis", "not_calculated"]
    basis: str | None
    reason: str | None
    assumptions: list[str]
    scenarios: list[Scenario]
    revenue_type: Literal["one_time", "recurring", "unknown"]
    target_hourly: int | None


@dataclass
class Profile:
    """사용자 프로필. 제공하지 않은 값은 None (추정하지 않는다)."""

    services: list[str] = field(default_factory=list)
    skills: str = ""
    excluded_work: str = ""
    min_contract: int | None = None
    target_hourly: int | None = None
    weekly_hours: int | None = None
    onsite: Literal["no", "first_meeting", "yes"] = "no"
    allow_short_term_employment: bool = True
    version: int = 1


@dataclass
class PostInput:
    """분석 입력. 수집 글은 신뢰하지 않는 데이터로 다룬다 (명령으로 해석하지 않음)."""

    title: str
    body: str
    observed_at: datetime
    source_status: str = "unknown"
    access_status: str = "accessible"
    status_evidence: list[Ev] = field(default_factory=list)
    last_checked_at: datetime | None = None
    published: DateInfo = field(default_factory=DateInfo)
    first_seen_at: datetime | None = None
    pay_hint: PayInfo | None = None
    deadline_hint: DateInfo | None = None
    source_kind: str = "site"


@dataclass
class RuleAnalysis:
    rule_version: str
    categories: list[str]
    dev_relevant: bool
    judgements: dict[str, Judgement]
    pay: PayInfo
    deadline: DateInfo
    work_start: DateInfo
    closed_marker: Ev | None
    risks: list[Risk]
    factors: list[Factor]
    risk_penalty: int
    risk_reasons: list[str]
    total: int | None
    unknown_factors: int
    recommendation: Literal["recommended", "needs_review", "excluded"]
    reasons: list[Reason]
    summary: str | None
    fit: list[str]
    unfit: list[str]
    uncertain: list[str]
    deliverables: list[str]
    tech: list[str]
    questions: list[str]
    next_action: str | None
    conversion_opportunity: str | None
    profitability: Profitability
