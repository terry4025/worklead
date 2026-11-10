from tests.exaone_fixtures import ex_ctx, exaone_json
from worklead.analysis.chat_draft import ChatDrafts, chat_step, with_draft
from worklead.jobs.chat_view import ChatView

CHAT = "고객: 홈페이지 리뉴얼 견적 문의드려요. 예산은 50만원 정도인데 가능할까요?"
REPLY = ("홈페이지 리뉴얼 견적을 문의해 주셔서 감사합니다. 말씀하신 50만원 안에서 가능한 범위를 같이 정리해 보겠습니다. "
         "지금 사이트에서 가장 불편한 점이 무엇인지, 꼭 바꾸고 싶은 화면이 어디인지 알려 주실 수 있을까요?")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_debounce_and_dedupe():
    clock = Clock()
    drafts = ChatDrafts(clock)
    drafts.submit(CHAT, None)
    assert drafts.take_due() is None and drafts.pending()
    clock.t = 3.5
    job = drafts.take_due()
    assert job is not None and job.text == CHAT
    drafts.finish(job.digest, "ready", "답장")
    drafts.submit(CHAT, None)
    assert not drafts.pending()


def test_step_creates_draft_shown_with_matching_view(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"reply": REPLY}))
    clock = Clock()
    ctx._chat_drafts = ChatDrafts(clock)
    ctx.chat_drafts.submit(CHAT, None)
    clock.t = 4
    chat_step(ctx)
    view = with_draft(ctx, ChatView(enabled=True, state="visible", text=CHAT))
    assert view.draft_state == "ready" and view.draft_text == REPLY
    other = with_draft(ctx, ChatView(enabled=True, state="visible", text="다른 대화"))
    assert other.draft_state == "off" and other.draft_text is None


def test_switch_off_shows_no_draft(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"reply": REPLY}), assist_chat=False)
    ctx.chat_drafts.submit(CHAT, None)
    view = with_draft(ctx, ChatView(enabled=True, state="visible", text=CHAT))
    assert view.draft_state == "off"


def test_nothing_is_written_to_the_database(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"reply": REPLY}))
    clock = Clock()
    ctx._chat_drafts = ChatDrafts(clock)
    ctx.chat_drafts.submit(CHAT, None)
    clock.t = 4
    chat_step(ctx)
    assert ctx.chat_drafts.view_for(CHAT)[1] == "ready"
    ctx.db.dispose()
    files = list(tmp_path.glob("worklead.sqlite3*"))
    assert files
    for path in files:
        raw = path.read_bytes()
        assert "견적 문의드려요".encode() not in raw and "가장 불편한 점".encode() not in raw


def test_unexpected_error_marks_failed_not_pending(tmp_path, monkeypatch):
    import pytest
    from worklead.analysis import chat_draft

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(chat_draft, "call_exaone", boom)
    ctx = ex_ctx(tmp_path)
    clock = Clock()
    ctx._chat_drafts = ChatDrafts(clock)
    ctx.chat_drafts.submit(CHAT, None)
    clock.t = 4
    with pytest.raises(RuntimeError):
        chat_step(ctx)
    assert ctx.chat_drafts.view_for(CHAT)[1] == "failed"


def test_switch_turned_on_later_drafts_the_open_chat(tmp_path):
    from worklead.services.settings import get_setting, put_setting
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"reply": REPLY}), assist_chat=False)
    clock = Clock()
    ctx._chat_drafts = ChatDrafts(clock)
    ctx.chat_drafts.submit(CHAT, None)
    clock.t = 4
    chat_step(ctx)
    assert ctx.chat_drafts.view_for(CHAT)[1] == "off"
    with ctx.db.session() as s:
        put_setting(s, "ai", {**get_setting(s, "ai"), "assist_chat": True})
    ctx.chat_drafts.submit(CHAT, None)
    assert ctx.chat_drafts.pending()
    clock.t = 8
    chat_step(ctx)
    assert ctx.chat_drafts.view_for(CHAT)[1:] == ("ready", REPLY)


def test_failed_draft_is_not_resubmitted_for_the_same_text():
    clock = Clock()
    drafts = ChatDrafts(clock)
    drafts.submit(CHAT, None)
    clock.t = 4
    drafts.finish(drafts.take_due().digest, "failed", None)
    drafts.submit(CHAT, None)
    assert not drafts.pending()
