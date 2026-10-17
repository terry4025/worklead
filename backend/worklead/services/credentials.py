"""Scoped AI keys in the current Windows user's Credential Manager.

No credential enumeration, arbitrary target, database/file fallback or secret
metadata is exposed. Only ERROR_NOT_FOUND means an absent key. Python strings
received from a request or returned to a provider cannot be reliably erased;
the mutable buffers owned here are erased in finally blocks.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

PROVIDERS = frozenset({"openai", "telegram"})
MAX_KEY_BYTES = 5 * 512  # CRED_MAX_CREDENTIAL_BLOB_SIZE (wincred.h)
_ERROR_NOT_FOUND = 1168
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_CODES = frozenset({
    "credential_unsupported", "credential_unavailable", "credential_corrupt",
    "credential_invalid_provider", "credential_invalid_scope", "credential_invalid_key",
})


class CredentialError(Exception):
    """A fixed code only: no OS message, input, key fragment or target path."""

    def __init__(self, code: str):
        self.code = code if code in _ERROR_CODES else "credential_unavailable"
        super().__init__(self.code)


@dataclass(frozen=True)
class CredentialStatus:
    supported: bool
    configured: bool | None
    error_code: str | None = None


class CredentialStore(Protocol):
    def get_key(self, scope: Path, provider: str) -> str | None: ...
    def set_key(self, scope: Path, provider: str, key: str) -> None: ...
    def delete_key(self, scope: Path, provider: str) -> bool: ...
    def status(self, scope: Path, provider: str) -> CredentialStatus: ...


# Shared by all store instances in this process. OS credential operations are
# atomic; this also serializes validation/read/write/delete for each app scope.
_lock_guard = threading.Lock()
_target_locks: dict[str, threading.RLock] = {}


def _lock_for(target: str) -> threading.RLock:
    with _lock_guard:
        return _target_locks.setdefault(target, threading.RLock())


def _target(scope: Path, provider: str) -> str:
    if type(provider) is not str or provider not in PROVIDERS:
        raise CredentialError("credential_invalid_provider")
    try:
        if not isinstance(scope, Path) or not scope.is_absolute():
            raise ValueError()
        normalized = os.path.normcase(str(scope.resolve(strict=False)))
        if "\x00" in normalized:
            raise ValueError()
        digest = hashlib.sha256((normalized + "\x00" + provider).encode("utf-8")).hexdigest()
    except Exception:
        raise CredentialError("credential_invalid_scope") from None
    return "Worklead/ai/v1/" + digest


def _wipe(blob: bytearray) -> None:
    if blob:
        ctypes.memset(ctypes.addressof((ctypes.c_ubyte * len(blob)).from_buffer(blob)), 0, len(blob))


def _validate_key(key: str) -> None:
    # API keys are opaque printable ASCII, never normalized or truncated.
    if (type(key) is not str or not 1 <= len(key) <= MAX_KEY_BYTES
            or any(not 33 <= ord(char) <= 126 for char in key)):
        raise CredentialError("credential_invalid_key")


def _validate_blob(blob: bytearray) -> None:
    if not 1 <= len(blob) <= MAX_KEY_BYTES or any(not 33 <= char <= 126 for char in blob):
        raise CredentialError("credential_corrupt")


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32),
        ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
        ("LastWritten", _FILETIME), ("CredentialBlobSize", ctypes.c_uint32),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


class _WinCredentialAPI:
    def __init__(self, *, _dll=None, _last_error=None):
        if _dll is None:
            if sys.platform != "win32":
                raise CredentialError("credential_unsupported")
            try:
                _dll = ctypes.WinDLL("advapi32.dll", use_last_error=True)
            except Exception:
                raise CredentialError("credential_unavailable") from None
        self._dll = _dll
        self._last_error = _last_error or ctypes.get_last_error
        self._dll.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
        self._dll.CredReadW.restype = ctypes.c_int32
        self._dll.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), ctypes.c_uint32]
        self._dll.CredWriteW.restype = ctypes.c_int32
        self._dll.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self._dll.CredDeleteW.restype = ctypes.c_int32
        self._dll.CredFree.argtypes = [ctypes.c_void_p]
        self._dll.CredFree.restype = None

    def read(self, target: str) -> bytearray | None:
        ptr = ctypes.POINTER(_CREDENTIALW)()
        size, blob_pointer, blob = 0, None, None
        try:
            if not self._dll.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
                if self._last_error() == _ERROR_NOT_FOUND:
                    return None
                raise CredentialError("credential_unavailable")
            if not ptr:
                raise CredentialError("credential_corrupt")
            cred = ptr.contents
            size, blob_pointer = int(cred.CredentialBlobSize), cred.CredentialBlob
            if (cred.Type != _CRED_TYPE_GENERIC or not cred.TargetName
                    or cred.TargetName.casefold() != target.casefold()
                    or not 1 <= size <= MAX_KEY_BYTES or not blob_pointer):
                raise CredentialError("credential_corrupt")
            blob = bytearray(size)
            ctypes.memmove((ctypes.c_ubyte * size).from_buffer(blob), blob_pointer, size)
            return blob
        except CredentialError:
            if blob is not None:
                _wipe(blob)
            raise
        except Exception:
            if blob is not None:
                _wipe(blob)
            raise CredentialError("credential_unavailable") from None
        finally:
            if ptr:
                try:
                    try:
                        if blob_pointer and 1 <= size <= MAX_KEY_BYTES:
                            ctypes.memset(blob_pointer, 0, size)
                    finally:
                        self._dll.CredFree(ptr)
                except Exception:
                    if blob is not None:
                        _wipe(blob)
                    raise CredentialError("credential_unavailable") from None

    def write(self, target: str, blob: bytearray) -> None:
        buf = (ctypes.c_ubyte * len(blob)).from_buffer(blob)
        cred = _CREDENTIALW()
        cred.Type = _CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = "Worklead AI"
        try:
            if not self._dll.CredWriteW(ctypes.byref(cred), 0):
                raise CredentialError("credential_unavailable")
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("credential_unavailable") from None
        finally:
            ctypes.memset(buf, 0, len(blob))
            ctypes.memset(ctypes.byref(cred), 0, ctypes.sizeof(cred))

    def delete(self, target: str) -> bool:
        try:
            if self._dll.CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
                return True
            if self._last_error() == _ERROR_NOT_FOUND:
                return False
            raise CredentialError("credential_unavailable")
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("credential_unavailable") from None


class WindowsCredentialStore:
    def __init__(self, *, _api: _WinCredentialAPI | None = None, _platform: str | None = None):
        self._api = _api
        self._platform = sys.platform if _platform is None else _platform

    def _native(self) -> _WinCredentialAPI:
        if self._platform != "win32":
            raise CredentialError("credential_unsupported")
        if self._api is None:
            self._api = _WinCredentialAPI()
        return self._api

    def get_key(self, scope: Path, provider: str) -> str | None:
        target = _target(scope, provider)
        with _lock_for(target):
            blob = self._native().read(target)
            if blob is None:
                return None
            try:
                _validate_blob(blob)
                return blob.decode("ascii")
            finally:
                _wipe(blob)

    def set_key(self, scope: Path, provider: str, key: str) -> None:
        target = _target(scope, provider)
        _validate_key(key)
        blob = bytearray(key, "ascii")
        try:
            with _lock_for(target):
                self._native().write(target, blob)
        finally:
            _wipe(blob)

    def delete_key(self, scope: Path, provider: str) -> bool:
        target = _target(scope, provider)
        with _lock_for(target):
            return self._native().delete(target)

    def status(self, scope: Path, provider: str) -> CredentialStatus:
        try:
            target = _target(scope, provider)
            with _lock_for(target):
                blob = self._native().read(target)
                if blob is None:
                    return CredentialStatus(True, False)
                try:
                    _validate_blob(blob)
                    return CredentialStatus(True, True)
                finally:
                    _wipe(blob)
        except CredentialError as exc:
            return CredentialStatus(exc.code != "credential_unsupported", None, exc.code)


class FakeCredentialStore:
    """Memory-only test double. Never selected by the production factory."""

    def __init__(self):
        self._blobs: dict[str, bytearray] = {}
        self._failures: dict[str, str] = {}

    def set_failure(self, operation: str, code: str = "credential_unavailable") -> None:
        if operation not in {"read", "write", "delete"}:
            raise ValueError("unknown credential operation")
        self._failures[operation] = code if code in _ERROR_CODES else "credential_unavailable"

    def clear_failures(self) -> None:
        self._failures.clear()

    def _check(self, operation: str) -> None:
        if operation in self._failures:
            raise CredentialError(self._failures[operation])

    def get_key(self, scope: Path, provider: str) -> str | None:
        target = _target(scope, provider)
        with _lock_for(target):
            self._check("read")
            blob = self._blobs.get(target)
            return None if blob is None else blob.decode("ascii")

    def set_key(self, scope: Path, provider: str, key: str) -> None:
        target = _target(scope, provider)
        _validate_key(key)
        with _lock_for(target):
            self._check("write")
            new = bytearray(key, "ascii")
            old = self._blobs.get(target)
            if old is not None:
                _wipe(old)
            self._blobs[target] = new

    def delete_key(self, scope: Path, provider: str) -> bool:
        target = _target(scope, provider)
        with _lock_for(target):
            self._check("delete")
            old = self._blobs.pop(target, None)
            if old is not None:
                _wipe(old)
            return old is not None

    def status(self, scope: Path, provider: str) -> CredentialStatus:
        try:
            target = _target(scope, provider)
            with _lock_for(target):
                self._check("read")
                return CredentialStatus(True, target in self._blobs)
        except CredentialError as exc:
            return CredentialStatus(exc.code != "credential_unsupported", None, exc.code)


_default_store = WindowsCredentialStore()


def get_key(scope: Path, provider: str, *, store: CredentialStore | None = None) -> str | None:
    return (store or _default_store).get_key(scope, provider)


def set_key(scope: Path, provider: str, key: str, *, store: CredentialStore | None = None) -> None:
    (store or _default_store).set_key(scope, provider, key)


def delete_key(scope: Path, provider: str, *, store: CredentialStore | None = None) -> bool:
    return (store or _default_store).delete_key(scope, provider)


def status(scope: Path, provider: str, *, store: CredentialStore | None = None) -> CredentialStatus:
    return (store or _default_store).status(scope, provider)
