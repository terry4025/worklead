"""당근알바 어댑터. 범위: 공식 사이트맵의 공개 구인글만 (중고거래·동네생활·비즈프로필·채팅 검색 아님)."""

from __future__ import annotations

from pathlib import Path

from .adapter import DaangnAdapter, create_from

PROFILE_PATH = Path(__file__).with_name("profile.json")


def create() -> DaangnAdapter:
    return create_from(PROFILE_PATH)
