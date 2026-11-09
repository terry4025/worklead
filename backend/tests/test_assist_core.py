from datetime import timedelta

import pytest
from pydantic import BaseModel, ConfigDict

from tests.exaone_fixtures import ex_ctx, exaone_json
from tests.test_source_fields import NOW
from worklead.analysis.assist import AssistWorker, call_exaone, calls_today, mask_for_ai, take_call
from worklead.analysis.provider import AIFailure
from worklead.services.settings import put_setting


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool


def test_mask_hides_accounts_and_phones_but_keeps_dates():
    text = mask_for_ai("국민 110-123-456789 로 입금, 010-1234-5678, 마감 2026-11-09")
    assert "[계좌번호]" in text and "[전화번호]" in text and "2026-11-09" in text


def test_daily_cap_resets_on_the_seoul_day(tmp_path):
    ctx = ex_ctx(tmp_path)
    with ctx.db.session() as s:
        assert take_call(s, 2, NOW) and take_call(s, 2, NOW) and not take_call(s, 2, NOW)
        assert calls_today(s, NOW) == 2
        assert calls_today(s, NOW + timedelta(days=1)) == 0


def test_call_succeeds_and_counts(tmp_path):
    ctx = ex_ctx(tmp_path, lambda request: exaone_json({"ok": True}))
    out, usage = call_exaone(ctx, "s", "u", Probe, feature="assist_chat")
    assert out.ok and usage[0] == 50
    with ctx.db.session() as s:
        assert calls_today(s, NOW) == 1


@pytest.mark.parametrize("change,code", [
    ({"enabled": False}, "disabled"), ({"assist_chat": False}, "disabled"),
    ({"provider": "openai"}, "disabled"), ({"daily_call_cap": 1}, "call_cap_reached")])
def test_gates_block_before_http(tmp_path, change, code):
    sent = []
    ctx = ex_ctx(tmp_path, lambda request: sent.append(1) or exaone_json({"ok": True}), **change)
    if code == "call_cap_reached":
        with ctx.db.session() as s:
            take_call(s, 1, NOW)
    with pytest.raises(AIFailure) as exc:
        call_exaone(ctx, "s", "u", Probe, feature="assist_chat")
    assert exc.value.code == code and not sent


def test_missing_key_is_not_configured(tmp_path):
    ctx = ex_ctx(tmp_path)
    ctx.credential_store.delete_key(ctx.config.data_dir, "exaone")
    with pytest.raises(AIFailure) as exc:
        call_exaone(ctx, "s", "u", Probe)
    assert exc.value.code == "not_configured"


def test_worker_runs_steps_and_isolates_failures(tmp_path):
    ctx = ex_ctx(tmp_path)
    seen = []
    worker = AssistWorker(ctx)
    worker.steps = [lambda app: (_ for _ in ()).throw(RuntimeError("boom")), lambda app: seen.append("ran")]
    worker.run_once()
    assert seen == ["ran"]
