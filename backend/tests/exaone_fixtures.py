"""Shared EXAONE test setup: fake key, fake transport, no real sleeps."""
import json

import httpx

from tests.conftest import make_ctx
from tests.test_source_fields import NOW
from worklead.analysis.exaone_provider import MODEL, ExaoneClient
from worklead.services.credentials import FakeCredentialStore
from worklead.services.settings import put_setting

EX_CFG = {"enabled": True, "external_transfer_consent": True, "provider": "exaone", "model": MODEL, "daily_call_cap": 300,
          "assist_review": True, "assist_recheck": True, "assist_application": True, "assist_chat": True, "assist_quote": True}


def exaone_json(obj, status=200):
    return httpx.Response(status, json={"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(obj, ensure_ascii=False)}}], "model": MODEL,
        "usage": {"prompt_tokens": 50, "completion_tokens": 20}})


def ex_ctx(path, handler=None, **cfg):
    ctx = make_ctx(path)
    ctx.clock = lambda: NOW
    ctx.credential_store = FakeCredentialStore()
    ctx.credential_store.set_key(ctx.config.data_dir, "exaone", "fixture-not-real")
    ctx._exaone_client = ExaoneClient(transport=httpx.MockTransport(handler or (lambda r: exaone_json({}))),
                                      sleep=lambda seconds: None)
    with ctx.db.session() as s:
        put_setting(s, "ai", {**EX_CFG, **cfg})
    return ctx
