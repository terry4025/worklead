"""운영 지표. 수집 운영 / 리드 품질 / 영업 성과를 분리하고 기간·분모를 명시한다.

미래 예상 금액은 실제 수금에 합산하지 않는다. 원천 전체 공고 수를 모르므로 재현율을 계산하지 않는다.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api import schemas as S
from ..db import utcnow
from ..models import AnalysisResult, Lead, LeadActivity, Outcome, Run, UserFeedback

PERIOD_DAYS = 30
KST = timezone(timedelta(hours=9))
MAX_SAFE_INTEGER = 9_007_199_254_740_991
REVIEW_ACTIVITY_KINDS = ("user_mark", "sales_stage", "memo", "feedback")


def _money_total(outcomes: list[Outcome], kind: str) -> S.MoneyTotal:
    records = [outcome for outcome in outcomes if outcome.kind == kind]
    missing = sum(outcome.amount is None for outcome in records)
    known = sum(outcome.amount for outcome in records if outcome.amount is not None)
    return S.MoneyTotal(amount=None if missing else str(known), known_amount=str(known),
                        records=len(records), missing_amounts=missing)


def _currency_sales(currency: str, outcomes: list[Outcome]) -> S.CurrencySales:
    return S.CurrencySales(currency=currency,
                           contract=_money_total(outcomes, "contract_confirmed"),
                           collected=_money_total(outcomes, "payment_received"),
                           refunded=_money_total(outcomes, "refund"),
                           direct_cost=_money_total(outcomes, "direct_cost"))


def _legacy_krw(total: S.MoneyTotal) -> int | None:
    if total.amount is None:
        return None
    amount = int(total.amount)
    return amount if abs(amount) <= MAX_SAFE_INTEGER else None


def metrics(s: Session, *, now: datetime | None = None) -> S.MetricsOut:
    now = now or utcnow()
    since = now - timedelta(days=PERIOD_DAYS)
    runs = s.scalars(select(Run).where(Run.created_at >= since, Run.kind.in_(("discovery", "recheck_recent", "recheck_stale", "recheck_lead", "import")))).all()

    def total(key: str) -> int:
        return sum(int((r.counts or {}).get(key, 0)) for r in runs)

    costs = s.scalars(select(AnalysisResult.cost_krw).where(AnalysisResult.created_at >= since, AnalysisResult.engine != "rules")).all()
    ai_calls = len(costs)
    cost = None if ai_calls == 0 or any(c is None for c in costs) else int(sum(c for c in costs if c is not None))
    collection = S.CollectionMetrics(
        period_days=PERIOD_DAYS,
        runs=len(runs),
        requests=total("requests"),
        details_fetched=total("details_fetched"),
        created=total("created"),
        duplicates=total("duplicates"),
        parse_failures=total("parse_failures"),
        fetch_failures=total("fetch_failures"),
        policy_stops=total("policy_stops"),
        ai_failures=total("ai_failures"),
        ai_cost_krw=0 if ai_calls == 0 else cost,
    )

    reviewed_ids = select(LeadActivity.lead_id).where(
        LeadActivity.kind.in_(REVIEW_ACTIVITY_KINDS), LeadActivity.at >= since, LeadActivity.at <= now).distinct()
    reviewed_leads = s.scalars(select(Lead).where(Lead.id.in_(reviewed_ids))).all()
    fb = s.scalars(select(UserFeedback).where(UserFeedback.created_at >= since, UserFeedback.created_at <= now)).all()
    remote_fp = sum(1 for f in fb if f.kind == "remote" and f.value == "denied")
    buyer_fp = sum(1 for f in fb if f.kind == "real_request" and f.value == "no")
    confirmed_fit = sum(1 for lead in reviewed_leads if lead.user_mark == "interested" or lead.sales_stage in ("contacted", "negotiating", "won"))
    hours: list[float] = []
    for lead in reviewed_leads:
        first = s.scalar(
            select(func.min(LeadActivity.at)).where(LeadActivity.lead_id == lead.id,
                                                   LeadActivity.kind.in_(REVIEW_ACTIVITY_KINDS),
                                                   LeadActivity.at >= lead.first_seen_at, LeadActivity.at <= now)
        )
        if first:
            hours.append((first - lead.first_seen_at).total_seconds() / 3600)
    quality = S.QualityMetrics(
        period_days=PERIOD_DAYS,
        reviewed=len(reviewed_leads),
        confirmed_fit=confirmed_fit,
        remote_false_positive=remote_fp,
        buyer_false_positive=buyer_fp,
        median_hours_to_review=round(statistics.median(hours), 1) if hours else None,
        note="최근 30일 관심·제외/진행 상태/메모/확인 사용자 활동을 기록한 서로 다른 리드 기준. 현재 표시를 취소한 리드도 활동이 있으면 포함한다. 자동 분석·원문 갱신·초안 생성·관계·성과 기록만으로는 검토 수를 늘리지 않는다. 검토 시간은 발견 이후 최초 유효 사용자 활동까지이며, 활동 없는 기존 자료는 추정하지 않는다. 사람이 라벨링한 검수 표본이 아니므로 정밀도로 해석하지 않는다.",
    )

    stages = dict(s.execute(select(Lead.sales_stage, func.count()).group_by(Lead.sales_stage)).all())
    # Financial records describe an occurrence date in Korea, independent of when
    # the user entered the record. Today counts as the final calendar day.
    period_end = now.astimezone(KST).date()
    period_start = period_end - timedelta(days=PERIOD_DAYS - 1)
    outs = s.scalars(select(Outcome).where(Outcome.occurred_on >= period_start.isoformat(),
                                           Outcome.occurred_on <= period_end.isoformat())).all()
    grouped: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outs:
        grouped[outcome.currency].append(outcome)
    by_currency = [_currency_sales(currency, rows) for currency, rows in sorted(grouped.items())]
    # Existing scalar fields remain KRW-only. An absent KRW group means no KRW
    # records, not an inferred conversion from the other currencies.
    krw = next((group for group in by_currency if group.currency == "KRW"), _currency_sales("KRW", []))
    sales = S.SalesMetrics(
        period_days=PERIOD_DAYS,
        period_start=period_start,
        period_end=period_end,
        time_zone="Asia/Seoul",
        stage_scope="current_all",
        reviewed=len(reviewed_leads),
        contacted=int(stages.get("contacted", 0)),
        negotiating=int(stages.get("negotiating", 0)),
        won=int(stages.get("won", 0)),
        contracts_confirmed=sum(outcome.kind == "contract_confirmed" for outcome in outs),
        contract_amount=_legacy_krw(krw.contract),
        collected=_legacy_krw(krw.collected),
        refunded=_legacy_krw(krw.refunded),
        direct_cost=_legacy_krw(krw.direct_cost),
        by_currency=by_currency,
    )
    return S.MetricsOut(
        collection=collection,
        quality=quality,
        sales=sales,
        note=f"수집은 최근 {PERIOD_DAYS}일 실행 기록, 검토는 같은 기간의 관심·제외/진행 상태/메모/확인 사용자 활동 기준이다. 계약·수금·환불·비용은 한국 날짜 {period_start.isoformat()}~{period_end.isoformat()} 발생일 기준이며 통화별로 합산한다. 연락·협의·수주는 현재 전체 리드의 단계별 수다. 금액 미기록은 0원으로 추정하지 않으며 예상 금액·수주 표시는 수금에 포함하지 않는다. 원천 전체 공고 수를 모르므로 재현율은 계산하지 않는다.",
    )
