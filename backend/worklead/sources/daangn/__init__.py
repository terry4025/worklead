"""당근알바 어댑터. 범위: 허용된 공개 구인글만 (중고거래·동네생활·비즈프로필·채팅 검색 아님)."""

from __future__ import annotations

from pathlib import Path

from ..profiled import ProfiledSiteAdapter, load_profile

PROFILE_PATH = Path(__file__).with_name("profile.json")


def create() -> ProfiledSiteAdapter:
    return ProfiledSiteAdapter(
        load_profile(PROFILE_PATH),
        name="당근알바",
        scope_note="허용된 공개 구인글만 대상 · 중고거래·동네생활·비즈프로필·채팅은 범위 밖",
        adapter_version="0.1.0",
        parser_version="daangn-0.1.0",
    )
