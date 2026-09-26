from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from worklead.api.app import create_app
from worklead.config import RuntimeConfig
from worklead.context import AppContext
from worklead.server import build_context

TOKEN = "test-token-123"


def make_ctx(tmp_path: Path, adapters: dict[str, object] | None = None, **cfg_kw) -> AppContext:
    cfg = RuntimeConfig(data_dir=tmp_path, run_worker=False, **cfg_kw)
    ctx = build_context(cfg, adapters=adapters, token=TOKEN)
    ctx.sleep = lambda _s: None
    return ctx


def client_for(ctx: AppContext) -> TestClient:
    app = create_app(ctx, trusted_hosts={"testserver", "127.0.0.1"})
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[AppContext]:
    c = make_ctx(tmp_path)
    yield c
    c.db.dispose()


@pytest.fixture
def client(ctx: AppContext) -> TestClient:
    return client_for(ctx)
