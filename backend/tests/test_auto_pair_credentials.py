"""Dedicated target, sealed grant format, and opt-in owned Windows roundtrip."""
import json
import os
import secrets
import sys
import uuid

import pytest

from worklead.services.auto_pair_credentials import AutoPairCredentialStore
from worklead.services.credentials import CredentialError


def grant():
    return {"version": 1, "token": secrets.token_urlsafe(32), "desired": True, "account": "a"*64,
            "profile": None, "min_score": 50, "daily_limit": 20, "paused": None}


class Api:
    def __init__(self): self.values = {}; self.buffers = []
    def read(self, target):
        if target not in self.values: return None
        blob = bytearray(self.values[target]); self.buffers.append(blob); return blob
    def write(self, target, blob): self.values[target] = bytes(blob); self.buffers.append(blob)
    def delete(self, target): return self.values.pop(target, None) is not None


def test_dedicated_scope_roundtrip_and_owned_buffers_wiped(tmp_path):
    api = Api(); store = AutoPairCredentialStore(api=api); value = grant()
    assert store.read(tmp_path) is None
    store.write(tmp_path, value)
    assert store.read(tmp_path) == value
    assert all(not any(buf) for buf in api.buffers)
    assert store._target(tmp_path).startswith("Worklead/jobs/pair/v1/")
    assert store.read(tmp_path / "different") is None
    assert list(tmp_path.iterdir()) == []
    assert store.delete(tmp_path) and not store.delete(tmp_path)


@pytest.mark.parametrize("changes", [{"token": "short"}, {"desired": 1}, {"account": "bad"}, {"profile": "b"},
    {"min_score": True}, {"daily_limit": 0}, {"paused": "unknown"}, {"version": 2}, {"extra": True}])
def test_corrupt_grants_rejected_with_fixed_error_only(tmp_path, changes):
    api = Api(); store = AutoPairCredentialStore(api=api)
    value = {**grant(), **changes}
    with pytest.raises(CredentialError, match="^credential_corrupt$"): store.write(tmp_path, value)
    api.values[store._target(tmp_path)] = json.dumps(value).encode()
    with pytest.raises(CredentialError, match="^credential_corrupt$"): store.read(tmp_path)
    assert all(not any(buf) for buf in api.buffers)


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("WORKLEAD_TEST_CREDENTIAL_OS") != "1", reason="opt-in owned Windows pairing credential probe")
def test_windows_vault_owned_uuid_scope_roundtrip(tmp_path):
    scope = tmp_path / ("auto-pair-probe-" + uuid.uuid4().hex)
    scope.mkdir()
    store = AutoPairCredentialStore(); value = grant()
    assert store.read(scope) is None
    try:
        store.write(scope, value)
        assert store.read(scope) == value
        store.write(scope, {**value, "desired": False})
        assert store.read(scope)["desired"] is False
        assert list(scope.iterdir()) == []
    finally:
        store.delete(scope)
    assert store.read(scope) is None
