"""Separate capability server; unread tokens and main API tokens never apply."""
import json

from .applications import ApplicationError
from .auto_apply_models import IdentityIn, NextIn, ResultIn, ReviewIn
from .chrome_bridge import EXTENSION_ORIGIN, _BridgeServer, _Handler, _unique_object

BridgeServer = _BridgeServer
MODELS = {"/next": NextIn, "/review": ReviewIn, "/permit": IdentityIn, "/result": ResultIn}


class Handler(_Handler):
    def _reply(self, code, data=None, *, cors=False):
        # Windows can reset a socket with unread request bytes, discarding even
        # an authentication error response. Drain only a bounded known length.
        raw_length = self.headers.get("Content-Length", "")
        if not getattr(self, "_body_consumed", False) and raw_length.isascii() and raw_length.isdecimal() and len(raw_length) <= 5:
            length = int(raw_length)
            if length <= 65536 and self.headers.get("Transfer-Encoding") is None:
                try: self.rfile.read(length)
                except (OSError, TimeoutError): pass
            self._body_consumed = True
        super()._reply(code, data, cors=cors)

    def _scope(self):
        if self.path not in MODELS:
            self._reply(404, {"error": "not_found"})
            return False
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self._reply(403, {"error": "host_rejected"})
            return False
        if len(self.headers.get_all("Origin", [])) != 1 or self.headers.get("Origin") != EXTENSION_ORIGIN:
            self._reply(403, {"error": "origin_rejected"})
            return False
        if self.server.runtime._stop.is_set():
            self._reply(503, {"error": "bridge_stopped"}, cors=True)
            return False
        return True

    def do_POST(self):
        if not self._scope(): return
        runtime = self.server.runtime
        if len(self.headers.get_all("Authorization", [])) != 1 or not runtime.authorized(self.headers.get("Authorization", "")):
            self._reply(401, {"error": "unauthorized"}, cors=True)
            return
        raw_length = self.headers.get("Content-Length", "")
        if (len(self.headers.get_all("Content-Length", [])) != 1 or self.headers.get("Transfer-Encoding") is not None
                or not raw_length.isascii() or not raw_length.isdecimal() or len(raw_length) > 4):
            self._reply(400, {"error": "invalid_length"}, cors=True)
            return
        length = int(raw_length)
        if not 1 <= length <= 2048:
            self._reply(413, {"error": "body_too_large"}, cors=True)
            return
        if self.headers.get("Content-Type", "").lower().replace(" ", "") not in {"application/json", "application/json;charset=utf-8"}:
            self._reply(415, {"error": "json_required"}, cors=True)
            return
        try:
            raw = self.rfile.read(length)
            self._body_consumed = True
            if len(raw) != length: raise ValueError("incomplete body")
            body = MODELS[self.path].model_validate(json.loads(raw, object_pairs_hook=_unique_object))
        except (ValueError, TypeError, UnicodeError, OSError, TimeoutError):
            self._reply(422, {"error": "invalid_request"}, cors=True)
            return
        try:
            result = getattr(runtime, self.path[1:])(body)
        except ApplicationError as exc:
            self._reply(exc.status, {"error": exc.code}, cors=True)
        except Exception:
            self._reply(503, {"error": "auto_apply_unavailable"}, cors=True)
        else:
            self._reply(200, result, cors=True)
