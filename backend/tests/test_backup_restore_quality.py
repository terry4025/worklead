"""Backup API, isolated restore, and maintenance boundaries on temporary DBs."""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, select
from starlette.requests import Request

from tests.conftest import client_for, make_ctx
from tests.test_source_fields import sample
from worklead import storage
from worklead.engine import http as http_mod, runs, worker as worker_mod
from worklead.engine.pipeline import ingest
from worklead.models import HostBudget, Lead, Outcome, Run, Source, SourcePolicy, new_id
from worklead.services import backups
from worklead.services.settings import put_setting, update_profile

NOW = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.mark.parametrize("job", ["recheck", "reanalyze"])
@pytest.mark.parametrize("state", ["queued", "running", "paused", "succeeded", "orphaned"])
def test_restore_clears_job_flags_and_allows_a_new_explicit_request(ctx, job, state):
    lead_id = seed_lead(ctx, memo="선택한 백업의 사용자 메모")
    # Authorization fixture only; workers remain disabled and no HTTP is sent.
    with ctx.db.session() as s:
        s.get(SourcePolicy, "daangn-alba").status = "restricted"
    with client_for(ctx) as client:
        queued = client.post(f"/v1/leads/{lead_id}/{job}")
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["run_id"]
        with ctx.db.session() as s:
            lead = s.get(Lead, lead_id)
            lead.draft_text, lead.draft_edited = "사용자가 작성한 문의", True
            run = s.get(Run, run_id)
            if state == "orphaned":
                s.delete(run)
            else:
                run.state = state
        inspection = create_and_inspect(client)
        with ctx.db.session() as s:
            s.get(Lead, lead_id).memo = "복원 직전 메모"
        response = restore(client, inspection)
        assert response.status_code == 200, response.text
        detail = client.get(f"/v1/leads/{lead_id}").json()
        assert detail["active_job"] is None
        assert detail["memo"] == "선택한 백업의 사용자 메모"
        assert detail["draft"]["text"] == "사용자가 작성한 문의"
        with ctx.db.session() as s:
            run = s.get(Run, run_id)
            if state in ("queued", "running", "paused"):
                assert run.state == "cancelled" and run.finished_at == NOW
            elif state == "succeeded":
                assert run.state == "succeeded"
            else:
                assert run is None
        requested = client.post(f"/v1/leads/{lead_id}/{job}")
        assert requested.status_code == 202, requested.text
        assert requested.json()["run_id"] != run_id
        assert client.get(f"/v1/leads/{lead_id}").json()["active_job"] == job


def seed_lead(ctx, suffix="00001", memo="백업할 메모"):
    ctx.clock = lambda: NOW
    post = sample()
    post.source_post_id = "fixture" + suffix
    post.original_url = post.canonical_url = "https://jobs.daangn.com/job-posts/" + post.source_post_id
    with ctx.db.session() as s:
        result = ingest(s, "daangn-alba", post, parser_version="test", now=NOW)
        s.get(Lead, result.lead_id).memo = memo
    return result.lead_id


def create_and_inspect(client):
    created = client.post("/v1/backups")
    assert created.status_code == 201, created.text
    inspection = client.get(f"/v1/backups/{created.json()['id']}")
    assert inspection.status_code == 200, inspection.text
    assert inspection.json()["can_restore"] is True
    return inspection.json()


def restore(client, inspection):
    return client.post(f"/v1/backups/{inspection['backup']['id']}/restore",
                       json={"sha256": inspection["sha256"], "confirm": True})


def memo_from_file(path, lead_id):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT memo FROM leads WHERE id=?", (lead_id,)).fetchone()[0]


def assert_current(ctx, lead_id, memo, count=1):
    with ctx.db.session() as s:
        assert s.get(Lead, lead_id).memo == memo
        assert s.scalar(select(func.count()).select_from(Lead)) == count


def short_maintenance_timeout(ctx, monkeypatch):
    original = ctx.db.gate.exclusive
    monkeypatch.setattr(ctx.db.gate, "exclusive", lambda timeout=10.0: original(timeout=0.03))


def test_online_backup_includes_wal_and_preserves_same_timestamp_files(ctx, monkeypatch):
    lead_id = seed_lead(ctx)
    frozen = datetime(2026, 9, 30, 1, 2, 3, 456789, tzinfo=UTC)

    class FrozenDatetime:
        @classmethod
        def now(cls, tz):
            return frozen

    monkeypatch.setattr(storage, "datetime", FrozenDatetime)
    with closing(sqlite3.connect(ctx.config.db_path)) as writer, client_for(ctx) as client:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE leads SET memo=? WHERE id=?", ("WAL에 커밋된 메모", lead_id))
        writer.commit()
        wal = Path(str(ctx.config.db_path) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
        first = create_and_inspect(client)
        first_path = ctx.config.backup_dir / first["backup"]["id"]
        first_digest = storage.file_digest(first_path)
        assert memo_from_file(first_path, lead_id) == "WAL에 커밋된 메모"
        writer.execute("UPDATE leads SET memo=? WHERE id=?", ("두 번째 커밋", lead_id))
        writer.commit()
        second = create_and_inspect(client)
        assert first["backup"]["id"] != second["backup"]["id"]
        assert storage.file_digest(first_path) == first_digest
        assert memo_from_file(ctx.config.backup_dir / second["backup"]["id"], lead_id) == "두 번째 커밋"
        listed = client.get("/v1/backups").json()
        assert {row["id"] for row in listed["items"]} == {first["backup"]["id"], second["backup"]["id"]}
        assert set(first) == {"backup", "sha256", "revision", "can_restore", "problem", "lead_count"}
        assert set(first["backup"]) == {"id", "file_name", "created_at", "size_bytes"}
        assert "WAL에 커밋된 메모" not in json.dumps(listed, ensure_ascii=False)
        assert first["sha256"] == first_digest and first["lead_count"] == 1
    assert not list(ctx.config.backup_dir.glob("*.part*"))


def test_api_restore_preserves_user_records_settings_and_safety_backup(ctx):
    lead_id = seed_lead(ctx)
    with ctx.db.session() as s:
        lead = s.get(Lead, lead_id)
        lead.user_mark, lead.sales_stage = "interested", "contacted"
        lead.draft_text, lead.draft_edited = "내가 작성한 초안", True
        outcome_id = new_id("out")
        s.add(Outcome(id=outcome_id, lead_id=lead_id, kind="payment_received", amount=600_000,
                      occurred_on="2026-09-30", note="입금 확인 기록"))
        update_profile(s, {"min_contract": 500_000, "target_hourly": 30_000})
        put_setting(s, "retention", {"raw_days": 45})
        s.get(Source, "daangn-alba").auto_collect_enabled = True
        for state in ("queued", "running", "paused", "succeeded"):
            s.add(Run(id="backup-run-" + state, kind="import", state=state,
                      lease_owner="old-owner" if state == "running" else None,
                      lease_expires_at=NOW + timedelta(hours=1), retry_after=NOW + timedelta(hours=1),
                      pause_requested=True, cancel_requested=True))
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        seed_lead(ctx, "00002", "백업 뒤에 추가된 리드")
        with ctx.db.session() as s:
            s.get(Lead, lead_id).memo = "복원 직전 메모"
            update_profile(s, {"min_contract": 2_000_000})
            put_setting(s, "retention", {"raw_days": 10})
        generation = ctx.db.gate.generation
        response = restore(client, inspection)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["restored_backup_id"] == inspection["backup"]["id"]
        assert result["lead_count"] == 1 and result["auto_collection_paused"] is True
        assert ctx.db.gate.generation == generation + 1
        detail = client.get(f"/v1/leads/{lead_id}").json()
        assert (detail["memo"], detail["user_mark"], detail["sales_stage"]) == ("백업할 메모", "interested", "contacted")
        assert detail["outcomes"][0]["id"] == outcome_id
        assert detail["outcomes"][0]["amount"] == 600_000
        settings = client.get("/v1/settings").json()
        assert settings["profile"]["min_contract"] == 500_000 and settings["retention"]["raw_days"] == 45
        with ctx.db.session() as s:
            lead = s.get(Lead, lead_id)
            assert (lead.draft_text, lead.draft_edited) == ("내가 작성한 초안", True)
            assert not any(source.auto_collect_enabled for source in s.scalars(select(Source)))
            for state in ("queued", "running", "paused"):
                run = s.get(Run, "backup-run-" + state)
                assert run.state == "cancelled" and run.finished_at == NOW
                assert run.lease_owner is None and run.lease_expires_at is None and run.retry_after is None
                assert not run.pause_requested and not run.cancel_requested
            assert s.get(Run, "backup-run-succeeded").state == "succeeded"
        safety = ctx.config.backup_dir / result["safety_backup"]["id"]
        assert storage.validate_database(safety)[1] == 2
        assert memo_from_file(safety, lead_id) == "복원 직전 메모"
        assert not list(ctx.config.data_dir.glob("worklead-restore-*"))


@pytest.mark.parametrize("confirm", [None, False, 1, "true"])
def test_restore_requires_explicit_boolean_confirmation(ctx, confirm):
    lead_id = seed_lead(ctx)
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        body = {"sha256": inspection["sha256"]}
        if confirm is not None:
            body["confirm"] = confirm
        before = set(ctx.config.backup_dir.iterdir())
        response = client.post(f"/v1/backups/{inspection['backup']['id']}/restore", json=body)
        assert response.status_code == 422
        assert set(ctx.config.backup_dir.iterdir()) == before and ctx.db.gate.generation == 0
    assert_current(ctx, lead_id, "백업할 메모")


def test_changed_backup_hash_is_rejected_before_safety_or_live_mutation(ctx):
    lead_id = seed_lead(ctx)
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        with (ctx.config.backup_dir / inspection["backup"]["id"]).open("ab") as stream:
            stream.write(b"changed-after-inspection")
        before = set(ctx.config.backup_dir.iterdir())
        response = restore(client, inspection)
        assert response.status_code == 409 and response.json()["error"]["code"] == "backup_changed"
        assert set(ctx.config.backup_dir.iterdir()) == before and ctx.db.gate.generation == 0
        assert not list(ctx.config.data_dir.glob("worklead-restore-*"))
    assert_current(ctx, lead_id, "백업할 메모")


@pytest.mark.parametrize("problem", ["corrupt", "unknown_revision", "fake_head", "missing_column", "broken_fk", "broken_json"])
def test_invalid_backups_are_inspectable_but_cannot_replace_live_data(ctx, problem):
    lead_id = seed_lead(ctx)
    with ctx.db.session() as s:
        s.add(Outcome(id="out-fixture", lead_id=lead_id, kind="payment_received", amount=1,
                      occurred_on="2026-09-30"))
    path = ctx.config.backup_dir / f"worklead-invalid-{problem}.sqlite3"
    if problem == "corrupt":
        path.write_bytes(b"not a sqlite database")
    elif problem == "fake_head":
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
            conn.execute("INSERT INTO alembic_version VALUES (?)", (storage.head_revision(),))
            conn.commit()
    else:
        source = storage.backup_database(ctx.config.db_path, ctx.config.backup_dir, "fixture")
        source.replace(path)
        with closing(sqlite3.connect(path)) as conn:
            if problem == "unknown_revision":
                conn.execute("UPDATE alembic_version SET version_num='unknown-future-revision'")
            elif problem == "missing_column":
                conn.execute("ALTER TABLE leads RENAME COLUMN memo TO unsupported_memo")
            elif problem == "broken_json":
                conn.execute("UPDATE leads SET categories='not-json'")
            else:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute("UPDATE outcomes SET lead_id='missing-lead'")
            conn.commit()
    with client_for(ctx) as client:
        inspection = client.get(f"/v1/backups/{path.name}")
        assert inspection.status_code == 200, inspection.text
        data = inspection.json()
        assert data["can_restore"] is False and data["problem"]
        before = set(ctx.config.backup_dir.iterdir())
        response = restore(client, data)
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_backup"
        assert set(ctx.config.backup_dir.iterdir()) == before and ctx.db.gate.generation == 0
    assert_current(ctx, lead_id, "백업할 메모")


def test_known_previous_revision_migrates_on_isolated_copy(ctx):
    seed_lead(ctx)
    path = ctx.config.backup_dir / "worklead-previous-revision.sqlite3"
    command.upgrade(storage.alembic_config(path), "0001")
    original_digest = storage.file_digest(path)
    with client_for(ctx) as client:
        inspection = client.get(f"/v1/backups/{path.name}").json()
        assert inspection["can_restore"] is True and inspection["revision"] == "0001" and inspection["lead_count"] == 0
        response = restore(client, inspection)
        assert response.status_code == 200, response.text
        assert response.json()["lead_count"] == 0
        assert storage.current_revision(ctx.config.db_path) == storage.head_revision()
        assert client.get("/v1/leads").json()["items"] == []
        assert client.get("/v1/settings").status_code == 200
        with ctx.db.session() as s:
            assert s.get(Source, "manual") is not None
            assert s.get(Source, "daangn-alba") is not None
    assert storage.current_revision(path) == "0001" and storage.file_digest(path) == original_digest


def test_candidate_rejects_paths_other_modes_and_file_symlink(ctx, tmp_path):
    seed_lead(ctx)
    with client_for(ctx) as client:
        valid = create_and_inspect(client)
        valid_path = ctx.config.backup_dir / valid["backup"]["id"]
        for name in ("worklead-demo-fixture.sqlite3", "foreign-fixture.sqlite3"):
            shutil.copyfile(valid_path, ctx.config.backup_dir / name)
            assert client.get(f"/v1/backups/{name}").status_code == 400
        for name in ("../worklead-fixture.sqlite3", r"..\worklead-fixture.sqlite3", r"C:\worklead-fixture.sqlite3"):
            with pytest.raises(backups.BackupError) as exc:
                backups.inspect_backup(ctx, name)
            assert exc.value.code == "invalid_backup"
        link = ctx.config.backup_dir / "worklead-link.sqlite3"
        try:
            link.symlink_to(valid_path)
        except OSError as exc:
            pytest.skip(f"This account cannot create a symlink: {exc}")
        assert client.get(f"/v1/backups/{link.name}").status_code == 400
        listed = {row["id"] for row in client.get("/v1/backups").json()["items"]}
        assert listed == {valid_path.name}


def test_backup_directory_symlink_cannot_redirect_restore_boundary(tmp_path):
    ctx = make_ctx(tmp_path / "app")
    try:
        seed_lead(ctx)
        path = storage.backup_database(ctx.config.db_path, tmp_path / "external", "manual")
        ctx.config.backup_dir.rmdir()
        try:
            ctx.config.backup_dir.symlink_to(path.parent, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"This account cannot create a directory symlink: {exc}")
        with client_for(ctx) as client:
            response = client.get(f"/v1/backups/{path.name}")
            assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_backup"
    finally:
        ctx.db.dispose()


def test_wal_dependent_candidate_is_not_a_self_contained_backup(ctx):
    lead_id = seed_lead(ctx)
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        path = ctx.config.backup_dir / inspection["backup"]["id"]
        with closing(sqlite3.connect(path)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("UPDATE leads SET memo='candidate WAL only'")
            writer.commit()
            assert Path(str(path) + "-wal").stat().st_size > 0
            checked = client.get(f"/v1/backups/{path.name}").json()
            assert checked["can_restore"] is False and "WAL" in checked["problem"]
            response = restore(client, checked)
            assert response.status_code == 400
    assert_current(ctx, lead_id, "백업할 메모")


@pytest.mark.parametrize("phase", ["prepare", "migrate"])
def test_stage_failure_leaves_live_data_and_backup_list_unchanged(ctx, monkeypatch, phase):
    lead_id = seed_lead(ctx)
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        before = set(ctx.config.backup_dir.iterdir())

        def fail_prepare(*args):
            raise OSError("fixture stage preparation failure")

        if phase == "prepare":
            monkeypatch.setattr(backups, "_prepare", fail_prepare)
        else:
            monkeypatch.setattr(storage, "migrate", fail_prepare)
        response = restore(client, inspection)
        assert response.status_code == 500 and response.json()["error"]["code"] == "restore_failed"
        assert set(ctx.config.backup_dir.iterdir()) == before and ctx.db.gate.generation == 0
    assert_current(ctx, lead_id, "백업할 메모")


def test_failure_after_publish_rolls_back_to_safety_and_reopens_sessions(ctx, monkeypatch):
    lead_id = seed_lead(ctx)
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        with ctx.db.session() as s:
            s.get(Lead, lead_id).memo = "복원 직전 유지할 메모"
        original = storage._replace_db
        calls = []

        def fail_after_first_publish(source, destination):
            calls.append(source)
            original(source, destination)
            if len(calls) == 1:
                raise OSError("fixture post-publication failure")

        monkeypatch.setattr(storage, "_replace_db", fail_after_first_publish)
        response = restore(client, inspection)
        assert response.status_code == 500 and response.json()["error"]["code"] == "restore_failed"
        assert len(calls) == 2 and "pre-restore" in calls[1].name
        assert ctx.db.gate.generation == 0
        assert client.get(f"/v1/leads/{lead_id}").json()["memo"] == "복원 직전 유지할 메모"
    assert_current(ctx, lead_id, "복원 직전 유지할 메모")
    assert not list(ctx.config.data_dir.glob("*.restore-part"))


def test_double_recovery_failure_halts_db_operations_and_retains_safety(ctx, monkeypatch):
    seed_lead(ctx)
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        original = storage._replace_db

        def fail_publish(*args):
            raise OSError("fixture storage unavailable")

        monkeypatch.setattr(storage, "_replace_db", fail_publish)
        response = restore(client, inspection)
        assert response.status_code == 500 and response.json()["error"]["code"] == "restore_recovery_failed"
        assert ctx.db.gate.generation == 0
        assert client.get("/v1/leads").status_code == 503
        assert client.post("/v1/backups").status_code == 503
        assert any("pre-restore" in p.name and storage.validate_database(p)[1] == 1
                   for p in ctx.config.backup_dir.glob("*.sqlite3"))
        monkeypatch.setattr(storage, "_replace_db", original)
        response = restore(client, inspection)
        assert response.status_code == 200, response.text
        assert ctx.db.gate.generation == 1
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/leads").status_code == 200
        assert client.get("/v1/leads").json()["items"][0]["has_memo"] is True


def test_held_api_session_causes_busy_without_backup_or_live_mutation(ctx, monkeypatch):
    lead_id = seed_lead(ctx)
    short_maintenance_timeout(ctx, monkeypatch)
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        before = set(ctx.config.backup_dir.iterdir())
        with ctx.db.session() as s:
            assert s.get(Lead, lead_id).memo == "백업할 메모"
            response = restore(client, inspection)
            assert response.status_code == 503 and response.json()["error"]["code"] == "maintenance_busy"
        assert set(ctx.config.backup_dir.iterdir()) == before and ctx.db.gate.generation == 0
        assert restore(client, inspection).status_code == 200, "Released sessions must not leave a gate reservation behind."


def test_session_reservation_can_be_released_on_another_thread(ctx):
    ticket = ctx.db.session()
    entered = threading.Event()
    failures = []

    def enter():
        try:
            session = ticket.__enter__()
            session.execute(select(1))
            entered.set()
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=enter)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and entered.is_set() and not failures
    ticket.__exit__(None, None, None)
    with ctx.db.gate.exclusive(timeout=0.03):
        with ctx.db.session() as s:
            assert s.scalar(select(1)) == 1


@pytest.mark.parametrize("actor", ["worker", "scheduler"])
def test_whole_worker_or_scheduler_pass_prevents_restore_during_non_db_work(ctx, monkeypatch, actor):
    seed_lead(ctx)
    short_maintenance_timeout(ctx, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def wait_in_operation(*args):
        entered.set()
        assert release.wait(timeout=2), "Fixture operation release timed out."

    failures = []
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        before = set(ctx.config.backup_dir.iterdir())
        if actor == "worker":
            with ctx.db.session() as s:
                runs.enqueue(s, "import", source_id="manual", input={"format": "text", "content": "fixture"}, exclusive=False)
            monkeypatch.setattr(worker_mod, "execute", wait_in_operation)
            worker = worker_mod.Worker(ctx, "test-worker", runs.INTERACTIVE_KINDS)
            target = worker.run_once
        else:
            scheduler = worker_mod.Scheduler(ctx)
            monkeypatch.setattr(worker_mod.runs, "recover_expired", wait_in_operation)
            monkeypatch.setattr(worker_mod, "schedule_due", lambda app: [])
            monkeypatch.setattr(worker_mod, "housekeeping", lambda app: scheduler.stop())
            target = scheduler.run

        def run_target():
            try:
                target()
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=run_target)
        thread.start()
        try:
            assert entered.wait(timeout=2)
            response = restore(client, inspection)
            assert response.status_code == 503 and response.json()["error"]["code"] == "maintenance_busy"
            assert set(ctx.config.backup_dir.iterdir()) == before and ctx.db.gate.generation == 0
        finally:
            release.set()
            thread.join(timeout=2)
        assert not thread.is_alive() and not failures
        assert restore(client, inspection).status_code == 200


def test_exclusive_maintenance_blocks_new_api_sessions(ctx):
    with client_for(ctx) as client, ctx.db.gate.exclusive():
        response = client.get("/v1/settings")
        assert response.status_code == 503 and response.json()["error"]["code"] == "maintenance_busy"


def test_read_only_validation_closes_connections_and_does_not_create_missing_db(ctx):
    missing = ctx.config.data_dir / "missing.sqlite3"
    assert storage.current_revision(missing) is None
    assert not storage.integrity_ok(missing) and not missing.exists()
    path = storage.backup_database(ctx.config.db_path, ctx.config.backup_dir, "closing-test")
    assert storage.current_revision(path) == storage.head_revision()
    assert storage.integrity_ok(path) and storage.validate_database(path)[1] == 0
    moved = path.with_suffix(".moved")
    path.rename(moved)  # Windows rejects this if validation leaves a connection open.
    moved.rename(path)


def freeze_budget_day(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(backups, "datetime", FrozenDatetime)
    monkeypatch.setattr(http_mod, "datetime", FrozenDatetime)


@pytest.mark.parametrize(("saved", "current", "expected"), [
    ((10, 300, 1), (300, 300, 2), (300, 300, 2)),
    ((300, 300, 2), (5, 900, 1), (300, 900, 2)),
])
def test_restore_preserves_current_host_policy_and_spent_requests(ctx, monkeypatch, saved, current, expected):
    freeze_budget_day(monkeypatch)
    seed_lead(ctx)
    host, window = "fixture.example.invalid", NOW.strftime("%Y-%m-%d")
    with ctx.db.session() as s:
        s.add(HostBudget(host=host, window=window, used=saved[0], limit=saved[1],
                         last_request_at=NOW + timedelta(hours=saved[2])))
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        candidate = ctx.config.backup_dir / inspection["backup"]["id"]
        candidate_digest = storage.file_digest(candidate)
        with ctx.db.session() as s:
            row = s.get(HostBudget, (host, window))
            row.used, row.limit, row.last_request_at = current[0], current[1], NOW + timedelta(hours=current[2])
        response = restore(client, inspection)
        assert response.status_code == 200, response.text
        with ctx.db.session() as s:
            row = s.get(HostBudget, (host, window))
            assert (row.used, row.limit, row.last_request_at) == (expected[0], expected[1], NOW + timedelta(hours=expected[2]))
        fetcher = http_mod.Fetcher(ctx.db, allowed_hosts={host}, user_agent="fixture", daily_budget=300, min_interval_s=0)
        try:
            if expected[0] >= expected[1]:
                with pytest.raises(http_mod.RunHalt) as exc:
                    fetcher._reserve_budget(host)  # Local reservation only; no network request is made.
                assert exc.value.code == "budget_exhausted"
            else:
                fetcher._reserve_budget(host)  # A larger current policy is preserved rather than replaced by the old limit.
                with ctx.db.session() as s:
                    row = s.get(HostBudget, (host, window))
                    assert (row.used, row.limit) == (expected[0] + 1, expected[1])
        finally:
            fetcher.close()
        assert storage.file_digest(candidate) == candidate_digest, "Merge only the staged copy, preserving the selected backup."
        safety = ctx.config.backup_dir / response.json()["safety_backup"]["id"]
        with closing(sqlite3.connect(safety)) as conn:
            assert conn.execute('SELECT used, "limit" FROM host_budgets WHERE host=? AND window=?', (host, window)).fetchone() == current[:2]


def test_restore_copies_current_only_hosts_without_merging_past_windows(ctx, monkeypatch):
    freeze_budget_day(monkeypatch)
    seed_lead(ctx)
    host, window = "new-host.example.invalid", NOW.strftime("%Y-%m-%d")
    past = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    with ctx.db.session() as s:
        s.add(HostBudget(host=host, window=past, used=7, limit=25, last_request_at=NOW - timedelta(days=1)))
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        with ctx.db.session() as s:
            s.add(HostBudget(host=host, window=window, used=300, limit=300, last_request_at=NOW))
            old = s.get(HostBudget, (host, past))
            old.used, old.limit = 24, 50
        response = restore(client, inspection)
        assert response.status_code == 200, response.text
        with ctx.db.session() as s:
            today = s.get(HostBudget, (host, window))
            assert (today.used, today.limit, today.last_request_at) == (300, 300, NOW)
            old = s.get(HostBudget, (host, past))
            assert (old.used, old.limit, old.last_request_at) == (7, 25, NOW - timedelta(days=1))
        fetcher = http_mod.Fetcher(ctx.db, allowed_hosts={host}, user_agent="fixture", daily_budget=300, min_interval_s=0)
        try:
            with pytest.raises(http_mod.RunHalt) as exc:
                fetcher._reserve_budget(host)
            assert exc.value.code == "budget_exhausted"
        finally:
            fetcher.close()
        with closing(sqlite3.connect(ctx.config.backup_dir / inspection["backup"]["id"])) as conn:
            assert conn.execute("SELECT COUNT(*) FROM host_budgets WHERE window=?", (window,)).fetchone()[0] == 0


def test_existing_stream_and_high_reconnect_cursor_reset_after_restore(ctx):
    lead_id = seed_lead(ctx)
    with client_for(ctx) as client:
        inspection = create_and_inspect(client)
        old_head = client.get("/v1/bootstrap").json()["event_seq"]
        assert client.patch(f"/v1/leads/{lead_id}", json={"memo": "복원 전 새 이벤트"}).status_code == 200
        high_head = client.get("/v1/bootstrap").json()["event_seq"]
        assert high_head > old_head
        route = next(route for route in client.app.routes if getattr(route, "path", "") == "/v1/events")

        async def receive():
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        def request():
            return Request({"type": "http", "method": "GET", "path": "/v1/events", "headers": [],
                            "query_string": b"", "app": client.app}, receive)

        def event(chunk):
            return json.loads(next(line[6:] for line in chunk.decode().splitlines() if line.startswith("data: ")))

        async def verify():
            response = await route.endpoint(request(), after_seq=high_head)
            iterator = response.body_iterator
            try:
                assert (await asyncio.wait_for(anext(iterator), 2)).startswith(b": connected")
                restored = restore(client, inspection)
                assert restored.status_code == 200, restored.text
                reset = event(await asyncio.wait_for(anext(iterator), 2))
                assert reset["type"] == "stream.reset" and reset["seq"] == old_head < high_head
                assert reset["id"].startswith("restore-")
                assert client.patch(f"/v1/leads/{lead_id}", json={"memo": "복원 후 새 이벤트"}).status_code == 200
                received = event(await asyncio.wait_for(anext(iterator), 2))
                assert received["seq"] > reset["seq"] and received["type"] == "lead.updated"
            finally:
                await iterator.aclose()
            reconnect = await route.endpoint(request(), after_seq=high_head + 100)
            iterator = reconnect.body_iterator
            try:
                reset = event(await asyncio.wait_for(anext(iterator), 2))
                assert reset["type"] == "stream.reset" and reset["seq"] < high_head + 100
            finally:
                await iterator.aclose()

        asyncio.run(verify())
