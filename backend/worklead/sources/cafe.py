"""Metadata: session work never goes through the anonymous Fetcher."""
from .base import Capability


class CafeSource:
    source_id = 'daangn_cafe'
    name = '당근 카페'
    kind = 'site'
    adapter_version = 'cafe-1'
    parser_version = 'cafe-1'
    scope_note = '전용 Edge 세션 · 승인된 카페 · 내 글 대조·날짜 범위 미확보로 자동 수집 보류'
    default_interval_minutes = 180
    allowed_hosts = {'cafe.daangn.com'}
    daily_request_budget = 300
    min_interval_seconds = 8
    browser_collection = True

    def describe_capabilities(self):
        return [Capability('discovery', '카페 글 수집', 'unverified', '내 글 제외·최근 작성 시각의 지원 경로 미확보'), Capability('recheck', '원문 재확인', 'unverified', '전용 브라우저 큐 필요'), Capability('own_posts', '내 글 대조', 'unverified', '지원 경로 미확보'), Capability('full_comments', '전체 댓글', 'unverified', '조사한 웹 화면은 앱 전용')]
