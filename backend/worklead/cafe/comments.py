"""Local inquiry queue. A partial web page is never a successful comment poll."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import re
from sqlalchemy import func, select, text
from ..api import schemas as S
from ..models import AppSetting, Cafe, CafeComment, CafePostAttempt, Lead, new_id
from ..engine.events import emit, notify
from ..engine.pipeline import ingest
from ..sources.base import ParsedPost
from ..services import cafe as svc


@dataclass
class Comment:
    author: str
    body: str
    written_at: datetime | None
    is_own: bool | None
    external_id: str | None = None
    written_raw: str | None = None
    is_reply: bool = False


@dataclass
class CommentPage:
    comments: list[Comment]
    complete: bool
    confirmed_removed: bool = False


def classify(body):
    compact = re.sub(r'\s+', '', body).lower()
    if re.search(r'견적|작업문의|제작문의|개발문의|가능할까요|연락주세요|채팅드렸|부탁드|제작가능', compact): return 'request'
    if '?' in body or re.search(r'궁금|질문|어떻게|무엇|언제|어디|어떤', compact): return 'question'
    return 'other'


def save_page(app, attempt_id, page):
    now = app.clock()
    if not page.complete and not page.confirmed_removed:
        raise svc.CafeError(409, 'cafe_comments_incomplete', '전체 댓글을 읽지 못했습니다. 완료로 처리하지 않습니다')
    if not page.confirmed_removed and any(c.is_own is None or not c.external_id and c.written_at is None for c in page.comments):
        raise svc.CafeError(409, 'cafe_comment_identity_unverified', '내 댓글 제외·안정적인 중복 키를 확인하지 못했습니다')
    if any(len(c.body) > 20000 or len(c.author) > 200 or c.external_id and len(c.external_id) > 200 or c.written_at and c.written_at.tzinfo is None for c in page.comments):
        raise svc.CafeError(422, 'cafe_comment_invalid', '댓글 길이·시각 형식이 올바르지 않습니다')
    created = 0
    with app.db.session() as s:
        if not svc.consent_accepted(s) or svc.session_row(s).state != 'logged_in':
            raise svc.CafeError(409, 'cafe_session_required', '댓글 읽기를 저장하려면 로그인 확인이 필요합니다')
        attempt = s.get(CafePostAttempt, attempt_id)
        if attempt is None or attempt.status != 'posted' or not attempt.post_url:
            raise svc.CafeError(409, 'cafe_not_posted', '확인된 내 게시글만 댓글을 읽습니다')
        settings = svc.get_settings(s)
        if attempt.created_at < now - timedelta(days=settings['comment_monitor_days']):
            return {'created': 0, 'expired': True}
        if page.confirmed_removed:
            attempt.status, attempt.error_code = 'removed', 'cafe_removed'
            attempt.error_message = '삭제·숨김을 확인하여 재게시와 댓글 감시를 중지했습니다'
            return {'created': 0, 'removed': True}
        for value in page.comments:
            if value.is_own: continue
            key = value.external_id or hashlib.sha256((attempt.post_url + '\n' + value.written_at.isoformat() + '\n' + value.body).encode()).hexdigest()
            row = s.scalar(select(CafeComment).where(CafeComment.attempt_id == attempt_id, CafeComment.external_id == key))
            new = row is None
            if new:
                row = CafeComment(id=new_id('cfc'), attempt_id=attempt_id, external_id=key, author_nickname=value.author, body=value.body, is_reply=value.is_reply, memo='')
                s.add(row)
                s.flush()
                created += 1
            row.author_nickname, row.body = value.author, value.body
            row.written_at, row.written_raw, row.is_reply = value.written_at, value.written_raw, value.is_reply
            row.classification = classify(value.body)
            if new and row.classification == 'request':
                notify(s, 'cafe_request', '새 카페 문의가 있습니다. Worklead에서 확인하세요', source_id='daangn_cafe', now=now, dedupe_key='cafe-request:' + row.id)
        cfg = s.get(AppSetting, 'cafe_comment_checks')
        checks = dict(cfg.value if cfg else {})
        checks[attempt_id] = now.isoformat()
        if cfg: cfg.value = checks
        else: s.add(AppSetting(key='cafe_comment_checks', value=checks))
        emit(s, 'cafe.comments_changed', {'created': created})
    app.hub.notify()
    return {'created': created, 'complete': True}


def monitor(app, reader):
    now = app.clock()
    with app.db.session() as s:
        cfg = svc.get_settings(s)
        attempts = list(s.scalars(select(CafePostAttempt).where(CafePostAttempt.status == 'posted', CafePostAttempt.created_at >= now - timedelta(days=cfg['comment_monitor_days']))))
        checks = s.get(AppSetting, 'cafe_comment_checks')
        previous = dict(checks.value if checks else {})
    created = checked = 0
    for attempt in attempts:
        last = datetime.fromisoformat(previous[attempt.id]) if attempt.id in previous else None
        if last and last + timedelta(minutes=cfg['comment_interval_minutes']) > now: continue
        result = save_page(app, attempt.id, reader(attempt.post_url))
        checked += 1
        created += result['created']
    return {'checked': checked, 'created': created}


def comment_out(s, row):
    attempt = s.get(CafePostAttempt, row.attempt_id)
    cafe = s.get(Cafe, attempt.cafe_id)
    return S.CafeCommentOut(**{k: getattr(row, k) for k in S.CafeCommentOut.model_fields if k not in ('cafe_name', 'post_url')}, cafe_name=cafe.name, post_url=attempt.post_url)


def listing(s):
    return S.CafeCommentList(items=[comment_out(s, row) for row in s.scalars(select(CafeComment).order_by(CafeComment.read_at.is_not(None), CafeComment.written_at.desc(), CafeComment.id.desc()).limit(500))], unread_count=s.scalar(select(func.count()).select_from(CafeComment).where(CafeComment.read_at.is_(None))) or 0)


def get(s, id_):
    row = s.get(CafeComment, id_)
    if row is None: raise svc.CafeError(404, 'cafe_comment_not_found', '문의 댓글을 찾을 수 없습니다')
    return row


def update(s, id_, patch, now):
    row = get(s, id_)
    if patch.read is not None: row.read_at = now if patch.read else None
    if patch.memo is not None: row.memo = patch.memo
    return comment_out(s, row)


def make_lead(s, id_, now, provider):
    s.execute(text('BEGIN IMMEDIATE'))
    row = get(s, id_)
    if row.lead_id and s.get(Lead, row.lead_id): return s.get(Lead, row.lead_id)
    attempt = s.get(CafePostAttempt, row.attempt_id)
    post = ParsedPost(title='카페 문의 · ' + row.body[:60], body=row.body, original_url=attempt.post_url, canonical_url=attempt.post_url, source_post_id='cafe-comment:' + row.id, contact_channel='당근 카페 원문 댓글')
    result = ingest(s, 'manual', post, source_kind='manual', parser_version='cafe-comment-1', now=now, provider=provider)
    row.lead_id = result.lead_id
    return s.get(Lead, result.lead_id)


def draft(s, id_, now, provider):
    from ..analysis.service import analyze, inquiry_draft
    from ..analysis.types import PostInput
    from ..services.settings import get_profile, ttl_hours
    row = get(s, id_)
    analysis = analyze(PostInput(title='카페 문의', body=row.body, observed_at=now, source_kind='manual'), get_profile(s), now, ttl_hours(s))
    return S.CafeCommentDraft(text=inquiry_draft('카페 문의', analysis, reply=True))
