"""당근알바 어댑터. 범위: 허용된 공개 구인글만 (중고거래·동네생활·비즈프로필·채팅 검색 아님)."""

from __future__ import annotations

from pathlib import Path

from ..profiled import ProfiledSiteAdapter, load_profile
from .adapter import DaangnAdapter

PROFILE_PATH = Path(__file__).with_name("profile.json")


def create() -> ProfiledSiteAdapter:
    return DaangnAdapter(
        load_profile(PROFILE_PATH),
        name="당근알바",
        scope_note="전국 공개 공고 목록 · 활성 검색어·동의어로 제목 후보 선별 · 약한 후보는 제한 순환 조회 · 본문 전수 검색 아님",
        adapter_version="0.6.0",
        parser_version="daangn-0.2.0",
    )
