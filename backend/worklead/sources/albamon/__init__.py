"""알바몬 어댑터. 범위: IT 업직종·재택 알바 목록의 공개 공고 (지원·연락은 사용자가 원문에서 직접)."""

from __future__ import annotations

from pathlib import Path

from .adapter import AlbamonAdapter, create_from

PROFILE_PATH = Path(__file__).with_name("profile.json")


def create() -> AlbamonAdapter:
    return create_from(PROFILE_PATH)
