"""앱 설정·사용자 프로필. 비밀값(API 키 등)은 여기에 저장하지 않는다 (OS 자격 증명 저장소 사용)."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from ..analysis.types import Profile
from ..db import utcnow
from ..models import AppSetting, UserProfile

DEFAULT_QUERY_GROUPS: dict[str, Any] = {
    "version": 1,
    "groups": [
        {
            "id": "direct_build",
            "label": "직접 제작 수요",
            "enabled": True,
            "keywords": ["홈페이지", "웹사이트", "웹페이지", "사이트 제작", "랜딩페이지", "쇼핑몰 제작", "쇼핑몰 오픈", "자사몰", "카페24", "워드프레스", "앱 개발", "앱 제작", "어플", "웹앱", "프로그램 개발", "프로그램 제작", "프로그래머", "소프트웨어", "풀스택", "개발자", "웹 개발"],
        },
        {
            "id": "automation",
            "label": "자동화 수요",
            "enabled": True,
            "keywords": ["엑셀", "VBA", "매크로", "자동화", "크롤링", "파이썬", "챗봇", "구글 시트", "업무툴", "API 연동"],
        },
        {
            "id": "buyer_intent",
            "label": "구매 의도",
            "enabled": True,
            "keywords": ["만들어주실", "제작해주실", "개발해주실", "수정해주실", "의뢰", "외주", "견적", "구합니다"],
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
        "meaningful_change": True,
        "source_issue": True,
        "quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"},
    },
    "ai": {
        "enabled": False,
        "external_transfer_consent": False,
        "engine_label": None,
        "monthly_cost_cap": None,
        "daily_cost_cap": None,
    },
    "retention": {"raw_days": 30},
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
    "allow_short_term_employment": True,
    #: 빠른 연락 메시지에 들어갈 한 줄 소개·포트폴리오 링크 (사용자가 직접 입력, 비우면 채울 자리로 남김)
    "intro": "",
    "portfolio_url": None,
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
        allow_short_term_employment=bool(data.get("allow_short_term_employment", True)),
        version=row.version,
    )


def update_profile(s: Session, data: dict[str, Any]) -> bool:
    """변경되면 True (버전 증가 → 분석 캐시 무효화)."""
    row = get_profile_row(s)
    merged = {**DEFAULT_PROFILE, **(row.data or {}), **data}
    if merged == {**DEFAULT_PROFILE, **(row.data or {})}:
        return False
    row.data = merged
    row.version += 1
    return True


def ttl_hours(s: Session) -> int:
    return int(get_setting(s, "recheck")["ttl_hours"])
