from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tests.conftest import make_ctx
from worklead.engine.http import FetchResult, Fetcher, PolicyStop, RunHalt
from worklead.sources.base import ParseError, PostNotCollectable, PostRef, TaskSpec
from worklead.sources.iboss import create
from worklead.sources.profiled import TaskFailed

FIXTURES = Path(__file__).parent / "fixtures" / "iboss"
URL = "https://www.i-boss.co.kr/ab-1958-900001"
NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def fixture(name):
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def response(text, url=URL, outcome="ok", status=200):
    return FetchResult(url, url, status, outcome, text)


class Saved:
    def __init__(self, text, outcome="ok", status=200):
        self.text = text
        self.outcome = outcome
        self.status = status
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return response(self.text, url, self.outcome, self.status)


def task(page=None):
    return TaskSpec("iboss-all-public-v1", "all-public", "전체 분류", cursor=page)


def test_plan_scans_all_public_categories_without_title_or_keyword_selection():
    adapter = create()
    plan = adapter.plan_discovery([{"id": "dev", "enabled": False, "keywords": ["엑셀"]}], NOW)
    assert len(plan.tasks) == 1
    assert plan.tasks[0].query is None
    assert plan.depth_limit == 0
    assert plan.region_list_status == "not_applicable"
    assert plan.target_units_total is None
    assert adapter.persist_discovery_pages is True
    assert (adapter.discovery_request_limit, adapter.batch_resume_seconds) == (120, 60)
    assert (adapter.daily_request_budget, adapter.min_interval_seconds, adapter.default_interval_minutes) == (600, 6, 180)
    capabilities = {cap.key: cap for cap in adapter.describe_capabilities()}
    assert capabilities["nationwide_search"].label == "지역 제한 없는 공개 목록"
    assert capabilities["incremental"].label == "중복 제거·이어보기"


def test_all_actual_list_cards_are_kept_deduped_and_unrelated_sidebar_is_ignored():
    saved = Saved(fixture("list"))
    found = create().discover(task(), saved)
    assert [r.source_post_id for r in found.refs] == ["900001", "900002"]
    assert found.refs[0].title_hint == "업무 관련 도움 요청합니다"
    assert found.refs[1].title_hint == "마케팅 실행사 찾습니다"
    assert found.next_cursor == "2"
    assert found.metadata["body_filter"] == "all_public"
    assert found.metadata["listed_posts"] == 2
    assert found.metadata["next_page"] == 2
    assert saved.urls == ["https://www.i-boss.co.kr/ab-1957?sort=now"]


def test_installed_user_agent_public_list_uses_subject_without_txt_wrapper():
    found = create().discover(task(), Saved(fixture("list-installed-ua")))
    assert len(found.refs) == 20
    assert len({ref.source_post_id for ref in found.refs}) == 20
    assert found.next_cursor == "2"


@pytest.mark.parametrize("mutated", [
    lambda s: s.replace('class="subject"', 'class="unrecognized"', 1),
    lambda s: s.replace('/ab-1958-910001', '/unexpected/910001', 1),
    lambda s: s.replace('<a href="/ab-1958-910001">', '<a href="/ab-1958-910099">합성</a><a href="/ab-1958-910001">', 1),
])
def test_compact_list_missing_or_ambiguous_real_post_is_not_silently_skipped(mutated):
    with pytest.raises(ParseError):
        create().discover(task(), Saved(mutated(fixture("list-installed-ua"))))


def test_pagination_uses_next_public_link_and_can_continue_past_first_block():
    markup = fixture("list").replace('>1</strong>', '>10</strong>')
    found = create().discover(task("10"), Saved(markup))
    assert found.next_cursor == "11"
    assert found.metadata["page"] == 10
    last = create().discover(task("11"), Saved(markup))
    assert last.next_cursor is None
    assert last.metadata["has_next"] is False


@pytest.mark.parametrize("cursor", ["0", "-1", "x", "1&category_1=SIT", "https://example.test"])
def test_invalid_cursor_never_causes_a_request(cursor):
    saved = Saved(fixture("list"))
    with pytest.raises(ParseError):
        create().discover(task(cursor), saved)
    assert saved.urls == []


@pytest.mark.parametrize("mutated", [
    lambda s: s.replace('class="proposal_bd"', 'class="changed"'),
    lambda s: s.replace('class="ABA-list-paging AB-alignc"', 'class="changed"'),
    lambda s: s.replace('/ab-1958-900001', 'https://other.example/ab-1958-900001'),
    lambda s: s.replace('sort=now&amp;PB_', 'category_1=SIT&amp;PB_'),
    lambda s: s.replace('PB_1415256624=2', 'PB_1415256624=20'),
])
def test_changed_or_unsafe_list_structure_is_not_silent_empty_success(mutated):
    with pytest.raises(ParseError):
        create().discover(task(), Saved(mutated(fixture("list"))))


def test_explicit_empty_list_is_distinct_from_broken_markup():
    empty = '<div class="proposal_bd"><p>등록된 게시물이 없습니다</p><div class="ABA-list-paging"></div></div>'
    found = create().discover(task(), Saved(empty))
    assert found.refs == [] and found.next_cursor is None
    with pytest.raises(ParseError):
        create().discover(task(), Saved(empty.replace('등록된 게시물이 없습니다', '')))


def test_empty_requested_page_after_explicit_last_page_finishes_shrunk_listing():
    # Observed after midnight: requested page 5, empty wrap, pager clamped to 3.
    found = create().discover(task("5"), Saved(fixture("list-out-of-range")))
    assert found.refs == [] and found.next_cursor is None
    assert found.metadata["terminal_reason"] == "requested_page_after_last"
    assert found.metadata["reported_last_page"] == 3


@pytest.mark.parametrize("mutated", [
    lambda s: s.replace('class="wrap"', 'class="unexpected"'),
    lambda s: s.replace('class="wrap"></div>', 'class="wrap">로딩 중</div>'),
    lambda s: s.replace('class="wrap"></div>', 'class="wrap"><div></div></div>'),
    lambda s: s.replace('LF_page_link_current', 'unknown_current'),
    lambda s: s.replace('<b>3</b>', '<b>5</b>'),
    lambda s: s.replace('<b>3</b>', '<b>unknown</b>'),
    lambda s: s.replace('PB_1415256624=2', 'PB_1415256624=4'),
    lambda s: s.replace('ab-1957?sort=now&amp;PB_1415256624=2', 'javascript:void(0)'),
])
def test_empty_without_complete_out_of_range_proof_remains_parse_error(mutated):
    with pytest.raises(ParseError):
        create().discover(task("5"), Saved(mutated(fixture("list-out-of-range"))))


def test_empty_current_page_is_not_assumed_to_be_a_normal_terminal_page():
    with pytest.raises(ParseError):
        create().discover(task("3"), Saved(fixture("list-out-of-range")))


def test_public_detail_preserves_fields_and_excludes_contacts_ads_and_scripts():
    result = response(fixture("detail"))
    adapter = create()
    assert adapter.block_detector(result) is None
    post = adapter.parse(PostRef(URL, "900001"), result, NOW)
    assert post.title == "주문 관리 기능 개발 의뢰"
    assert post.body.startswith("기존 웹사이트에 주문 관리 기능")
    assert "서버와 DB 연동" in post.body
    assert "synthetic-contact" not in post.body
    assert "private_code" not in post.body
    assert "900만원" not in post.body
    assert post.canonical_url == URL
    assert post.source_post_id == "900001"
    assert (post.pay.min, post.pay.max, post.pay.unit) == (100000, 3000000, "project")
    assert post.pay.evidence[0].start is None
    assert post.published.at == datetime(2026, 9, 22, 2, 43, tzinfo=UTC)
    assert post.deadline.precision == "day"
    assert post.deadline.at == datetime(2026, 9, 29, 15, tzinfo=UTC)
    assert post.source_status == "open"


@pytest.mark.parametrize(("budget", "low", "high", "unit"), [
    ("월간 최소 100만원 ~ 최대 1억원", 1000000, 100000000, "month"),
    ("건당 최소 50만원", 500000, None, "project"),
    ("건당 최대 300만원", None, 3000000, "project"),
    ("성과당 최소 10만원 ~ 최대 100만원", 100000, 1000000, "unknown"),
    ("최소 100만원 ~ 최대 500만원", 1000000, 5000000, "unknown"),
    ("협의", None, None, "unknown"),
    ("건당 0원", 0, 0, "project"),
])
def test_budget_bounds_and_source_payment_unit_are_preserved(budget, low, high, unit):
    old = '<span>건당</span> 최소 <strong>10만원</strong> ~ 최대 <strong>300만원</strong>'
    post = create().parse(PostRef(URL), response(fixture("detail").replace(old, budget)), NOW)
    assert (post.pay.min, post.pay.max, post.pay.unit) == (low, high, unit)


def test_date_only_deadline_includes_last_korean_day_then_closes():
    adapter = create()
    result = response(fixture("detail"))
    assert adapter.parse(PostRef(URL), result, datetime(2026, 9, 30, 14, 59, 59, tzinfo=UTC)).source_status == "open"
    assert adapter.parse(PostRef(URL), result, datetime(2026, 9, 30, 15, tzinfo=UTC)).source_status == "closed"


def test_current_request_finished_badge_wins_over_future_deadline_but_body_words_do_not():
    markup = fixture("detail").replace('2026.09.30', '2026.10.30').replace('작업은 온라인', '마감된 의뢰 내용은 참고만 하세요. 작업은 온라인')
    assert create().parse(PostRef(URL), response(markup), NOW).source_status == "open"
    # Observed current-post state is inside request-info/close_date. A dline
    # elsewhere is a related article's state, not a current-post action.
    markup = markup.replace('<span>오늘 마감</span>', '<span class="deadline finished bd_R30">마감</span>')
    post = create().parse(PostRef(URL), response(markup), NOW)
    assert post.source_status == "closed"
    assert post.status_evidence[0].quote == "마감"


@pytest.mark.parametrize("name", ["detail", "detail-installed-ua"])
def test_related_closed_article_inside_current_article_does_not_close_open_request(name):
    markup = fixture(name).replace('2026.09.30', '2026.10.09')
    related = '<div class="more_articles"><div class="list-container"><div class="cell"><div class="dline">마감된 의뢰</div></div></div></div>'
    markup = markup.replace('<div id="BD1957-view">', '<div id="BD1957-view">' + related, 1)
    post = create().parse(PostRef(URL), response(markup), NOW)
    assert post.source_status == "open"
    assert post.status_evidence[0].quote != "마감된 의뢰"


def current_contact_markup(name, label="마감된 의뢰입니다"):
    markup = fixture(name).replace('2026.09.30', '2026.10.30')
    if name == "detail":
        return markup.replace('로그인 후 이용가능합니다.', label)
    contact = '<div class="contact-info"><div class="contact-wrap"><div class="wrap"><div class="user_info_contact"><div class="btn_cash_wrap"><span class="btn_cash done">' + label + '</span></div></div></div></div></div>'
    # Preserve the actual compact layout: wrapper > vf_wrap > contact-info.
    return markup.replace('<div id="BD1957-view">', '<div id="BD1957-view"><div id="ABA-wrapper-box-set"><div class="vf_wrap">' + contact, 1).replace('</body>', '</div></div></body>', 1)


@pytest.mark.parametrize("name", ["detail", "detail-installed-ua"])
def test_visible_current_contact_closed_cta_overrides_future_deadline(name):
    post = create().parse(PostRef(URL), response(current_contact_markup(name)), NOW)
    assert post.source_status == "closed"
    assert post.status_evidence[0].quote == "마감된 의뢰입니다"
    assert "마감된 의뢰입니다" not in post.body


@pytest.mark.parametrize("location", ["related", "body"])
def test_closed_contact_lookalike_in_other_content_does_not_close_current_post(location):
    markup = current_contact_markup("detail", "로그인 후 이용가능합니다.")
    fake = '<div class="contact-info"><div class="user_info_contact"><span class="btn_cash done">마감된 의뢰입니다</span></div></div>'
    if location == "related":
        markup = markup.replace('<div class="content_view">', '<div class="content_view"><div class="more_articles">' + fake + '</div>', 1)
    else:
        markup = markup.replace('<div class="ABA-view-body ABA-article-contents">', '<div class="ABA-view-body ABA-article-contents">' + fake, 1)
    assert create().parse(PostRef(URL), response(markup), NOW).source_status == "open"


@pytest.mark.parametrize("hidden", ['hidden', 'style="display: none"', 'style="visibility:hidden"'])
def test_hidden_current_contact_closed_template_does_not_close_current_post(hidden):
    markup = current_contact_markup("detail").replace('class="user_info_contact"', 'class="user_info_contact" ' + hidden)
    assert create().parse(PostRef(URL), response(markup), NOW).source_status == "open"


def test_contact_access_expiry_alone_is_not_an_explicit_closed_request():
    markup = current_contact_markup("detail", "열람기간 지남")
    assert create().parse(PostRef(URL), response(markup), NOW).source_status == "open"


def test_missing_date_is_unknown_not_assumed_open():
    markup = fixture("detail").replace('2026.09.30<span>오늘 마감</span>', '협의')
    post = create().parse(PostRef(URL), response(markup), NOW)
    assert post.deadline.at is None and post.source_status is None


@pytest.mark.parametrize("markup", [
    '<h1>로그인</h1><form><input type="password"></form>',
    '<p>로그인이 필요합니다</p>',
])
def test_login_wall_blocks_but_the_contact_login_prompt_does_not(markup):
    adapter = create()
    assert adapter.block_detector(response(markup)) == "login_required"
    with pytest.raises(PostNotCollectable) as caught:
        adapter.parse(PostRef(URL), response(markup), NOW)
    assert caught.value.code == "login_required"
    with pytest.raises(TaskFailed):
        adapter.discover(task(), Saved(markup))


@pytest.mark.parametrize("mutated", [
    lambda s: s.replace('ABA-view-body', 'changed'),
    lambda s: s.replace('class="request-info"', 'class="changed"'),
    lambda s: s.replace('2026-09-22 11:43', 'yesterday'),
    lambda s: s.replace('2026.09.30', '2026.99.99'),
])
def test_detail_layout_change_fails_instead_of_ingesting_the_whole_page(mutated):
    with pytest.raises(ParseError):
        create().parse(PostRef(URL), response(mutated(fixture("detail"))), NOW)


def test_detail_identity_must_match_requested_post():
    with pytest.raises(ParseError):
        create().parse(PostRef(URL, "other"), response(fixture("detail")), NOW)


def test_missing_public_body_is_parse_failure_despite_normal_contact_login_prompt():
    with pytest.raises(ParseError) as caught:
        create().parse(PostRef(URL), response(fixture("detail").replace('ABA-view-body', 'changed')), NOW)
    assert not isinstance(caught.value, PostNotCollectable)


def test_explicit_private_body_is_not_stored_as_a_valid_post():
    import re
    markup = re.sub(r'(<div class="ABA-view-body ABA-article-contents">).*?(</div>)',
                    r'\1로그인 후 열람 가능합니다.\2', fixture("detail"), flags=re.S)
    with pytest.raises(PostNotCollectable) as caught:
        create().parse(PostRef(URL), response(markup), NOW)
    assert caught.value.code == "content_unavailable"


@pytest.mark.parametrize(("outcome", "status"), [("not_found", 404), ("gone", 410), ("server_error", 503)])
def test_revalidation_keeps_unknown_deletion_and_does_not_parse_http_errors(outcome, status):
    result = create().revalidate(URL, Saved("", outcome, status), NOW)
    assert result.source_status is None and result.access_status == "error"
    assert result.http_status == status
    with pytest.raises(TaskFailed):
        create().discover(task(), Saved("", outcome, status))


def test_revalidation_uses_same_public_parser():
    result = create().revalidate(URL, Saved(fixture("detail")), NOW)
    assert result.source_status == "open" and result.parsed.title == "주문 관리 기능 개발 의뢰"


@pytest.mark.parametrize(("name", "state", "low", "high"), [
    ("detail-observed", "open", 100000, 3000000),
    ("closed-observed", "closed", 2000000, 10000000),
    ("detail-installed-ua", "open", 100000, 3000000),
])
def test_sanitized_real_dom_preserves_adjacent_date_badge_and_closed_control(name, state, low, high):
    post = create().parse(PostRef(URL), response(fixture(name)), NOW)
    assert post.title == "합성 웹사이트 제작 의뢰"
    assert post.source_status == state
    assert (post.pay.min, post.pay.max, post.pay.unit) == (low, high, "project")
    assert post.deadline.precision == "day"
    assert "온라인으로 진행" in post.body


@pytest.mark.parametrize(("status", "error"), [(403, PolicyStop), (429, RunHalt)])
def test_adapter_requests_use_common_http_policy_stops_without_retry(tmp_path, status, error):
    adapter = create()
    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    requested = []
    def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(status, headers={"Retry-After": "60"}, text="Access unavailable")
    fetcher = Fetcher(ctx.db, allowed_hosts=adapter.allowed_hosts, user_agent="Worklead-test", daily_budget=10,
                      min_interval_s=0, block_detector=adapter.block_detector, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(error):
            adapter.discover(task(), fetcher)
        assert requested == ["/robots.txt", "/ab-1957"]
    finally:
        fetcher.close()
        ctx.db.dispose()
