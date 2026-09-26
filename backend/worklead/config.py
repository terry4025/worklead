"""런타임 설정과 사용자 데이터 경로.

사용자 DB·설정·로그·내보내기는 사용자 AppData 하위에 둔다. 설치 폴더에는 쓰지 않는다.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Worklead"
APP_VERSION = "0.1.0"
API_VERSION = "v1"
CONTRACT_VERSION = "2026-09-26.1"


def default_data_dir() -> Path:
    override = os.environ.get("WORKLEAD_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_NAME.lower()


@dataclass
class RuntimeConfig:
    data_dir: Path = field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = 0
    dev: bool = False
    demo: bool = False
    #: 스케줄러·워커 실행 여부 (테스트에서 끈다)
    run_worker: bool = True
    #: 허용 Origin. Tauri 2 Windows(WebView2)는 http://tauri.localhost, macOS/Linux 는 tauri://localhost
    allowed_origins: tuple[str, ...] = ("http://tauri.localhost", "https://tauri.localhost", "tauri://localhost")
    #: 개발 모드에서만 추가되는 Origin
    dev_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")
    #: 수집기 User-Agent. 식별 가능한 이름을 쓴다 (위장하지 않음)
    user_agent: str = f"WorkleadLocal/{APP_VERSION} (+personal desktop lead review; non-commercial)"
    worker_poll_seconds: float = 1.0
    lease_seconds: int = 60
    scheduler_interval_seconds: int = 60

    @property
    def db_path(self) -> Path:
        return self.data_dir / ("worklead-demo.sqlite3" if self.demo else "worklead.sqlite3")

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.log_dir, self.export_dir, self.backup_dir):
            p.mkdir(parents=True, exist_ok=True)

    def origins(self) -> list[str]:
        return list(self.allowed_origins) + (list(self.dev_origins) if self.dev else [])
