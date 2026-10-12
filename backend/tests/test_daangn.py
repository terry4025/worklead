from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest

from tests.conftest import make_ctx, client_for
from worklead.engine import runs
from worklead.engine.http import FetchResult
from worklead.engine.worker import Worker
from worklead.sources.base import ParseError, PostNotCollectable, PostRef, TaskSpec
from worklead.sources.daangn import create
from worklead.sources.daangn.adapter import _Document

FIXTURES = Path(__file__).parent / "fixtures" / "daangn"
URL = "https://jobs.daangn.com/job-posts/프로그램-제작-fixture00001"
NOW = datetime(2026, 9, 28, tzinfo=UTC)
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
INDEX = f'<sitemapindex {NS}><sitemap><loc>https://jobs.daangn.com/sitemaps/job-posts-1.xml</loc></sitemap><sitemap><loc>https://jobs.daangn.com/sitemaps/job-posts-2.xml</loc></sitemap><sitemap><loc>https://jobs.daangn.com/sitemaps/region-searches.xml</loc></sitemap></sitemapindex>'
JOBS = f'<urlset {NS}><url><loc>{URL}</loc><lastmod>2026-09-28T00:00:00Z</lastmod></url><url><loc>https://jobs.daangn.com/job-posts/식당-서빙-abcVBA123456</loc></url></urlset>'


def task():
    return create().plan_discovery([{"id": "dev", "keywords": ["프로그램", "VBA"]}], NOW).tasks[0]


def markup(name):
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def response(text, url=URL):
    return FetchResult(url, url, 200, "ok", text)


def changed(text, mutate):
    document = _Document(text)
    data = document.store()
    mutate(data)
    script = next(x for x in document.scripts if "window.__RELAY_STORE__" in x)
    return text.replace(script, "window.__RELAY_STORE__ = " + json.dumps(json.dumps(data, ensure_ascii=False), ensure_ascii=False) + ";")


def test_real_format_detail_fields_and_state():
    post = create().parse(PostRef(URL, "fixture00001"), response(markup("detail")), NOW)
    assert post.title == "쇼핑몰 주문 정리 프로그램 제작 의뢰"
    assert post.body.endswith("예산 60만원입니다.")
    assert (post.pay.min, post.pay.unit) == (600000, "project")
    assert post.published.at == datetime(2026, 9, 26, 7, 46, 26, 161000, tzinfo=UTC)
    assert post.source_status == "open"
    assert post.workplace_raw == "서울특별시 성동구"
    closed = changed(markup("detail"), lambda d: d["job"].update(closed=True))
    assert create().parse(PostRef(URL), response(closed), NOW).source_status == "closed"


@pytest.mark.parametrize("mutate", [
    lambda d: d["job"].update({"content(masking:true)": "다른 공고"}),
    lambda d: d["permalink"].update(publicId="other0000001"),
    lambda d: d.pop("job"),
])
def test_reject_unavailable_or_mismatched_post(mutate):
    with pytest.raises(ParseError):
        create().parse(PostRef(URL), response(changed(markup("detail"), mutate)), NOW)


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda d: d["job"].update(hidden=True), "hidden_or_deleted"),
    (lambda d: d["job"].update(deleted=True), "hidden_or_deleted"),
    (lambda d: d["user"].update(webCrawlNotAllowed=True), "collection_disallowed"),
    (lambda d: d["user"].pop("webCrawlNotAllowed"), "collection_permission_unknown"),
])
def test_non_collectable_post_is_skipped_without_storing_body(mutate, code):
    with pytest.raises(PostNotCollectable) as caught:
        create().parse(PostRef(URL), response(changed(markup("detail"), mutate)), NOW)
    assert caught.value.code == code


def test_no_script_execution_or_guessing_on_layout_change():
    with pytest.raises(ParseError):
        _Document('<script>window.__RELAY_STORE__ = alert("test");</script>').store()
    with pytest.raises(ParseError):
        create().parse(PostRef(URL), response(markup("detail").replace("whitespace-pre-wrap", "new-layout")), NOW)


def test_reviews_using_body_class_are_not_ingested():
    text = markup("detail").replace("</body>", '<p class="whitespace-pre-wrap">작성자 후기: 작업 잘했습니다</p></body>')
    post = create().parse(PostRef(URL), response(text), NOW)
    assert "작성자 후기" not in post.body
    assert post.body.startswith("주문 엑셀")


def test_closed_title_badge_requires_matching_closed_status():
    text = markup("detail").replace("<h1>", "<h1><span>마감<!-- --> </span>")
    with pytest.raises(ParseError):
        create().parse(PostRef(URL), response(text), NOW)
    text = changed(text, lambda d: d["job"].update(closed=True))
    post = create().parse(PostRef(URL), response(text), NOW)
    assert post.source_status == "closed"
    assert post.title == "쇼핑몰 주문 정리 프로그램 제작 의뢰"


class Cached:
    def __init__(self, text=JOBS):
        self.text = text
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return response(INDEX if url.endswith('/sitemap.xml') else self.text, url)


def test_all_public_sitemaps_and_dedup_without_id_keyword_matches():
    fetcher = Cached()
    found = create().discover(task(), fetcher)
    assert [r.url for r in found.refs] == [URL]
    assert found.next_cursor is None
    assert len(fetcher.urls) == 3  # index and both publicly listed job maps
    assert all('/api/' not in url and 'regionId' not in url for url in fetcher.urls)
    assert found.metadata['listed_urls'] == 2
    assert found.metadata['title_candidates'] == 1


def test_empty_sitemap_vs_missing_structure_and_invalid_urls():
    assert create().discover(task(), Cached(f'<urlset {NS}></urlset>')).refs == []
    with pytest.raises(ParseError):
        create().discover(task(), Cached("<h1>프로그램</h1>"))
    with pytest.raises(ParseError):
        create().discover(task(), Cached(JOBS.replace('https://jobs.daangn.com/job-posts/', 'https://other.example/')))


def test_default_permission_and_partial_coverage(tmp_path):
    ctx = make_ctx(tmp_path)
    c = client_for(ctx)
    src = next(x for x in c.get("/v1/sources").json()["items"] if x["id"] == "daangn-alba")
    assert src["research"]["ready"] is True
    assert src["policy"]["status"] == "permission_pending"
    assert c.post("/v1/runs", json={"kind": "discovery", "source_id": "daangn-alba"}).json()["error"]["code"] == "policy_not_allowed"
    plan = create().plan_discovery([{"id": "dev", "keywords": ["프로그램"]}], NOW)
    assert len(plan.tasks) == 1 and plan.depth_limit == 1
    assert plan.region_list_status == "not_applicable"
    assert plan.target_units_covered is None and plan.target_units_total is None
    assert plan.target_label == "전국 공개 공고 목록"
    assert any("누락" in n for n in plan.notes)
    ctx.db.dispose()


def test_adapter_through_collection_worker_and_dedup(tmp_path):
    adapter = create()
    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nDisallow: /api/\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=INDEX)
        if request.url.path.startswith("/sitemaps/"):
            return httpx.Response(200, text=JOBS)
        return httpx.Response(200, text=markup("detail"))
    ctx.transport = httpx.MockTransport(handler)
    c = client_for(ctx)
    assert c.patch("/v1/sources/daangn-alba", json={"policy": {"status": "restricted", "basis": "합성 표본 테스트"}}).status_code == 200
    c.patch("/v1/settings", json={"query_groups": [{"id": "dev", "label": "개발", "enabled": True, "keywords": ["프로그램"]}]})
    for _ in range(2):
        accepted = c.post("/v1/runs", json={"kind": "discovery", "source_id": "daangn-alba"}).json()
        Worker(ctx, "test", runs.COLLECTION_KINDS).run_once()
        run = c.get("/v1/runs/" + accepted["run_id"]).json()
        assert run["state"] == "succeeded", run
    leads = c.get("/v1/leads", params={"queue": "all"}).json()["items"]
    assert len(leads) == 1
    assert leads[0]["recommendation"] == "recommended"
    assert run["counts"]["created"] == 0 and run["counts"]["duplicates"] == 1
    assert run["result"]["discovery"]["listed_urls"] == 2
    source = next(row for row in c.get('/v1/sources').json()['items'] if row['id'] == 'daangn-alba')
    assert source['coverage']['unit_label'] == '공개 공고 목록'
    assert source['coverage']['next_up'] == '전국 공개 공고 목록 · 웹사이트·프로그램·자동화 관련 공고'
    ctx.db.dispose()


def test_unconfirmed_crawl_permission_is_reported_as_skip_not_parse_failure(tmp_path):
    adapter = create()
    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    second_url = URL.replace("fixture00001", "fixture00002")
    two_jobs = JOBS.replace("</urlset>", f"<url><loc>{second_url}</loc></url></urlset>")

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=INDEX)
        if request.url.path.startswith("/sitemaps/"):
            return httpx.Response(200, text=two_jobs)
        body = markup("detail")
        if str(request.url).endswith("fixture00002"):
            body = changed(body, lambda d: d["user"].pop("webCrawlNotAllowed"))
            body = body.replace("fixture00001", "fixture00002")
        return httpx.Response(200, text=body)

    ctx.transport = httpx.MockTransport(handler)
    c = client_for(ctx)
    assert c.patch("/v1/sources/daangn-alba", json={"policy": {"status": "restricted", "basis": "합성 표본 테스트"}}).status_code == 200
    c.patch("/v1/settings", json={"query_groups": [{"id": "dev", "label": "개발", "enabled": True, "keywords": ["프로그램"]}]})
    accepted = c.post("/v1/runs", json={"kind": "discovery", "source_id": "daangn-alba"}).json()
    Worker(ctx, "test", runs.COLLECTION_KINDS).run_once()
    run = c.get("/v1/runs/" + accepted["run_id"]).json()
    assert run["state"] == "succeeded"
    assert run["counts"]["skipped"] == 1
    assert run["counts"]["parse_failures"] == 0
    assert "본문을 저장하지 않았" in run["note"]
    assert len(c.get("/v1/leads", params={"queue": "all"}).json()["items"]) == 1
    ctx.db.dispose()


def test_sitemap_size_override_keeps_html_limit_and_robots(tmp_path):
    from worklead.engine.http import Fetcher
    ctx = make_ctx(tmp_path)
    def handler(request):
        return httpx.Response(200, text="User-agent: *\nAllow: /\n" if request.url.path == '/robots.txt' else 'x' * (3 * 1024 * 1024 + 1))
    fetcher = Fetcher(ctx.db, allowed_hosts={'jobs.daangn.com'}, user_agent='Worklead-test', daily_budget=10,
                      min_interval_s=0, transport=httpx.MockTransport(handler))
    assert fetcher.get(URL).outcome == 'too_large'
    assert fetcher.get('https://jobs.daangn.com/sitemap.xml', max_bytes=50 * 1024 * 1024).outcome == 'ok'
    with pytest.raises(ValueError):
        fetcher.get(URL, max_bytes=51 * 1024 * 1024)
    fetcher.close()
    ctx.db.dispose()


def test_scheduled_nationwide_scan_adds_new_post_without_duplicate(tmp_path, monkeypatch):
    from datetime import timedelta
    from worklead.engine.worker import schedule_due
    adapter = create()
    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    clock = [NOW]
    ctx.clock = lambda: clock[0]
    monkeypatch.setattr(runs, 'utcnow', ctx.clock)
    stage = [1]
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /\n')
        if request.url.path == '/sitemap.xml':
            return httpx.Response(200, text=INDEX)
        if request.url.path.startswith('/sitemaps/'):
            extra = f'<url><loc>{URL.replace("fixture00001", "fixture00002")}</loc></url>' if stage[0] == 2 else ''
            return httpx.Response(200, text=JOBS.replace('</urlset>', extra + '</urlset>'))
        content = markup('detail')
        if str(request.url).endswith('fixture00002'):
            content = content.replace('fixture00001', 'fixture00002')
        return httpx.Response(200, text=content)
    ctx.transport = httpx.MockTransport(handler)
    c = client_for(ctx)
    assert c.patch('/v1/sources/daangn-alba', json={'policy': {'status': 'restricted', 'basis': '합성 표본'}, 'auto_collect_enabled': True, 'interval_minutes': 180}).status_code == 200
    c.patch('/v1/settings', json={'query_groups': [{'id': 'dev', 'label': '개발', 'enabled': True, 'keywords': ['프로그램']}]})
    w = Worker(ctx, 'test', runs.COLLECTION_KINDS)
    assert len(schedule_due(ctx)) == 2  # Enabling already queued the first scan.
    while w.run_once():
        pass
    stage[0] = 2
    clock[0] += timedelta(hours=3)
    ids = schedule_due(ctx)
    assert len(ids) == 1
    assert w.run_once()
    run = c.get('/v1/runs/' + ids[0]).json()
    assert run['trigger'] == 'schedule' and run['state'] == 'succeeded'
    assert run['counts']['created'] == 1 and run['counts']['duplicates'] == 1
    assert len(c.get('/v1/leads', params={'queue': 'all'}).json()['items']) == 2
    assert schedule_due(ctx) == []
    ctx.db.dispose()
