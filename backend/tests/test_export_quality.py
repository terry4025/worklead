"""Complete, cancellable CSV/JSON exports across collection-sized datasets."""
import csv
import json
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from tests.conftest import client_for, make_ctx
from tests.test_api import drain
from worklead.engine.executors import RunScope
from worklead.models import Lead, Run


def fill(ctx, count):
    with ctx.db.session() as s:
        s.execute(insert(Lead), [{"id": f"export{i:05d}", "source_id": "manual",
                                 "title": "=FORMULA" if i == 0 else f"리드 {i}"} for i in range(count)])


def export(ctx, kind):
    client = client_for(ctx)
    run_id = client.post("/v1/exports", json={"kind": kind}).json()["job_id"]
    drain(ctx)
    return client.get(f"/v1/runs/{run_id}").json()


@pytest.mark.parametrize("kind", ["leads_csv", "leads_json"])
def test_export_includes_every_lead_beyond_ten_thousand(tmp_path, kind):
    ctx = make_ctx(tmp_path)
    try:
        fill(ctx, 10_001)
        run = export(ctx, kind)
        assert run["state"] == "succeeded" and run["result"]["rows"] == 10_001
        path = ctx.config.export_dir / run["result"]["file_name"]
        if kind == "leads_json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                data = list(csv.DictReader(stream))
        assert len(data) == 10_001
        assert {row["id"] for row in data} == {f"export{i:05d}" for i in range(10_001)}
        first = next(row for row in data if row["id"] == "export00000")
        assert first["title"] == ("=FORMULA" if kind == "leads_json" else "'=FORMULA")
        assert run["progress"]["done"] == run["progress"]["total"] == 10_001
        assert not list(ctx.config.export_dir.glob("*.part"))
    finally:
        ctx.db.dispose()


@pytest.mark.parametrize("kind", ["leads_csv", "leads_json"])
def test_cancelled_export_does_not_publish_an_incomplete_file(tmp_path, monkeypatch, kind):
    ctx = make_ctx(tmp_path)
    checkpoint = RunScope.checkpoint

    def cancel_after_first_batch(scope):
        if scope.done >= 500:
            with ctx.db.session() as s:
                s.get(Run, scope.run_id).cancel_requested = True
        checkpoint(scope)

    monkeypatch.setattr(RunScope, "checkpoint", cancel_after_first_batch)
    try:
        fill(ctx, 10_001)
        run = export(ctx, kind)
        assert run["state"] == "cancelled"
        assert "file_name" not in run["result"]
        assert not list(ctx.config.export_dir.iterdir())
    finally:
        ctx.db.dispose()


def test_two_exports_in_the_same_second_preserve_both_files(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.clock = lambda: datetime(2026, 9, 30, tzinfo=UTC)
    try:
        fill(ctx, 1)
        first = export(ctx, "leads_json")
        with ctx.db.session() as s:
            s.add(Lead(id="added", source_id="manual", title="추가 리드"))
        second = export(ctx, "leads_json")
        assert first["result"]["file_name"] != second["result"]["file_name"]
        assert len(json.loads((ctx.config.export_dir / first["result"]["file_name"]).read_text(encoding="utf-8"))) == 1
        assert len(json.loads((ctx.config.export_dir / second["result"]["file_name"]).read_text(encoding="utf-8"))) == 2
    finally:
        ctx.db.dispose()


def test_export_snapshot_is_consistent_while_collection_adds_a_lead(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    checkpoint = RunScope.checkpoint
    added = False

    def collect_during_export(scope):
        nonlocal added
        if not added and scope.total == 1 and scope.done == 0:
            with ctx.db.session() as s:
                s.add(Lead(id="concurrent", source_id="manual", title="수집 중 추가"))
            added = True
        checkpoint(scope)

    monkeypatch.setattr(RunScope, "checkpoint", collect_during_export)
    try:
        fill(ctx, 1)
        run = export(ctx, "leads_json")
        data = json.loads((ctx.config.export_dir / run["result"]["file_name"]).read_text(encoding="utf-8"))
        assert added and run["state"] == "succeeded"
        assert run["result"]["rows"] == run["progress"]["total"] == len(data) == 1
        assert data[0]["id"] == "export00000"
    finally:
        ctx.db.dispose()


def test_diagnostics_export_keeps_its_existing_archive_contract(tmp_path):
    ctx = make_ctx(tmp_path)
    try:
        run = export(ctx, "diagnostics")
        assert run["state"] == "succeeded" and run["result"]["rows"] is None
        with zipfile.ZipFile(ctx.config.export_dir / run["result"]["file_name"]) as archive:
            assert {"versions.json", "settings.json", "sources.json", "runs.json"} <= set(archive.namelist())
    finally:
        ctx.db.dispose()
