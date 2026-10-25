"""Production content script in headless system Edge against intercepted fixtures.

No Chrome profile, real account, extension installation or real site response is
used. Every page request is fulfilled from synthetic HTML or aborted locally.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from worklead.cafe.browser import EdgeBrowser


URL = "https://jobs.daangn.com/job-posts/synthetic-123456789abc"
TITLE = "합성 브라우저 웹 개발 의뢰"
INTRO = "테스트에서만 사용하는 합성 기본 자기소개"


def html(mode):
    panel = ("<div role='dialog'><div id='intro-card'><h2>제출할 자기소개를 확인하세요</h2>"
        f"<button aria-selected='true'>기본</button><button>추가</button><textarea>{INTRO}</textarea>"
        "<button id='final'>지원하기</button></div>"
        + ("<label>근무 가능 요일<input required></label>" if mode == "questions" else "") + "</div>")
    return ("<!doctype html><meta charset='utf-8'><header><button aria-label='합성 계정 프로필 메뉴'>계정</button></header>"
        f"<main><h1>{TITLE}</h1><section id='region'>"
        + ("<button>지원 취소</button>" if mode == "already" else "<button id='open'>지원하기</button>")
        + "</section></main><script>window.fixture={opens:0,submits:0};"
        "const region=document.querySelector('#region');const open=document.querySelector('#open');"
        "if(open)open.onclick=()=>{fixture.opens++;setTimeout(()=>{region.innerHTML=" + json.dumps(panel) + ";"
        "document.querySelector('#final').onclick=()=>{fixture.submits++;setTimeout(()=>{region.innerHTML='<button>지원 취소</button>'},20)};"
        "},20)};</script>")


def test_auto_apply_content_real_system_edge_with_intercepted_pages(tmp_path):
    script = (Path(__file__).resolve().parents[2] / "extensions/worklead-messages/auto-content.js").read_text(encoding="utf-8")
    browser = EdgeBrowser(tmp_path / "synthetic-auto-apply-profile")
    intercepted, denied = [], []
    mode = ["normal"]
    try:
        browser.open()

        def local(route):
            intercepted.append(route.request.url)
            if route.request.url != URL or route.request.method != "GET":
                denied.append(route.request.url)
                route.abort()
                return
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html(mode[0]))

        browser.context.route("**/*", local)
        browser.context.add_init_script("globalThis.chrome={runtime:{sendMessage:()=>Promise.resolve()}}")
        page = browser.page

        def reset(value="normal"):
            mode[0] = value
            page.goto(URL, wait_until="domcontentloaded")
            page.add_script_tag(content=script)

        def run(operation, expected=None):
            return page.evaluate("([operation,expected])=>globalThis.__workleadAutoUi(operation,expected)", [operation, expected])

        def identity():
            account = run("inspect")
            assert account["state"] == "account"
            assert len(account["account_hash"]) == 64
            return {"job_id": "123456789abc", "title": TITLE, "account_hash": account["account_hash"]}

        def opened(expected):
            assert run("open", expected)["state"] == "opened"
            page.get_by_text("제출할 자기소개를 확인하세요", exact=True).wait_for()

        reset()
        expected = identity()
        assert run("inspect", expected)["state"] == "openable"
        assert page.evaluate("fixture") == {"opens": 0, "submits": 0}
        opened(expected)
        ready = run("inspect", expected)
        assert ready["state"] == "ready" and len(ready["profile_hash"]) == 64
        assert INTRO not in json.dumps(ready) and "합성 계정" not in json.dumps(ready)
        expired = {**expected, "profile_hash": ready["profile_hash"], "attempt_id": "expired", "permit_until": 0}
        assert run("submit", expired)["state"] == "held"
        assert page.evaluate("fixture.submits") == 0
        submit = {**expired, "attempt_id": "browser-test-once", "permit_until": page.evaluate("Date.now()+2000")}
        assert run("submit", submit)["state"] == "clicked"
        page.get_by_role("button", name="지원 취소", exact=True).wait_for()
        assert run("inspect", expected)["state"] == "already_applied"
        assert run("submit", submit)["state"] != "clicked"
        assert page.evaluate("fixture") == {"opens": 1, "submits": 1}

        reset("questions")
        expected = identity()
        opened(expected)
        held = run("inspect", expected)
        assert held["state"] == "held" and held["reason_code"] == "questions_required"
        assert page.evaluate("fixture") == {"opens": 1, "submits": 0}

        reset()
        expected = identity()
        wrong_job = {**expected, "job_id": "aaaaaaaaaaaa"}
        assert run("open", wrong_job)["reason_code"] == "navigation_failed"
        page.locator("header button").evaluate("el=>el.setAttribute('aria-label','다른 합성 계정 프로필 메뉴')")
        assert run("open", expected)["reason_code"] == "account_changed"
        assert page.evaluate("fixture") == {"opens": 0, "submits": 0}

        reset("already")
        expected = identity()
        assert run("inspect", expected)["state"] == "already_applied"
        assert page.evaluate("fixture") == {"opens": 0, "submits": 0}
        assert len(intercepted) == 4 and denied == []
        assert {urlsplit(url).netloc for url in intercepted} == {"jobs.daangn.com"}
        # No route.continue_() or upstream request exists in this fixture handler.
        # These URLs describe intercepted synthetic pages, not production access.
    finally:
        browser.close()
