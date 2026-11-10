from tests.exaone_fixtures import EX_CFG
from tests.test_auto_pairing import app  # noqa: F401
from tests.test_job_auto_apply import ACCOUNT, add  # noqa: F401
from tests.test_managed_auto import Browser, runner_for
from worklead.analysis.writer import application_draft, text_hash
from worklead.jobs.auto_apply import runtime as auto_runtime
from worklead.models import AiDraft, JobAutoAttempt, Lead, SourceRecord  # noqa: F401
from worklead.services.credentials import FakeCredentialStore
from worklead.services.settings import get_profile, put_setting


class TailoredBrowser(Browser):
    def __init__(self, profile=None):
        super().__init__(profile)
        self.filled = []

    def act(self, operation, expected=None):
        if operation == "fill":
            self.filled.append(expected["text"])
            self.profile_hash = text_hash(expected["text"])
            return {"state": "filled", "profile_hash": self.profile_hash}
        if operation == "open":
            self.profile_hash = "b" * 64  # the saved default intro
        return super().act(operation, expected)


def enable(app):
    app.credential_store = FakeCredentialStore()
    app.credential_store.set_key(app.config.data_dir, "exaone", "fixture-not-real")
    with app.db.session() as s:
        put_setting(s, "ai", EX_CFG)


def ready_text(app, lead_id, text):
    with app.db.session() as s:
        lead = s.get(Lead, lead_id)
        row = application_draft(s, lead, s.get(SourceRecord, lead.primary_record_id), get_profile(s))
        row.status, row.text = "ready", text
        return row.id


def test_pending_text_defers_without_attempt(app):
    enable(app)
    add(app)
    runner, browser = runner_for(app, TailoredBrowser())
    runner._step(browser)
    assert browser.opens == 0 and not auto_runtime(app).status().attempts
    with app.db.session() as s:
        assert s.query(AiDraft).filter_by(status="pending").count() == 1


def test_two_tailored_jobs_submit_without_profile_pause(app):
    enable(app)
    first, second = add(app), add(app, key="ABCDEFGHIJK2")
    ready_text(app, first, "첫 번째 맞춤 문구입니다.\n\n포트폴리오: https://www.seyj.kr/")
    ready_text(app, second, "두 번째 맞춤 문구입니다.\n\n포트폴리오: https://www.seyj.kr/")
    runner, browser = runner_for(app, TailoredBrowser())
    runner._step(browser)
    runner._step(browser)
    assert browser.submits == 2 and len(browser.filled) == 2
    assert auto_runtime(app).status().state != "paused"
    assert app.auto_pair_store.value["paused"] is None
    with app.db.session() as s:
        assert s.query(AiDraft).filter(AiDraft.used_at.is_not(None)).count() == 2


def test_failed_text_holds_the_job(app):
    enable(app)
    lead_id = add(app)
    draft_id = ready_text(app, lead_id, "x")
    with app.db.session() as s:
        s.get(AiDraft, draft_id).status = "failed"
    runner, browser = runner_for(app, TailoredBrowser())
    runner._step(browser)
    assert browser.opens == 0
    assert auto_runtime(app).status().attempts[0].reason_code == "ai_text_unavailable"


def test_fill_mismatch_never_submits(app):
    enable(app)
    ready_text(app, add(app), "맞춤 문구입니다.\n\n포트폴리오: https://www.seyj.kr/")

    class Wrong(TailoredBrowser):
        def act(self, operation, expected=None):
            result = super().act(operation, expected)
            if operation == "fill":
                self.profile_hash = "c" * 64
                return {"state": "filled", "profile_hash": "c" * 64}
            return result
    runner, browser = runner_for(app, Wrong())
    runner._step(browser)
    assert browser.submits == 0


def test_edited_post_does_not_reuse_old_text(app):
    enable(app)
    lead_id = add(app)
    ready_text(app, lead_id, "이전 문구입니다.\n\n포트폴리오: https://www.seyj.kr/")
    with app.db.session() as s:
        s.get(SourceRecord, s.get(Lead, lead_id).primary_record_id).content_hash = "edited"
    runner, browser = runner_for(app, TailoredBrowser())
    runner._step(browser)
    assert browser.submits == 0 and not browser.filled


def test_switch_turned_on_mid_attempt_refuses_permit_without_text(app):
    add(app)

    class LateSwitch(TailoredBrowser):
        def act(self, operation, expected=None):
            if operation == "open":
                enable(app)
            return super().act(operation, expected)
    runner, browser = runner_for(app, LateSwitch())
    runner._step(browser)
    assert browser.submits == 0 and not browser.filled
    assert auto_runtime(app).status().attempts[0].reason_code == "ai_text_unavailable"


def test_switch_turned_off_mid_attempt_refuses_permit(app):
    enable(app)
    ready_text(app, add(app), "맞춤 문구입니다.\n\n포트폴리오: https://www.seyj.kr/")

    class OffAfterFill(TailoredBrowser):
        def act(self, operation, expected=None):
            result = super().act(operation, expected)
            if operation == "fill":
                with app.db.session() as s:
                    put_setting(s, "ai", {**EX_CFG, "assist_application": False})
            return result
    runner, browser = runner_for(app, OffAfterFill())
    runner._step(browser)
    assert browser.filled and browser.submits == 0
    assert auto_runtime(app).status().attempts[0].reason_code == "ai_text_unavailable"


def test_pending_wake_never_takes_ai_control_lock_and_runs_after_transaction(app, monkeypatch):
    import threading
    from worklead.jobs.auto_apply_models import NextIn
    enable(app)
    add(app)
    bridge = auto_runtime(app)
    seen = []

    class Worker:
        def wake(self):
            free = []
            t = threading.Thread(target=lambda: free.append(app.ai_control_lock.acquire(blocking=False)
                                                            and (app.ai_control_lock.release() or True)))
            t.start(); t.join()
            seen.append((bridge._lock._is_owned(), free[0]))
    app._assist_worker = Worker()

    def no_lock(self):
        raise AssertionError("assist_worker property takes ai_control_lock")
    monkeypatch.setattr(type(app), "assist_worker", property(no_lock))
    # Managed runner path: it holds bridge._lock around next(); the wake must still avoid ai_control_lock.
    runner, browser = runner_for(app, TailoredBrowser())
    runner._step(browser)
    assert seen and all(free for _, free in seen) and browser.opens == 0
    # Direct bridge path: the wake runs after next() released bridge._lock.
    seen.clear()
    assert bridge.next(NextIn(account_hash=ACCOUNT))["job"] is None
    assert seen == [(False, True)]
