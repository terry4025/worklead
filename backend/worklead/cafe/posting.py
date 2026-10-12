"""Durable one-post state machine. Ownership must be proven before typing."""
import json
from pathlib import Path
from sqlalchemy import select
from ..api import schemas as S
from ..models import AppSetting, Cafe, CafeBoard, CafePostAttempt, new_id
from ..services import cafe as svc
from .parsing import CafePageError, safe_cafe_url


def template(s):
    row = s.get(AppSetting, 'cafe_template')
    default = json.loads(Path(__file__).with_name('default_template.json').read_text(encoding='utf-8'))
    return S.CafeTemplate.model_validate(row.value if row else default)


def update_template(s, value):
    current = template(s)
    title, body = value.title, value.body.replace('\r\n', '\n').replace('\r', '\n')
    if current.title == title and current.body == body: return current
    updated = S.CafeTemplate(version=current.version + 1, title=title, body=body)
    row = s.get(AppSetting, 'cafe_template')
    if row: row.value = updated.model_dump()
    else: s.add(AppSetting(key='cafe_template', value=updated.model_dump()))
    return updated


def attempt_out(row):
    return S.CafeAttempt.model_validate({k: getattr(row, k) for k in S.CafeAttempt.model_fields})


def list_attempts(s):
    return S.CafeAttemptList(items=[attempt_out(row) for row in s.scalars(select(CafePostAttempt).order_by(CafePostAttempt.created_at.desc()).limit(500))])


def lines(body):
    return [line if line != '\u3164' else '' for line in body.replace('\r\n', '\n').split('\n')]


def publish(app, browser, cafe_id, navigate, reserve, *, repost=False):
    now = app.clock()
    with app.db.session() as s:
        if not svc.consent_accepted(s) or svc.session_row(s).state != 'logged_in':
            raise svc.CafeError(409, 'cafe_session_required', '위험 안내 확인과 로그인 확인이 필요합니다')
        cafe = s.get(Cafe, cafe_id)
        board = s.scalar(select(CafeBoard).where(CafeBoard.cafe_id == cafe_id, CafeBoard.approved.is_(True)))
        if cafe is None or cafe.status != 'approved' or not cafe.joined or board is None or not board.post_allowed:
            raise svc.CafeError(409, 'cafe_rule_unverified', '가입·카페 승인·게시판 허용 근거가 필요합니다')
        previous = list(s.scalars(select(CafePostAttempt).where(CafePostAttempt.cafe_id == cafe_id).order_by(CafePostAttempt.created_at.desc())))
        removed_urls = {r.post_url for r in previous if r.status == 'removed' and r.post_url}
        if repost and not removed_urls:
            raise svc.CafeError(409, 'cafe_repost_unverified', '삭제·숨김이 확인된 글만 직접 재게시할 수 있습니다')
        saved = next((r for r in previous if r.status in ('posted', 'already_exists', 'posted_mismatch') and not (repost and r.post_url in removed_urls)), None)
        if saved:
            return {'status': 'already_exists', 'post_url': saved.post_url}
        if any(r.status == 'removed' for r in previous) and not repost:
            return {'status': 'skipped_rule', 'reason': 'removed_no_auto_repost'}
        if any(r.status == 'pending' or r.error_code == 'cafe_submission_uncertain' for r in previous):
            raise svc.CafeError(409, 'cafe_reconciliation_required', '이전 게시 결과가 불확실합니다. 자동 재시도하지 않습니다')
        value = template(s)
        attempt = CafePostAttempt(id=new_id('cfp'), cafe_id=cafe_id, board_id=board.id, template_version=value.version, status='pending', created_at=now)
        s.add(attempt)
        s.flush()
        attempt_id = attempt.id
    submitted = False
    try:
        # The production implementation raises here because no own-post route
        # was observed. Test readers supply complete synthetic account evidence.
        owned, complete = browser.owned_posts(cafe.url, navigate)
        if not complete: raise CafePageError('cafe_ownership_unverified', '내 글 전체 대조가 완료되지 않았습니다')
        existing = next((p for p in owned if p['title'] == value.title or p['body'][:100] == value.body[:100]), None)
        if existing:
            safe_cafe_url(existing['url'])
            if not existing['url'].startswith(cafe.url + '/posts/'):
                raise CafePageError('cafe_ownership_unverified', '다른 카페의 글은 중복 판정 근거로 사용할 수 없습니다')
            result = {'status': 'already_exists', 'post_url': existing['url']}
        else:
            browser.prepare_editor(cafe.url, board.name, value, navigate)
            # Recheck approval and current template after browser work. Count
            # conservatively before the single submission, even if its result is lost.
            reserve()
            with app.db.session() as s:
                current = s.get(Cafe, cafe_id)
                current_board = s.get(CafeBoard, board.id)
                if current.status != 'approved' or not current_board.approved or not current_board.post_allowed or template(s).version != value.version or svc.session_row(s).state != 'logged_in':
                    raise svc.CafeError(409, 'cafe_scope_changed', '게시 준비 중 승인·템플릿·세션이 변경되었습니다')
                usage, settings = svc.session_row(s), svc.get_settings(s)
                if usage.usage_day != svc.day_key(app.clock()):
                    usage.usage_day, usage.page_count, usage.post_count = svc.day_key(app.clock()), 0, 0
                if usage.post_count >= settings['daily_post_limit']:
                    raise svc.CafeError(409, 'cafe_post_budget', '오늘 게시 한도에 도달했습니다')
                if usage.last_post_at and (app.clock() - usage.last_post_at).total_seconds() < settings['min_post_minutes'] * 60:
                    raise svc.CafeError(409, 'cafe_post_interval', '게시 간격이 지나지 않았습니다')
                usage.post_count += 1
                usage.last_post_at = app.clock()
            submitted = True
            url = browser.submit_post()
            safe_cafe_url(url)
            if not url.startswith(cafe.url + '/posts/') or url.endswith('/new'):
                raise CafePageError('cafe_submission_uncertain', '게시된 글 주소를 확인하지 못했습니다')
            # Persist URL before verification. Never submit again on a lost response.
            with app.db.session() as s: s.get(CafePostAttempt, attempt_id).post_url = url
            navigate(url)
            actual = browser.read_post()
            same = actual['title'] == value.title and lines(actual['body']) == lines(value.body)
            result = {'status': 'posted' if same else 'posted_mismatch', 'post_url': url}
        with app.db.session() as s:
            row = s.get(CafePostAttempt, attempt_id)
            row.status, row.post_url, row.verified_at = result['status'], result['post_url'], app.clock()
            if row.status == 'posted_mismatch':
                row.error_code, row.error_message = 'cafe_post_mismatch', '실제 글과 템플릿·줄바꿈이 다릅니다. 자동 수정·재게시하지 않습니다'
                from ..engine.events import notify
                notify(s, 'source_issue', '카페 게시 내용 확인이 필요합니다', source_id='daangn_cafe', now=app.clock(), dedupe_key='cafe-mismatch:' + attempt_id)
        return result
    except Exception as exc:
        with app.db.session() as s:
            row = s.get(CafePostAttempt, attempt_id)
            row.status = 'failed'
            row.error_code = 'cafe_submission_uncertain' if submitted else getattr(exc, 'code', 'cafe_preparation_failed')
            row.error_message = '게시 결과 불확실. 원문·내 글을 직접 대조하세요. 자동 재시도하지 않습니다' if submitted else str(exc) if isinstance(exc, (CafePageError, svc.CafeError)) else '게시 준비 실패'
        raise CafePageError(row.error_code, row.error_message) from None
