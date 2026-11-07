"""Local enrollment, stop persistence and browser-free migration boundaries."""
import pytest

from tests.conftest import client_for
from tests.test_auto_pairing import MemoryStore
from tests.test_job_auto_apply import ACCOUNT, NOW
from worklead.jobs.applications import ApplicationError
from worklead.jobs.auto_apply import runtime as auto_runtime
from worklead.jobs.auto_apply_models import AutoApplyStartIn
from worklead.jobs.auto_pairing import PairingRuntime
from worklead.jobs.managed_setup import SETTING, boot, configuration, current_status, setup
from worklead.models import AppSetting, SourcePolicy


class FakeManaged:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.stage = "stopped"

    def start(self):
        self.started += 1
        self.stage = "login_required"
        return self.status()

    def shutdown(self, *, user_stop=False):
        self.stopped += 1
        self.stage = "stopped"
        return self.status()

    def status(self):
        return {"stage": self.stage, "browser_open": self.stage != "stopped",
            "needs_user_action": self.stage == "login_required", "error_code": None,
            "message": "전용 창에서 로그인하세요"}


@pytest.fixture
def app(ctx):
    ctx.clock = lambda: NOW
    ctx.auto_pair_store = MemoryStore()
    with ctx.db.session() as session:
        session.get(SourcePolicy, "daangn-alba").status = "allowed"
    ctx._jobs_auto_pairing_runtime = PairingRuntime(ctx, port=0)
    ctx._jobs_managed_auto_runtime = FakeManaged()
    yield ctx
    auto_runtime(ctx).shutdown()
    ctx._jobs_auto_pairing_runtime.close()


def body():
    return AutoApplyStartIn(confirm=True, min_score=50, daily_limit=20)


def test_setup_enrolls_browser_without_extension_files_or_secret_response(app):
    client = client_for(app)
    response = client.post("/v1/jobs/auto-apply/setup", json=body().model_dump())
    assert response.status_code == 200
    result = response.json()
    assert result["provider"] == "managed_edge"
    assert result["setup_stage"] == "login_required"
    assert result["enabled"] and result["remembers_connection"]
    assert result["needs_user_action"] and result["browser_open"]
    assert configuration(app)["enabled"]
    assert app._jobs_managed_auto_runtime.started == 1
    assert app._jobs_auto_pairing_runtime._server is None
    assert not (app.config.data_dir / "chrome-message-extension").exists()
    assert app.auto_pair_store.value["token"] not in response.text
    assert "connection" not in result


def test_stop_persists_and_does_not_restart_on_boot(app):
    setup(app, body())
    client = client_for(app)
    assert client.post("/v1/jobs/auto-apply/stop").status_code == 200
    assert configuration(app)["enabled"] is False
    assert app.auto_pair_store.value["desired"] is False
    assert not boot(app)
    assert app._jobs_managed_auto_runtime.started == 1
    assert not current_status(app).enabled
    # Even an inconsistent old vault must not override the persisted user stop.
    app._jobs_auto_pairing_runtime._write({**app.auto_pair_store.value, "desired": True})
    assert not boot(app)


def test_unpaired_already_enabled_install_migrates_and_boots_without_code(app):
    app._jobs_auto_pairing_runtime.start(body(), managed=True)
    auto_runtime(app).shutdown()
    assert configuration(app) is None
    assert boot(app)
    assert configuration(app)["enabled"] is True
    assert app._jobs_managed_auto_runtime.started == 1
    assert app._jobs_auto_pairing_runtime._server is None


def test_new_install_and_paused_grant_are_never_opted_in(app):
    assert not boot(app)
    assert configuration(app) is None
    assert app._jobs_managed_auto_runtime.started == 0
    app._jobs_auto_pairing_runtime.start(body(), managed=True)
    app._jobs_auto_pairing_runtime.disable("account_changed")
    auto_runtime(app).shutdown()
    assert not boot(app)
    assert configuration(app) is None


def test_managed_account_binding_persists_and_rejects_account_changes(app):
    setup(app, body())
    pair = app._jobs_auto_pairing_runtime
    pair.bind_account(ACCOUNT)
    assert app.auto_pair_store.value["account"] == ACCOUNT
    assert auto_runtime(app).status().paired
    with pytest.raises(ApplicationError) as caught:
        pair.bind_account("c" * 64)
    assert caught.value.code == "account_changed"
    assert app.auto_pair_store.value["paused"] == "account_changed"
    assert app.auto_pair_store.value["account"] == ACCOUNT


def test_setup_requires_confirmation_and_demo_never_enrolls(app):
    client = client_for(app)
    assert client.post("/v1/jobs/auto-apply/setup", json={"confirm": False}).status_code == 422
    app.config.demo = True
    with pytest.raises(ApplicationError) as caught:
        setup(app, body())
    assert caught.value.code == "demo_only"
    assert app.auto_pair_store.value is None


def test_restored_database_registration_cannot_recreate_revoked_vault(app):
    with app.db.session() as session:
        session.add(AppSetting(key=SETTING, value={"enabled": True}))
    assert not boot(app)
    assert app.auto_pair_store.value is None
    assert app._jobs_managed_auto_runtime.started == 0
