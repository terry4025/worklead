"""앱 설정·사용자 프로필. 비밀값(API 키 등)은 여기에 저장하지 않는다 (OS 자격 증명 저장소 사용)."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from ..analysis.types import NON_SCORING_PROFILE_KEYS, Profile
from ..db import utcnow
from ..models import AppSetting, UserProfile, AnalysisResult, Lead

DEFAULT_QUERY_GROUPS: dict[str, Any] = {
    "version": 1,
    "groups": [
        {
            "id": "direct_build",
            "label": "직접 제작 수요",
            "enabled": True,
            "keywords": ["홈페이지", "웹사이트", "랜딩페이지", "쇼핑몰", "풀스택", "프로그램", "소프트웨어", "개발자"],
        },
        {
            "id": "automation",
            "label": "자동화 수요",
            "enabled": True,
            "keywords": ["엑셀", "VBA", "매크로", "자동화", "업무툴", "데이터 정리", "보고서 자동화", "API 연동", "구글시트", "스프레드시트"],
        },
        {
            "id": "buyer_intent",
            "label": "구매 의도",
            "enabled": True,
            "keywords": ["만들어주실", "제작해주실", "개발해주실", "수정해주실", "의뢰", "외주", "견적", "구합니다"],
        },
        {
            "id": "development_aliases",
            "label": "앱·웹·자동화 표현",
            "enabled": True,
            "keywords": ["앱", "어플", "홈피", "웹페이지", "RPA", "크롤링", "크롤러", "파이썬", "워드프레스", "카페24", "시스템 개발", "시스템 구축"],
        },
        {
            "id": "problem",
            "label": "문제 표현",
            "enabled": False,
            "keywords": ["반복 입력", "엑셀 취합", "수작업", "관리 페이지 필요", "기존 사이트 수정"],
        },
    ],
}

DEFAULTS: dict[str, Any] = {
    "recheck": {"ttl_hours": 24},
    "notifications": {
        "new_recommended": True,
        "new_review": True,
        "meaningful_change": True,
        "source_issue": True,
        "daangn_messages": True,
        "quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"},
    },
    "ai": {
        "enabled": False,
        "external_transfer_consent": False,
        "engine_label": None,
        "monthly_cost_cap": None,
        "daily_cost_cap": None,
        "provider": "openai",
        "model": "gpt-4.1-mini-2025-04-14",
        "usd_to_krw_rate": None,
        "max_output_tokens": 1024,
        "pricing_confirmed": False,
        "price_version": None,
        "daily_call_cap": 300,
        "assist_review": False,
        "assist_recheck": False,
        "assist_application": False,
        "assist_chat": False,
        "assist_quote": False,
    },
    "exaone_calls": {"day": None, "count": 0},
    "ai_revision": 0,
    "retention": {"raw_days": 30},
    "collection": {"prevent_sleep": False},
    "query_groups": DEFAULT_QUERY_GROUPS,
    #: 이 seq 이하의 일반 이벤트는 정리됨 → 이보다 앞에서 이어받으려 하면 전체 재조회
    "events_trimmed_through": 0,
}

DEFAULT_PROFILE: dict[str, Any] = {
    "services": ["website", "landing", "shop", "fullstack", "software", "vba", "automation"],
    "skills": "",
    "excluded_work": "",
    "min_contract": None,
    "target_hourly": None,
    "weekly_hours": None,
    "onsite": "no",
    "allow_short_term_employment": False,
    "business_registered": False,
    "tax_invoice": False,
    "portfolio_url": "https://www.seyj.kr/",
    "price_anchor_note": "",
}


def get_setting(s: Session, key: str) -> Any:
    row = s.get(AppSetting, key)
    base = copy.deepcopy(DEFAULTS[key])
    if row is None:
        return base
    if isinstance(base, dict) and isinstance(row.value, dict):
        return _merge(base, row.value)
    return row.value


def put_setting(s: Session, key: str, value: Any) -> None:
    row = s.get(AppSetting, key)
    if row is None:
        s.add(AppSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()


class AISettingsChanged(Exception):
    pass


def advance_ai_revision(s: Session, expected: int) -> int:
    """Acquire the SQLite writer and compare atomically before OS key changes."""
    s.execute(insert(AppSetting).values(key="ai_revision", value=0).on_conflict_do_nothing(index_elements=[AppSetting.key]))
    result = s.execute(update(AppSetting).where(AppSetting.key == "ai_revision", AppSetting.value == expected)
                       .values(value=expected + 1, updated_at=utcnow()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise AISettingsChanged()
    row = s.get(AppSetting, "ai_revision")
    if row is not None:
        s.expire(row)
    return expected + 1


def stop_pending_ai(s: Session) -> list[str]:
    pending = select(AnalysisResult.id).where(AnalysisResult.engine == "ai:openai", AnalysisResult.status == "pending")
    ids = list(s.scalars(select(Lead.id).where(Lead.analysis_id.in_(pending), Lead.analysis_status == "pending")))
    changed = s.execute(update(Lead).where(Lead.analysis_id.in_(pending), Lead.analysis_status == "pending")
                        .values(analysis_status="rules_only").execution_options(synchronize_session=False)).rowcount
    s.execute(update(AnalysisResult).where(AnalysisResult.engine == "ai:openai", AnalysisResult.status == "pending")
              .values(status="rules_only").execution_options(synchronize_session=False))
    return ids if changed else []


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get_profile_row(s: Session) -> UserProfile:
    row = s.get(UserProfile, 1)
    if row is None:
        row = UserProfile(id=1, version=1, data=copy.deepcopy(DEFAULT_PROFILE))
        s.add(row)
        s.flush()
    return row


def get_profile(s: Session) -> Profile:
    row = get_profile_row(s)
    data = {**DEFAULT_PROFILE, **(row.data or {})}
    return Profile(
        services=list(data["services"]),
        skills=data["skills"] or "",
        excluded_work=data["excluded_work"] or "",
        min_contract=data["min_contract"],
        target_hourly=data["target_hourly"],
        weekly_hours=data["weekly_hours"],
        onsite=data["onsite"],
        allow_short_term_employment=bool(data.get("allow_short_term_employment", False)),
        business_registered=bool(data["business_registered"]),
        tax_invoice=bool(data["tax_invoice"]),
        portfolio_url=data["portfolio_url"] or "",
        price_anchor_note=data["price_anchor_note"] or "",
        version=row.version,
    )


def update_profile(s: Session, data: dict[str, Any]) -> bool:
    """변경되면 True (버전 증가 → 분석 캐시 무효화)."""
    row = get_profile_row(s)
    before = {**DEFAULT_PROFILE, **(row.data or {})}
    merged = {**before, **data}
    if merged == before:
        return False
    row.data = merged
    if {k for k in merged if merged[k] != before.get(k)} <= NON_SCORING_PROFILE_KEYS:
        return False  # saved, but scoring inputs are unchanged: no version bump, no reanalysis
    row.version += 1
    return True


def ttl_hours(s: Session) -> int:
    return int(get_setting(s, "recheck")["ttl_hours"])
