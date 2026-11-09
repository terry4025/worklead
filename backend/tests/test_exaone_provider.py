import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from worklead.analysis.exaone_provider import MODEL, ExaoneClient
from worklead.analysis.provider import AIFailure


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    note: str | None


def reply(content, status=200, headers=None, finish="stop"):
    body = {"choices": [{"finish_reason": finish, "message": {"content": content, "reasoning_content": "hidden"}}],
            "model": MODEL, "usage": {"prompt_tokens": 10, "completion_tokens": 5, "reasoning_tokens": 3}}
    return httpx.Response(status, json=body if status == 200 else {"error": "x"}, headers=headers or {})


class Clock:
    def __init__(self):
        self.t, self.slept = 100.0, []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(round(seconds, 2))
        self.t += seconds


def make(handler):
    clock = Clock()
    return ExaoneClient(transport=httpx.MockTransport(handler), clock=clock.now, sleep=clock.sleep), clock


def test_request_shape_and_parsed_result():
    seen = {}

    def handler(request):
        seen["auth"], seen["body"] = request.headers["authorization"], json.loads(request.content)
        return reply('\n\n{"ok": true, "note": null}')
    client, _ = make(handler)
    out, usage = client.complete("fixture-key", "sys", "user", Probe)
    assert out == Probe(ok=True, note=None) and usage == (10, 5, 0)
    body = seen["body"]
    assert seen["auth"] == "Bearer fixture-key" and body["model"] == "k-exaone_v2.5" and body["reasoning_effort"] == "low"
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["schema"]["required"] == ["ok", "note"]
    assert schema["schema"]["additionalProperties"] is False


def test_fenced_json_is_accepted():
    client, _ = make(lambda request: reply('```json\n{"ok": false, "note": "x"}\n```'))
    assert client.complete("k", "s", "u", Probe)[0].ok is False


def test_rate_limit_waits_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return reply("", 429, {"retry-after-ms": "630"}) if len(calls) == 1 else reply('{"ok": true, "note": null}')
    client, clock = make(handler)
    assert client.complete("k", "s", "u", Probe)[0].ok
    assert 0.63 in clock.slept and len(calls) == 2


def test_calls_are_spaced_at_least_1_1_seconds():
    client, clock = make(lambda request: reply('{"ok": true, "note": null}'))
    client.complete("k", "s", "u", Probe)
    client.complete("k", "s", "u", Probe)
    assert clock.slept[-1] == pytest.approx(1.1)


@pytest.mark.parametrize("status,code", [(401, "authentication_failed"), (403, "permission_denied"), (500, "provider_http_error")])
def test_http_errors_map_to_codes(status, code):
    client, _ = make(lambda request: reply("", status))
    with pytest.raises(AIFailure) as exc:
        client.complete("k", "s", "u", Probe)
    assert exc.value.code == code


def test_truncated_or_invalid_output_is_rejected():
    client, _ = make(lambda request: reply('{"ok": true, "note": null}', finish="length"))
    with pytest.raises(AIFailure) as exc:
        client.complete("k", "s", "u", Probe)
    assert exc.value.code == "response_incomplete"
    client, _ = make(lambda request: reply('{"note": null}'))
    with pytest.raises(AIFailure) as exc:
        client.complete("k", "s", "u", Probe)
    assert exc.value.code == "schema_error"
