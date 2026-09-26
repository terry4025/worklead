"""운영 지표. 수집 운영 / 리드 품질 / 영업 성과를 분리하고 기간·분모를 명시한다.

미래 예상 금액은 실제 수금에 합산하지 않는다. 원천 전체 공고 수를 모르므로 재현율을 계산하지 않는다.
"""

from __future__ import annotations

import statistics
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api import schemas as S
from ..db import utcnow
from ..models import AnalysisResult, Lead, LeadActivity, Outcome, Run, UserFeedback

PERIOD_DAYS = 30


def metrics(s: Session) -> S.MetricsOut:
    now = utcnow()
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

    reviewed_leads = s.scalars(
        select(Lead).where(Lead.updated_at >= since, (Lead.user_mark.is_not(None)) | (Lead.sales_stage != "new"))
    ).all()
    fb = s.scalars(select(UserFeedback).where(UserFeedback.created_at >= since)).all()
    remote_fp = sum(1 for f in fb if f.kind == "remote" and f.value == "denied")
    buyer_fp = sum(1 for f in fb if f.kind == "real_request" and f.value == "no")
    confirmed_fit = sum(1 for lead in reviewed_leads if lead.user_mark == "interested" or lead.sales_stage in ("contacted", "negotiating", "won"))
    hours: list[float] = []
    for lead in reviewed_leads:
        first = s.scalar(
            select(func.min(LeadActivity.at)).where(LeadActivity.lead_id == lead.id, LeadActivity.kind.in_(("user_mark", "sales_stage", "memo")))
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
        note="사용자 판단 기록 기준. 사람이 라벨링한 검수 표본이 아니므로 정밀도로 해석하지 않는다.",
    )

    stages = dict(s.execute(select(Lead.sales_stage, func.count()).group_by(Lead.sales_stage)).all())
    outs = s.scalars(select(Outcome).where(Outcome.created_at >= since)).all()
    contract = [o.amount for o in outs if o.kind == "contract_confirmed"]
    sales = S.SalesMetrics(
        period_days=PERIOD_DAYS,
        reviewed=len(reviewed_leads),
        contacted=int(stages.get("contacted", 0)),
        negotiating=int(stages.get("negotiating", 0)),
        won=int(stages.get("won", 0)),
        contracts_confirmed=len(contract),
        contract_amount=None if any(a is None for a in contract) else sum(a for a in contract if a is not None) if contract else 0,
        collected=sum(o.amount or 0 for o in outs if o.kind == "payment_received"),
        refunded=sum(o.amount or 0 for o in outs if o.kind == "refund"),
        direct_cost=sum(o.amount or 0 for o in outs if o.kind == "direct_cost"),
    )
    return S.MetricsOut(
        collection=collection,
        quality=quality,
        sales=sales,
        note=f"최근 {PERIOD_DAYS}일. 수금은 실제 기록만 합산하며 예상 금액·수주 표시는 포함하지 않는다. 원천 전체 공고 수를 모르므로 재현율은 계산하지 않는다.",
    )
