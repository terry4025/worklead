"""아이보스 공개 대행의뢰 게시판 어댑터."""

from pathlib import Path

from ..profiled import load_profile
from .adapter import IbossAdapter

PROFILE_PATH = Path(__file__).with_name("profile.json")


def create() -> IbossAdapter:
    return IbossAdapter(
        load_profile(PROFILE_PATH),
        name="아이보스",
        scope_note="전체 분류의 공개 모집 중 의뢰 · 제목 선별 없이 본문 확인 · 요청 예산에 따라 이어서 탐색",
        adapter_version="0.1.2",
        parser_version="iboss-0.1.2",
    )
