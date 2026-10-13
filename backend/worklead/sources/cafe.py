"""Metadata: session work never goes through the anonymous Fetcher."""
from .base import Capability


class CafeSource:
    source_id = 'daangn_cafe'
    name = '당근 카페'
    kind = 'site'
    adapter_version = 'cafe-2'
    parser_version = 'cafe-2'
    scope_note = '전용 Edge · 최근 30일 상대 시각 · 템플릿 제목·seyj.kr 제외 · 웹에 보이는 댓글'
    default_interval_minutes = 180
    allowed_hosts = {'cafe.daangn.com'}
    daily_request_budget = 300
    min_interval_seconds = 8
    browser_collection = True

    def describe_capabilities(self):
        return [Capability('discovery', '웹 카페 글 수집', 'supported', '렌더링된 목록·상대 작성 시각 기준 최근 30일'), Capability('recheck', '원문 재확인', 'unverified', '전용 브라우저 큐 필요'), Capability('own_posts', '내 글 대조', 'unsupported', '제목·포트폴리오 링크로 제외하며 작성자 소유를 판정하지 않음'), Capability('title_duplicates', '같은 제목 게시 건너뛰기', 'supported', '승인된 게시판에 보이는 같은 템플릿 제목'), Capability('visible_comments', '웹 댓글·답글', 'supported', '웹에서 펼칠 수 있는 답글 포함'), Capability('full_comments', '전체 댓글', 'unsupported', '앱에서 전체 확인 필요')]
