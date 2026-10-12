from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from worklead.services.credentials import (
    MAX_KEY_BYTES, CredentialError, FakeCredentialStore, WindowsCredentialStore,
    _CREDENTIALW, _WinCredentialAPI, _target, delete_key, get_key, set_key, status,
)


KEY = "synthetic-fixture-private-token"


def test_fake_scope_isolation_replace_delete_and_status_without_secret_metadata(tmp_path):
    store = FakeCredentialStore()
    first, second = tmp_path / "first", tmp_path / "second"
    assert asdict(status(first, "openai", store=store)) == {"supported": True, "configured": False, "error_code": None}
    set_key(first, "openai", KEY, store=store)
    old = store._blobs[_target(first, "openai")]
    assert get_key(first, "openai", store=store) == KEY
    assert get_key(second, "openai", store=store) is None
    safe = asdict(status(first, "openai", store=store))
    assert safe == {"supported": True, "configured": True, "error_code": None}
    assert KEY not in repr(store) + repr(safe)
    set_key(first, "openai", "replacement-fixture", store=store)
    assert set(old) == {0}
    replacement = store._blobs[_target(first, "openai")]
    assert delete_key(first, "openai", store=store)
    assert set(replacement) == {0}
    assert not delete_key(first, "openai", store=store)
    assert get_key(first, "openai", store=store) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", ["", " ", "key with-space", "line\nnext", "key\x00", "한글", "x" * (MAX_KEY_BYTES + 1), None, 10])
def test_invalid_key_never_mutates_existing_key_or_discloses_input(tmp_path, key):
    store = FakeCredentialStore()
    store.set_key(tmp_path, "openai", KEY)
    with pytest.raises(CredentialError) as caught:
        store.set_key(tmp_path, "openai", key)
    assert str(caught.value) == "credential_invalid_key"
    assert caught.value.__cause__ is None
    assert store.get_key(tmp_path, "openai") == KEY


def test_max_blob_size_roundtrips_and_target_never_contains_paths(tmp_path):
    store = FakeCredentialStore()
    key = "z" * MAX_KEY_BYTES
    store.set_key(tmp_path, "openai", key)
    assert store.get_key(tmp_path, "openai") == key
    target = _target(tmp_path / "private-folder", "openai")
    assert target.startswith("Worklead/ai/v1/")
    assert len(target.removeprefix("Worklead/ai/v1/")) == 64
    assert "private-folder" not in target and str(tmp_path) not in target
    assert _target(tmp_path / "private-folder" / ".." / "private-folder", "openai") == target


@pytest.mark.parametrize("provider", ["OPENAI", "anthropic", "openai/else", "*", None])
def test_arbitrary_provider_cannot_select_an_os_target(tmp_path, provider):
    store = FakeCredentialStore()
    with pytest.raises(CredentialError, match="^credential_invalid_provider$"):
        store.get_key(tmp_path, provider)
    assert not store._blobs


@pytest.mark.parametrize("scope", ["Worklead/ai/v1/anything", None])
def test_scope_must_be_trusted_absolute_path(scope):
    with pytest.raises(CredentialError, match="^credential_invalid_scope$"):
        FakeCredentialStore().get_key(scope, "openai")


def test_relative_scope_is_rejected():
    from pathlib import Path
    with pytest.raises(CredentialError, match="^credential_invalid_scope$"):
        FakeCredentialStore().get_key(Path("relative"), "openai")


@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_store_failure_is_not_missing_and_preserves_existing_key(tmp_path, operation):
    store = FakeCredentialStore()
    store.set_key(tmp_path, "openai", KEY)
    store.set_failure(operation, KEY)
    with pytest.raises(CredentialError) as caught:
        if operation == "read":
            store.get_key(tmp_path, "openai")
        elif operation == "write":
            store.set_key(tmp_path, "openai", "replacement")
        else:
            store.delete_key(tmp_path, "openai")
    assert str(caught.value) == "credential_unavailable"
    if operation == "read":
        assert asdict(store.status(tmp_path, "openai")) == {"supported": True, "configured": None, "error_code": "credential_unavailable"}
    store.clear_failures()
    assert store.get_key(tmp_path, "openai") == KEY


def test_non_windows_is_unsupported_without_fallback_or_files(tmp_path):
    store = WindowsCredentialStore(_platform="linux")
    assert asdict(store.status(tmp_path, "openai")) == {"supported": False, "configured": None, "error_code": "credential_unsupported"}
    for action in (lambda: store.get_key(tmp_path, "openai"), lambda: store.set_key(tmp_path, "openai", KEY), lambda: store.delete_key(tmp_path, "openai")):
        with pytest.raises(CredentialError, match="^credential_unsupported$"):
            action()
    assert list(tmp_path.iterdir()) == []


class Function:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


class FakeWinDLL:
    """Exercises exact ABI pointer transfer and observable buffer cleanup."""
    def __init__(self):
        self.blobs = {}
        self.error = 1168
        self.read_error = None
        self.write_error = False
        self.delete_error = None
        self.raise_text = None
        self.read_buffer = None
        self.write_pointer = None
        self.write_size = 0
        self.write_credential = None
        self.freed = 0
        self.corrupt = None
        self.CredReadW = Function(self.read)
        self.CredWriteW = Function(self.write)
        self.CredDeleteW = Function(self.delete)
        self.CredFree = Function(self.free)

    def read(self, target, kind, flags, out):
        assert kind == 1 and flags == 0
        if self.raise_text:
            raise OSError(self.raise_text)
        if self.read_error is not None or target not in self.blobs:
            self.error = self.read_error or 1168
            return 0
        blob = self.blobs[target]
        self.read_buffer = (ctypes.c_ubyte * len(blob))(*blob)
        self.read_credential = _CREDENTIALW()
        self.read_credential.Type = 1
        self.read_credential.TargetName = target
        self.read_credential.CredentialBlobSize = len(blob)
        self.read_credential.CredentialBlob = self.read_buffer
        if self.corrupt == "type":
            self.read_credential.Type = 2
        if self.corrupt == "target":
            self.read_credential.TargetName = "unrelated-target"
        if self.corrupt == "pointer":
            self.read_credential.CredentialBlob = None
        ctypes.cast(out, ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)))[0] = ctypes.pointer(self.read_credential)
        return 1

    def write(self, ptr, flags):
        assert flags == 0
        cred = ctypes.cast(ptr, ctypes.POINTER(_CREDENTIALW)).contents
        assert cred.Type == 1 and cred.Persist == 2
        assert cred.Flags == 0 and cred.AttributeCount == 0 and not cred.Attributes
        assert cred.UserName == "Worklead AI" and cred.Comment is None
        self.write_pointer, self.write_size = cred.CredentialBlob, cred.CredentialBlobSize
        self.write_credential = ptr._obj  # Retain the structure, never a freed raw pointer.
        if self.raise_text:
            raise OSError(self.raise_text)
        if self.write_error:
            return 0
        self.blobs[cred.TargetName] = bytes(cred.CredentialBlob[:cred.CredentialBlobSize])
        return 1

    def delete(self, target, kind, flags):
        assert kind == 1 and flags == 0
        if self.raise_text:
            raise OSError(self.raise_text)
        if self.delete_error is not None:
            self.error = self.delete_error
            return 0
        if target not in self.blobs:
            self.error = 1168
            return 0
        self.blobs.pop(target)
        return 1

    def free(self, ptr):
        self.freed += 1
        if self.corrupt != "pointer":
            assert not any(self.read_buffer)


def make_native():
    dll = FakeWinDLL()
    api = _WinCredentialAPI(_dll=dll, _last_error=lambda: dll.error)
    return dll, api, WindowsCredentialStore(_api=api, _platform="win32")


def test_native_read_write_delete_abi_and_owned_buffers_are_erased(tmp_path):
    dll, api, store = make_native()
    blob = bytearray(KEY, "ascii")
    api.write(_target(tmp_path, "openai"), blob)
    assert not any(blob)
    assert not any(ctypes.string_at(ctypes.byref(dll.write_credential), ctypes.sizeof(_CREDENTIALW)))
    assert store.get_key(tmp_path, "openai") == KEY
    assert dll.freed == 1 and not any(dll.read_buffer)
    assert store.status(tmp_path, "openai").configured is True
    assert dll.freed == 2 and not any(dll.read_buffer)
    assert store.delete_key(tmp_path, "openai")
    assert store.get_key(tmp_path, "openai") is None
    assert not store.delete_key(tmp_path, "openai")


@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_native_os_error_is_fixed_and_no_secret_exception_context(tmp_path, operation):
    dll, _, store = make_native()
    store.set_key(tmp_path, "openai", KEY)
    dll.raise_text = KEY
    with pytest.raises(CredentialError) as caught:
        {"read": lambda: store.get_key(tmp_path, "openai"), "write": lambda: store.set_key(tmp_path, "openai", KEY), "delete": lambda: store.delete_key(tmp_path, "openai")}[operation]()
    assert str(caught.value) == "credential_unavailable" and caught.value.__suppress_context__
    assert KEY not in repr(caught.value)
    assert store.status(tmp_path, "openai").configured is None
    dll.raise_text = None
    assert store.get_key(tmp_path, "openai") == KEY


def test_native_access_denied_does_not_report_missing_or_destroy_key(tmp_path):
    dll, _, store = make_native()
    store.set_key(tmp_path, "openai", KEY)
    dll.read_error = 5
    with pytest.raises(CredentialError, match="^credential_unavailable$"):
        store.get_key(tmp_path, "openai")
    assert store.status(tmp_path, "openai").configured is None
    dll.delete_error = 5
    with pytest.raises(CredentialError, match="^credential_unavailable$"):
        store.delete_key(tmp_path, "openai")
    dll.read_error = dll.delete_error = None
    assert store.get_key(tmp_path, "openai") == KEY


def test_native_failed_write_erases_input_buffer_and_preserves_key(tmp_path):
    dll, api, store = make_native()
    store.set_key(tmp_path, "openai", KEY)
    dll.write_error = True
    blob = bytearray("replacement", "ascii")
    with pytest.raises(CredentialError, match="^credential_unavailable$"):
        api.write(_target(tmp_path, "openai"), blob)
    assert not any(blob)
    assert not any(ctypes.string_at(ctypes.byref(dll.write_credential), ctypes.sizeof(_CREDENTIALW)))
    assert store.get_key(tmp_path, "openai") == KEY


@pytest.mark.parametrize("corrupt", ["type", "target", "pointer", "contents"])
def test_corrupt_read_is_freed_and_not_missing_or_key_metadata(tmp_path, corrupt):
    dll, _, store = make_native()
    store.set_key(tmp_path, "openai", KEY)
    if corrupt == "contents":
        dll.blobs[_target(tmp_path, "openai")] = b"invalid\x00blob"
    else:
        dll.corrupt = corrupt
    with pytest.raises(CredentialError, match="^credential_corrupt$"):
        store.get_key(tmp_path, "openai")
    assert dll.freed == 1
    assert store.status(tmp_path, "openai").configured is None
    assert dll.freed == 2


def test_one_scope_is_serialized_across_instances_and_distinct_scope_remains_independent(tmp_path):
    class SlowAPI:
        def __init__(self):
            self.active, self.maximum = 0, 0
            self.lock = threading.Lock()
            self.last_buffer = None

        def read(self, target):
            with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            time.sleep(.015)
            with self.lock:
                self.active -= 1
            self.last_buffer = bytearray(KEY, "ascii")
            return self.last_buffer

    api = SlowAPI()
    stores = [WindowsCredentialStore(_api=api, _platform="win32") for _ in range(2)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda i: stores[i % 2].get_key(tmp_path, "openai"), range(12)))
    assert all(result == KEY for result in results)
    assert api.maximum == 1 and not any(api.last_buffer)
    assert _target(tmp_path / "other", "openai") != _target(tmp_path, "openai")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("WORKLEAD_TEST_CREDENTIAL_OS") != "1", reason="opt-in owned Windows credential probe")
def test_windows_os_roundtrip_on_owned_uuid_scope_only(tmp_path):
    scope = tmp_path / ("credential-probe-" + str(uuid.uuid4()))
    scope.mkdir()
    store = WindowsCredentialStore()
    assert store.get_key(scope, "openai") is None
    assert store.status(scope, "openai").configured is False
    key = "synthetic-credential-fixture-" + uuid.uuid4().hex
    replacement = "synthetic-replacement-fixture-" + uuid.uuid4().hex
    try:
        store.set_key(scope, "openai", key)
        assert store.get_key(scope, "openai") == key
        assert store.status(scope, "openai").configured is True
        store.set_key(scope, "openai", replacement)
        assert store.get_key(scope, "openai") == replacement
        assert list(scope.iterdir()) == []
    finally:
        store.delete_key(scope, "openai")
    assert store.get_key(scope, "openai") is None
    assert store.status(scope, "openai").configured is False
    assert not store.delete_key(scope, "openai")
