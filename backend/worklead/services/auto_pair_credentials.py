"""One scoped Chrome reconnect grant in the current Windows user's vault.

The complete grant lives outside SQLite backups. No plaintext file fallback,
credential enumeration, arbitrary target, or secret-bearing errors are exposed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .credentials import CredentialError, _WinCredentialAPI, _lock_for, _wipe


def validate(value):
    if (not isinstance(value, dict) or set(value) != {"version", "token", "desired", "account", "profile", "min_score", "daily_limit", "paused"}
            or value["version"] != 1 or type(value["version"]) is not int
            or not isinstance(value["token"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value["token"])
            or type(value["desired"]) is not bool
            or any(v is not None and (not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v)) for v in (value["account"], value["profile"]))
            or type(value["min_score"]) is not int or not 0 <= value["min_score"] <= 100
            or type(value["daily_limit"]) is not int or not 1 <= value["daily_limit"] <= 200
            or value["paused"] not in (None, "account_changed", "login_required", "profile_changed", "database_restored")):
        raise CredentialError("credential_corrupt")
    return value


class AutoPairCredentialStore:
    def __init__(self, *, api=None):
        self._api = api

    def _native(self):
        if self._api is None:
            self._api = _WinCredentialAPI()
        return self._api

    def _target(self, scope: Path):
        if not isinstance(scope, Path) or not scope.is_absolute():
            raise CredentialError("credential_invalid_scope")
        normalized = os.path.normcase(str(scope.resolve(strict=False)))
        return "Worklead/jobs/pair/v1/" + hashlib.sha256(normalized.encode()).hexdigest()

    def read(self, scope):
        target = self._target(scope)
        with _lock_for(target):
            blob = self._native().read(target)
            if blob is None:
                return None
            try:
                return validate(json.loads(blob.decode("ascii")))
            except CredentialError:
                raise
            except Exception:
                raise CredentialError("credential_corrupt") from None
            finally:
                _wipe(blob)

    def write(self, scope, value):
        validate(value)
        target = self._target(scope)
        blob = bytearray(json.dumps(value, separators=(",", ":")), "ascii")
        try:
            with _lock_for(target):
                self._native().write(target, blob)
        finally:
            _wipe(blob)

    def delete(self, scope):
        target = self._target(scope)
        with _lock_for(target):
            return self._native().delete(target)
