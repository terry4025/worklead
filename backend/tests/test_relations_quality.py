"""Non-destructive, symmetric candidate decisions on disposable databases."""
from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, select

from tests.conftest import client_for
from tests.test_source_fields import sample
from worklead import storage
from worklead.api import schemas as S
from worklead.engine.pipeline import _link_candidates, ingest
from worklead.models import DuplicateLink, EventOutbox, Lead, LeadActivity, SourceRecord, new_id
from worklead.services import relations

NOW = datetime(2026, 9, 30, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def seed_pair(ctx, body_b=None):
    results = []
    posts = []
    with ctx.db.session() as s:
        for index in range(2):
            post = sample()
            post.source_post_id = f"relation-{index}"
            post.original_url = post.canonical_url = f"https://jobs.daangn.com/job-posts/{post.source_post_id}"
            if index == 1 and body_b is not None:
                post.body = body_b
            results.append(ingest(s, "daangn-alba", post, parser_version="test", now=NOW))
            posts.append(post)
    return results, posts


def detail(c, lead_id):
    response = c.get(f"/v1/leads/{lead_id}")
    assert response.status_code == 200, response.text
    return response.json()


def act(c, lead_id, row, action, **overrides):
    payload = {"action": action, "expected_revision": row["revision"], "expected_generation": row["database_generation"], **overrides}
    return c.post(f"/v1/leads/{lead_id}/relations/{row['id']}", json=payload)


def assert_pair(c, ids, confirmed, dismissed, revision):
    rows = [detail(c, id_)["related"] for id_ in ids]
    assert all(len(group) == 1 for group in rows)
    a, b = rows[0][0], rows[1][0]
    assert a["id"] == b["id"]
    assert (a["lead_id"], b["lead_id"]) == (ids[1], ids[0])
    for row in (a, b):
        assert (row["confirmed"], row["dismissed"], row["revision"]) == (confirmed, dismissed, revision)
    return a, b


def state_counts(ctx):
    with ctx.db.session() as s:
        return (
            s.scalar(select(func.count()).select_from(Lead)),
            s.scalar(select(func.count()).select_from(SourceRecord)),
            s.scalar(select(func.count()).select_from(LeadActivity).where(LeadActivity.kind == "relation")),
            s.scalar(select(func.count()).select_from(EventOutbox).where(EventOutbox.type == "lead.updated")),
        )


def test_decisions_are_symmetric_repeatable_and_never_merge_user_data(ctx, client):
    results, _ = seed_pair(ctx)
    ids = [result.lead_id for result in results]
    for index, id_ in enumerate(ids):
        assert client.patch(f"/v1/leads/{id_}", json={"memo": f"별도 메모 {index}", "user_mark": "interested" if index == 0 else "dismissed", "sales_stage": "negotiating" if index == 0 else "won", "draft_text": f"별도 문의 {index}"}).status_code == 200
        assert client.post(f"/v1/leads/{id_}/outcomes", json={"kind": "contract_confirmed", "amount": 100000 + index, "occurred_on": "2026-09-30", "note": f"별도 계약 {index}"}).status_code == 200
    before = [detail(client, id_) for id_ in ids]
    counts = state_counts(ctx)
    a, b = assert_pair(client, ids, False, False, 0)
    confirmed = act(client, ids[0], a, "confirm")
    assert confirmed.status_code == 200, confirmed.text
    S.RelationUpdateOut.model_validate(confirmed.json())
    a, b = assert_pair(client, ids, True, False, 1)
    assert state_counts(ctx) == (counts[0], counts[1], counts[2] + 2, counts[3] + 2)
    repeated = act(client, ids[1], b, "confirm", expected_revision=0)
    assert repeated.status_code == 200
    assert state_counts(ctx) == (counts[0], counts[1], counts[2] + 2, counts[3] + 2)
    assert act(client, ids[1], b, "dismiss").status_code == 200
    a, b = assert_pair(client, ids, False, True, 2)
    rejected_stale = act(client, ids[0], a, "confirm", expected_revision=1)
    assert rejected_stale.status_code == 409 and rejected_stale.json()["error"]["code"] == "relation_changed"
    assert act(client, ids[0], a, "confirm").status_code == 200
    assert_pair(client, ids, True, False, 3)
    after = [detail(client, id_) for id_ in ids]
    for old, new in zip(before, after, strict=True):
        assert {k: v for k, v in old.items() if k not in {"related", "activity"}} == {k: v for k, v in new.items() if k not in {"related", "activity"}}


@pytest.mark.parametrize("changed", [False, True])
def test_dismissal_survives_recollection_and_candidate_relink(ctx, client, changed):
    results, posts = seed_pair(ctx)
    ids = [result.lead_id for result in results]
    a, _ = assert_pair(client, ids, False, False, 0)
    assert act(client, ids[0], a, "dismiss").status_code == 200
    with ctx.db.session() as s:
        for result, post in zip(results, posts, strict=True):
            if changed:
                post.body += " 결과물은 별도로 전달받겠습니다."
            refetched = ingest(s, "daangn-alba", post, parser_version="test", now=NOW + timedelta(hours=1))
            assert refetched.lead_id == result.lead_id
            rec = s.get(SourceRecord, result.record_id)
            _link_candidates(s, s.get(Lead, result.lead_id), rec, rec.content_hash)
        s.flush()
        assert s.scalar(select(func.count()).select_from(DuplicateLink)) == 1
    assert_pair(client, ids, False, True, 1)
    assert state_counts(ctx)[:3] == (2, 2, 2)


def test_legacy_reverse_rows_are_deduplicated_and_updated_together(ctx, client):
    results, _ = seed_pair(ctx)
    ids = [r.lead_id for r in results]
    with ctx.db.session() as s:
        s.add(DuplicateLink(id=new_id("dup"), lead_id=ids[0], source_record_id=results[1].record_id, relation="similar", basis="별도 후보 근거", confirmed=True))
    a, b = assert_pair(client, ids, True, False, 0)
    assert act(client, ids[1], b, "dismiss").status_code == 200
    assert_pair(client, ids, False, True, 1)
    with ctx.db.session() as s:
        rows = s.scalars(select(DuplicateLink)).all()
        assert len(rows) == 2
        assert all((row.confirmed, row.dismissed, row.revision) == (False, True, 1) for row in rows)
        assert {row.basis for row in rows} == {"별도 후보 근거", "본문·조건 동일 (원천 ID 다름) — 병합하지 않음"}


def test_new_repost_stays_separate_without_reviving_dismissed_pair(ctx, client):
    results, _ = seed_pair(ctx)
    ids = [r.lead_id for r in results]
    a, _ = assert_pair(client, ids, False, False, 0)
    assert act(client, ids[0], a, "dismiss").status_code == 200
    with ctx.db.session() as s:
        repost = sample()
        repost.source_post_id = "relation-new-repost"
        repost.original_url = repost.canonical_url = "https://jobs.daangn.com/job-posts/relation-new-repost"
        third = ingest(s, "daangn-alba", repost, parser_version="test", now=NOW + timedelta(hours=2)).lead_id
        assert third not in ids
    for id_ in ids:
        rows = detail(client, id_)["related"]
        prior = next(row for row in rows if row["id"] == a["id"])
        assert (prior["confirmed"], prior["dismissed"], prior["revision"]) == (False, True, 1)
    assert len(detail(client, third)["related"]) == 2
    assert all(not row["confirmed"] and not row["dismissed"] for row in detail(client, third)["related"])
    assert state_counts(ctx)[:3] == (3, 3, 2)


def test_same_title_alone_never_creates_a_relation(ctx, client):
    results, _ = seed_pair(ctx, body_b="창고 재고 정리, 현장 근무 인력을 모집합니다. 일급 15만원입니다.")
    for result in results:
        assert detail(client, result.lead_id)["related"] == []


@pytest.mark.parametrize("payload", [
    {"action": "merge", "expected_revision": 0, "expected_generation": 0},
    {"action": "confirm", "expected_revision": -1, "expected_generation": 0},
    {"action": "dismiss", "expected_revision": 0, "expected_generation": -1},
    {"action": "confirm", "expected_revision": 0},
    {"action": "confirm", "expected_revision": 0, "expected_generation": 0, "memo": "덮어쓰지 않음"},
])
def test_invalid_actions_are_rejected_without_mutation(ctx, client, payload):
    results, _ = seed_pair(ctx)
    ids = [r.lead_id for r in results]
    a, _ = assert_pair(client, ids, False, False, 0)
    counts = state_counts(ctx)
    response = client.post(f"/v1/leads/{ids[0]}/relations/{a['id']}", json=payload)
    assert response.status_code == 422
    assert state_counts(ctx) == counts


def test_link_id_is_authorized_by_both_endpoints_only(ctx, client):
    results, _ = seed_pair(ctx)
    ids = [r.lead_id for r in results]
    a, _ = assert_pair(client, ids, False, False, 0)
    with ctx.db.session() as s:
        post = sample()
        post.source_post_id = "unrelated"
        post.body = "현장 판매 직원 모집합니다."
        other = ingest(s, "daangn-alba", post, parser_version="test", now=NOW).lead_id
    counts = state_counts(ctx)
    assert act(client, other, a, "confirm").status_code == 404
    assert act(client, "missing", a, "confirm").status_code == 404
    assert act(client, ids[0], {**a, "id": "missing"}, "confirm").status_code == 404
    response = client.post(f"/v1/leads/{ids[0]}/relations/{a['id']}", headers={"Authorization": ""}, json={"action": "confirm", "expected_revision": 0, "expected_generation": 0})
    assert response.status_code == 401
    assert state_counts(ctx) == counts


def test_conflicting_simultaneous_decisions_use_compare_and_swap(ctx, client, monkeypatch):
    results, _ = seed_pair(ctx)
    ids = [r.lead_id for r in results]
    a, b = assert_pair(client, ids, False, False, 0)
    barrier = threading.Barrier(2)
    original = relations.decision

    def simultaneous(links):
        state = original(links)
        barrier.wait(timeout=5)
        return state

    monkeypatch.setattr(relations, "decision", simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(act, client, ids[0], a, "confirm"), pool.submit(act, client, ids[1], b, "dismiss")]
        responses = [request.result(timeout=10) for request in requests]
    assert sorted(response.status_code for response in responses) == [200, 409]
    monkeypatch.setattr(relations, "decision", original)
    final = detail(client, ids[0])["related"][0]
    assert final["revision"] == 1 and final["confirmed"] != final["dismissed"]
    assert_pair(client, ids, final["confirmed"], final["dismissed"], 1)
    assert state_counts(ctx)[2:] == (2, 2)


def test_restore_preserves_relation_decisions_and_rejects_old_screen_generation(ctx, client):
    results, _ = seed_pair(ctx)
    ids = [r.lead_id for r in results]
    a, _ = assert_pair(client, ids, False, False, 0)
    assert act(client, ids[0], a, "dismiss").status_code == 200
    a, _ = assert_pair(client, ids, False, True, 1)
    created = client.post("/v1/backups")
    assert created.status_code == 201
    inspected = client.get(f"/v1/backups/{created.json()['id']}").json()
    assert act(client, ids[0], a, "confirm").status_code == 200
    assert client.patch(f"/v1/leads/{ids[0]}", json={"memo": "복원 뒤 없어질 변경"}).status_code == 200
    restored = client.post(f"/v1/backups/{created.json()['id']}/restore", json={"confirm": True, "sha256": inspected["sha256"]})
    assert restored.status_code == 200, restored.text
    after, _ = assert_pair(client, ids, False, True, 1)
    assert after["database_generation"] == a["database_generation"] + 1
    counts = state_counts(ctx)
    # Matching revision cannot make a request from the old DB timeline safe.
    rejected = act(client, ids[0], a, "confirm")
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "relation_changed"
    assert state_counts(ctx) == counts
    assert act(client, ids[0], after, "confirm").status_code == 200
    assert_pair(client, ids, True, False, 2)
    assert detail(client, ids[0])["memo"] == ""


def test_sidecar_restart_rejects_prior_relation_generation_even_if_revision_matches(ctx, client):
    results, _ = seed_pair(ctx)
    ids = [r.lead_id for r in results]
    original, _ = assert_pair(client, ids, False, False, 0)
    assert 0 < original["database_generation"] < 2 ** 53
    with client_for(ctx) as restarted:
        fresh, _ = assert_pair(restarted, ids, False, False, 0)
        assert fresh["database_generation"] != original["database_generation"]
        counts = state_counts(ctx)
        response = act(restarted, ids[0], original, "confirm")
        assert response.status_code == 409 and response.json()["error"]["code"] == "relation_changed"
        assert state_counts(ctx) == counts
        assert act(restarted, ids[0], fresh, "confirm").status_code == 200


@pytest.mark.parametrize("revision", ["0001", "0002_detail_fetch_states", "0003_native_notifications"])
def test_older_backups_validate_migrate_and_keep_confirmed_relation(ctx, client, revision):
    results, _ = seed_pair(ctx)
    ids = [r.lead_id for r in results]
    a, _ = assert_pair(client, ids, False, False, 0)
    assert act(client, ids[0], a, "confirm").status_code == 200
    assert client.patch(f"/v1/leads/{ids[0]}", json={"memo": "이전 버전 메모"}).status_code == 200
    path = storage.backup_database(ctx.config.db_path, ctx.config.backup_dir, "older-relations-test")
    command.downgrade(storage.alembic_config(path), revision)
    assert storage.validate_database(path) == (revision, 2)
    with closing(sqlite3.connect(path)) as conn:
        assert "dismissed" not in {row[1] for row in conn.execute("PRAGMA table_info(duplicate_links)")}
    inspected = client.get(f"/v1/backups/{path.name}")
    assert inspected.status_code == 200 and inspected.json()["can_restore"] is True
    restored = client.post(f"/v1/backups/{path.name}/restore", json={"confirm": True, "sha256": inspected.json()["sha256"]})
    assert restored.status_code == 200, restored.text
    assert storage.current_revision(ctx.config.db_path) == "0010_ai_assist"
    assert_pair(client, ids, True, False, 0)
    assert detail(client, ids[0])["memo"] == "이전 버전 메모"


def test_new_revision_cannot_masquerade_as_valid_without_decision_columns(ctx):
    seed_pair(ctx)
    path = storage.backup_database(ctx.config.db_path, ctx.config.backup_dir, "missing-columns-test")
    command.downgrade(storage.alembic_config(path), "0003_native_notifications")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num='0004_relation_decisions'")
        conn.commit()
    with pytest.raises(ValueError, match="필수 컬럼"):
        storage.validate_database(path)


@pytest.mark.parametrize("fixture", ["relation-confirmed", "relation-dismissed"])
def test_new_actual_relation_contract_fixtures(fixture):
    payload = json.loads((ROOT / "contracts" / "fixtures" / f"{fixture}.json").read_text(encoding="utf-8"))
    parsed = S.RelationUpdateOut.model_validate(payload)
    a, b = parsed.lead.related[0], parsed.related_lead.related[0]
    assert a.id == b.id and (a.confirmed, a.dismissed, a.revision) == (b.confirmed, b.dismissed, b.revision)
    assert parsed.lead.id != parsed.related_lead.id
