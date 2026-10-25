"""백엔드 실행 (Tauri sidecar).

시작 순서: 단일 실행 잠금 → 마이그레이션(사전 백업) → 소스 동기화 → 워커 시작 → 127.0.0.1 임의 포트 바인딩
→ stdout 에 준비 줄 1개 출력 {"event":"ready","port":..,"token":..} → 요청 처리.
- 토큰은 실행마다 새로 만들고 stdout(부모 프로세스 파이프)으로만 전달한다. 로그·명령행·URL 에 넣지 않는다.
- --sidecar: stdin 이 닫히면(부모 종료) 스스로 종료한다 (자식 프로세스 정리).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import re
import secrets
import socket
import sys
import threading
from pathlib import Path
from typing import Any

import uvicorn

from .api.app import create_app
from .config import APP_VERSION, RuntimeConfig
from .context import AppContext
from .db import Database
from .engine.worker import Workers
from .services.settings import get_profile_row
from .sources.registry import default_adapters, sync_sources
from .storage import migrate

log = logging.getLogger("worklead")


def local_loop_factory() -> asyncio.AbstractEventLoop:
    # The local API uses sockets and thread workers, with no asyncio subprocesses.
    # CPython 3.11's Windows Proactor socket close can fail before releasing the
    # socket/server attachment when a WebView cancels an SSE connection (10022).
    return asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()


class _Mask(logging.Filter):
    PATTERNS = [
        (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [masked]"),
        (re.compile(r"(?i)(token|api[_-]?key|authorization)(\"?\s*[:=]\s*\"?)[^\s\",]+"), r"\1\2[masked]"),
        (re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}"), "[phone]"),
        (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
        (re.compile(r"(?<!\d)0(?:2|[3-6][1-5])[-\s]\d{3,4}[-\s]\d{4}(?!\d)"), "[phone]"),
        (re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)"), "[identity]"),
    ]

    def __init__(self, secrets_: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets_ if s]

    def redact(self, msg: str) -> str:
        for sec in self.secrets:
            msg = msg.replace(sec, "[masked]")
        for pat, repl in self.PATTERNS:
            msg = pat.sub(repl, msg)
        return msg

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = self.redact(record.getMessage()), ()
        return True


class _PrivateFormatter(logging.Formatter):
    """Exception messages, source lines and absolute paths can contain user data."""
    def __init__(self, redactor: _Mask, format_: str | None = None):
        super().__init__(format_)
        self.redactor = redactor

    def formatException(self, exc_info) -> str:
        import builtins
        _, exception, trace = exc_info
        name = type(exception).__name__
        known = isinstance(getattr(builtins, name, None), type) and issubclass(getattr(builtins, name), BaseException)
        if not known and name not in {'OperationalError', 'IntegrityError', 'InvalidRequestError', 'TimeoutException', 'ConnectError', 'AIFailure', 'ParseError'}:
            name = 'Exception'
        lines = ['Traceback (messages, source text and absolute paths omitted):']
        package = Path(__file__).resolve().parent
        while trace:
            file = Path(trace.tb_frame.f_code.co_filename)
            try:
                relative = file.resolve().relative_to(package).as_posix()
            except (ValueError, OSError):
                relative = '<library>'
            lines.append(f'  {relative}:{trace.tb_lineno}')
            trace = trace.tb_next
        lines.append('exception_type=' + name)
        return '\n'.join(lines)

    def formatStack(self, stack_info: str) -> str:
        return 'Stack source text and absolute paths omitted'

    def format(self, record: logging.LogRecord) -> str:
        # Another handler may have cached a raw traceback. Recompute it here.
        cached = record.exc_text
        record.exc_text = None
        try:
            return self.redactor.redact(super().format(record))
        finally:
            record.exc_text = cached


def setup_logging(cfg: RuntimeConfig, token: str) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(cfg.log_dir / "backend.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    redactor = _Mask([token])
    handler.setFormatter(_PrivateFormatter(redactor, "%(asctime)s %(levelname)s %(name)s %(threadName)s %(message)s"))
    handler.addFilter(redactor)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.setFormatter(_PrivateFormatter(redactor))
    err.addFilter(redactor)
    root = logging.getLogger()
    root.handlers[:] = [handler, err]
    root.setLevel(logging.INFO)
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class InstanceLock:
    """같은 데이터 폴더에 백엔드가 둘 이상 뜨지 않게 한다."""

    def __init__(self, path: Path):
        self.path = path
        self._fh: Any = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")  # noqa: SIM115
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return False
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


def build_context(cfg: RuntimeConfig, *, adapters: dict[str, object] | None = None, token: str | None = None) -> AppContext:
    cfg.ensure_dirs()
    migrate(cfg.db_path, cfg.backup_dir)
    db = Database(cfg.db_path)
    adapters = adapters if adapters is not None else default_adapters()
    with db.session() as s:
        sync_sources(s, adapters)
        get_profile_row(s)
    return AppContext(config=cfg, db=db, adapters=adapters, token=token or secrets.token_urlsafe(32))


def _emit_line(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="worklead-backend")
    ap.add_argument("--port", type=int, default=0, help="0 = 임의 포트")
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--dev", action="store_true", help="개발 Origin 허용, 토큰은 WORKLEAD_DEV_TOKEN 환경변수 사용")
    ap.add_argument("--demo", action="store_true", help="별도 데모 DB 에 합성 데이터로 실행 (bootstrap.mode=demo)")
    ap.add_argument("--sidecar", action="store_true", help="stdin 이 닫히면 종료")
    ap.add_argument("--cafe-browser-self-check", action="store_true", help="별도 프로필로 시스템 Edge·패키지 자산만 확인 (외부 페이지·운영 DB 미사용)")
    ap.add_argument("--remove-ai-credential", action="store_true", help="명시 확인 후 현재 데이터 폴더의 OS AI 키만 삭제하고 종료")
    ap.add_argument("--confirm-key-delete", action="store_true", help="OS AI 키 삭제를 명시 확인")
    args = ap.parse_args(argv)

    if args.cafe_browser_self_check:
        if args.data_dir is None or args.sidecar or args.demo or args.dev or args.remove_ai_credential or args.confirm_key_delete:
            _emit_line({'event':'cafe_browser_probe', 'status':'error', 'code':'isolated_data_dir_required'})
            return 64
        from .cafe.browser import EdgeBrowser
        profile = args.data_dir.resolve() / 'browser-selfcheck' / secrets.token_hex(8)
        probe = EdgeBrowser(profile)
        try:
            probe.open()
            probe.page.set_content('<h1>synthetic browser self-check</h1>')
            assert probe.page.locator('h1').inner_text() == 'synthetic browser self-check'
            probe.context.add_cookies([{'name':'synthetic_probe', 'value':'fixture', 'domain':'cafe.daangn.com', 'path':'/', 'expires':2000000000}])
            probe.close()
            probe.open()
            persisted = any(c['name'] == 'synthetic_probe' for c in probe.context.cookies())
            import json as probe_json
            value = probe_json.loads((Path(__file__).parent / 'cafe/default_template.json').read_text(encoding='utf-8'))
            _emit_line({'event':'cafe_browser_probe', 'status':'ok' if persisted else 'error', 'engine':'system_msedge', 'persisted':persisted, 'template_version':value['version']})
            return 0 if persisted else 18
        except Exception:
            _emit_line({'event':'cafe_browser_probe', 'status':'error', 'code':'cafe_browser_unavailable'})
            return 18
        finally:
            probe.close()

    cfg = RuntimeConfig(dev=args.dev, demo=args.demo, port=args.port)
    if args.data_dir:
        cfg.data_dir = args.data_dir
    if args.remove_ai_credential or args.confirm_key_delete:
        if not (args.remove_ai_credential and args.confirm_key_delete) or args.dev or args.demo or args.sidecar:
            _emit_line({"event": "error", "code": "credential_confirmation_required"})
            return 64
        # Dedicated uninstall action: no DB, log, provider call or key read.
        from .services.credentials import CredentialError, WindowsCredentialStore
        try:
            WindowsCredentialStore().delete_key(cfg.data_dir.resolve(), "openai")
        except CredentialError:
            _emit_line({"event": "error", "code": "credential_delete_failed"})
            return 18
        _emit_line({"event": "credential_removed"})
        return 0
    token = os.environ.get("WORKLEAD_DEV_TOKEN") if args.dev else None
    token = token or secrets.token_urlsafe(32)
    setup_logging(cfg, token)

    lock = InstanceLock(cfg.data_dir / ("backend-demo.lock" if cfg.demo else "backend.lock"))
    if not lock.acquire():
        _emit_line({"event": "error", "code": "already_running", "message": "같은 데이터 폴더를 쓰는 백엔드가 이미 실행 중입니다"})
        return 3
    try:
        try:
            ctx = build_context(cfg, token=token)
        except Exception as exc:  # noqa: BLE001
            log.exception("startup failed")
            _emit_line({"event": "error", "code": "startup_failed", "message": f"{type(exc).__name__}: {exc}"[:500]})
            return 2
        if cfg.demo:
            from .services.demo import seed_if_empty

            seed_if_empty(ctx)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) if sys.platform != "win32" else None
        try:
            sock.bind((cfg.host, cfg.port))
        except OSError as exc:
            _emit_line({"event": "error", "code": "port_unavailable", "message": str(exc)})
            return 4
        port = sock.getsockname()[1]
        workers = Workers(ctx)
        workers.start()
        if cfg.run_worker and not cfg.demo:
            ctx.cafe_runtime.start()
        app = create_app(ctx, workers_running=lambda: all(t.is_alive() for t in workers.threads))
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off", timeout_graceful_shutdown=5,
                                             loop="worklead.server:local_loop_factory" if sys.platform == "win32" else "auto"))
        if args.sidecar:
            def _watch_stdin() -> None:
                try:
                    while sys.stdin.read(1024):
                        pass
                except (OSError, ValueError):
                    pass
                log.info("stdin closed — shutting down")
                server.should_exit = True

            threading.Thread(target=_watch_stdin, name="stdin-watch", daemon=True).start()
        ready = {"event": "ready", "port": port, "pid": os.getpid(), "version": APP_VERSION, "api": "v1", "mode": "demo" if cfg.demo else "live"}
        if not args.dev:
            ready["token"] = ctx.token
        sock.listen(128)

        def _announce_when_serving() -> None:
            # 요청을 실제로 받을 수 있을 때만 준비 줄을 낸다 (셸이 바로 연결해도 거부되지 않도록)
            for _ in range(1500):
                if server.started:
                    _emit_line(ready)
                    log.info("backend ready port=%s mode=%s", port, ready["mode"])
                    return
                threading.Event().wait(0.02)
            _emit_line({"event": "error", "code": "startup_timeout", "message": "서버 시작 시간이 초과되었습니다"})

        threading.Thread(target=_announce_when_serving, name="ready-announce", daemon=True).start()
        try:
            server.run(sockets=[sock])
        finally:
            workers.stop()
            if ctx._jobs_auto_apply_runtime is not None:
                ctx._jobs_auto_apply_runtime.stop()
            if ctx._jobs_chrome_runtime is not None:
                ctx._jobs_chrome_runtime.stop()
            if ctx._jobs_message_runtime is not None:
                ctx._jobs_message_runtime.stop()
            if ctx._cafe_runtime is not None:
                ctx.cafe_runtime.stop()
            ctx.db.dispose()
        return 0
    finally:
        lock.release()


def main() -> None:
    sys.exit(serve())
