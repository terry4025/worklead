"""Browser ParsedPost -> lead pipeline. Unknown ownership/range fails closed."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy import select
from ..models import AppSetting, Cafe, CafeBoard, CafePostAttempt, SourcePolicy
from ..sources.base import ParsedPost
from ..engine.pipeline import ingest
from ..services import cafe as svc


@dataclass
class CafePost:
    post: ParsedPost
    is_own: bool | None


@dataclass
class CafePage:
    posts: list[CafePost]
    complete: bool


def collect(app, reader):
    """Called by the browser owner. A reader proves ownership and full range.

    Checkpoints advance only after a complete board scan commits. Natural
    browser access obeys F0, never the anonymous HTTP Fetcher adapter.
    """
    now = app.clock()
    with app.db.session() as s:
        if not svc.consent_accepted(s):
            raise svc.CafeError(409, 'cafe_consent_required', '위험 안내 확인이 필요합니다')
        policy = s.get(SourcePolicy, 'daangn_cafe')
        if policy is None or policy.status != 'allowed':
            raise svc.CafeError(409, 'policy_not_allowed', '카페 수집 정책을 확인하세요')
        if svc.session_row(s).state != 'logged_in':
            raise svc.CafeError(409, 'cafe_session_required', '로그인 확인이 필요합니다')
        boards = list(s.scalars(select(CafeBoard).join(Cafe, Cafe.id == CafeBoard.cafe_id).where(Cafe.status.in_(('approved', 'eligible')), CafeBoard.kind.in_(('job_seek', 'job_offer', 'free')))))
        cfg = s.get(AppSetting, 'cafe_collection_checkpoints')
        checkpoints = dict(cfg.value if cfg else {})
        own_urls = set(s.scalars(select(CafePostAttempt.post_url).where(CafePostAttempt.post_url.is_not(None))))
    counts = {'created': 0, 'updated': 0, 'unchanged': 0, 'skipped_own': 0, 'skipped_old': 0, 'boards': 0}
    for board in boards:
        since = datetime.fromisoformat(checkpoints[board.id]) if board.id in checkpoints else now - timedelta(days=30)
        page = reader(board, since)
        if not page.complete:
            raise svc.CafeError(409, 'cafe_listing_incomplete', '전체 목록 범위를 확인하지 못해 마지막 확인 시각을 갱신하지 않습니다')
        for envelope in page.posts:
            if envelope.is_own is None and envelope.post.canonical_url not in own_urls:
                raise svc.CafeError(409, 'cafe_ownership_unverified', '내 글 제외를 보장할 수 없어 수집을 중단했습니다')
            stamp = envelope.post.published
            if not envelope.is_own and envelope.post.canonical_url not in own_urls and (stamp.at is None or stamp.precision not in ('exact', 'day')):
                raise svc.CafeError(409, 'cafe_date_unverified', '최근 30일·증분 조회에 필요한 작성 시각이 확인되지 않았습니다')
        with app.db.session() as s:
            current = s.get(Cafe, board.cafe_id)
            if current is None or current.status not in ('eligible', 'approved') or s.get(SourcePolicy, 'daangn_cafe').status != 'allowed':
                raise svc.CafeError(409, 'cafe_scope_changed', '카페 승인·수집 정책이 변경되어 저장을 중단했습니다')
            for envelope in page.posts:
                post = envelope.post
                if envelope.is_own or post.canonical_url in own_urls:
                    counts['skipped_own'] += 1
                    continue
                if post.published.at < since or post.published.at > now:
                    counts['skipped_old'] += 1
                    continue
                result = ingest(s, 'daangn_cafe', post, parser_version='cafe-1', now=now, provider=app.provider, path=(current.id, current.region, board.id))
                counts[result.kind] += 1
            checkpoints[board.id] = now.isoformat()
            cfg = s.get(AppSetting, 'cafe_collection_checkpoints')
            if cfg:
                cfg.value = dict(checkpoints)
            else:
                s.add(AppSetting(key='cafe_collection_checkpoints', value=dict(checkpoints)))
            counts['boards'] += 1
    app.hub.notify()
    return counts
