"""Only proven pre-permit failures may be retried; no evidence is relaxed."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from alembic import command
import pytest
from sqlalchemy import select

from tests.test_job_auto_apply import ACCOUNT, NOW, add, app, claimed, connect, old_record, ready
from worklead import storage
from worklead.jobs.auto_apply import AutoApplyRuntime, DIAGNOSTIC_LIMIT, RETRY_DELAYS, runtime
from worklead.models import JobAutoAttempt, Lead, Run, SourceRecord


def hold(client, body, reason="browser_timeout"):
    result=client.post('/review',json={**body,'outcome':'held','reason_code':reason})
    assert result.status_code==200,result.text
    assert result.json()['status']=='held'


def test_transient_retry_backoff_is_durable_bounded_and_rotates_each_lease(app):
    add(app);bridge,client,_=connect(app);clock=[NOW];app.clock=lambda:clock[0]
    try:
        body=claimed(client);attempt=body['attempt_id'];leases={body['lease_token']}
        for count,delay in enumerate(RETRY_DELAYS):
            hold(client,body)
            item=bridge.status().attempts[0]
            assert item.retry_count==count and item.retry_after==clock[0]+timedelta(seconds=delay)
            assert bridge.status().retry_waiting==1
            assert claimed(client) is None
            clock[0]+=timedelta(seconds=delay-1);assert claimed(client) is None
            clock[0]+=timedelta(seconds=1)
            previous=body;body=claimed(client)
            assert body['attempt_id']==attempt and body['lease_token'] not in leases
            leases.add(body['lease_token'])
            assert client.post('/review',json={**previous,'outcome':'ready','reason_code':'ready','evidence':'application_form'}).status_code==409
            assert bridge.status().attempts[0].retry_count==count+1
        hold(client,body)
        item=bridge.status().attempts[0]
        assert item.reason_code=='retry_exhausted' and item.retry_after is None and item.retry_count==3
        clock[0]+=timedelta(days=1);assert claimed(client) is None
        assert not bridge._journal.exists()
        with app.db.session() as s:assert len(list(s.scalars(select(JobAutoAttempt))))==1
    finally:client.close()


@pytest.mark.parametrize('reason',['questions_required','ui_unrecognized','profile_changed','condition_conflict','cancelled','closed','login_required','account_changed'])
def test_nontransient_ui_or_user_conditions_never_gain_a_retry(app,reason):
    add(app);bridge,client,_=connect(app)
    try:
        body=claimed(client);hold(client,body,reason)
        app.clock=lambda:NOW+timedelta(hours=1)
        assert claimed(client) is None
        item=bridge.status().attempts[0]
        assert item.reason_code==reason and item.retry_after is None and item.retry_count==0
    finally:client.close()


@pytest.mark.parametrize('boundary',['journal','permitted','uncertain','submitted','submitting','already_applied'])
def test_submission_boundary_blocks_retry_even_if_retry_metadata_is_present(app,boundary):
    add(app);bridge,client,_=connect(app)
    try:
        body=claimed(client);hold(client,body,'navigation_failed')
        with app.db.session() as s:
            row=s.get(JobAutoAttempt,body['attempt_id'])
            if boundary=='journal':bridge._remember_permit(row.job_key)
            elif boundary=='permitted':row.permitted_at=NOW
            else:row.state=boundary
        app.clock=lambda:NOW+timedelta(seconds=61)
        assert claimed(client) is None
        with app.db.session() as s:assert s.get(JobAutoAttempt,body['attempt_id']).retry_count==0
    finally:client.close()


def test_prepermit_process_restart_preserves_backoff_and_discards_old_authority(app):
    add(app);bridge,client,_=connect(app);body=claimed(client)
    # This is process shutdown, not an explicit stop/cancel.
    bridge.shutdown();client.close()
    item=bridge.status().attempts[0]
    assert item.reason_code=='connection_ended' and item.retry_after==NOW+timedelta(seconds=60)
    restarted=AutoApplyRuntime(app);app._jobs_auto_apply_runtime=restarted
    bridge,client,_=connect(app)
    try:
        assert claimed(client) is None
        app.clock=lambda:NOW+timedelta(seconds=60)
        replacement=claimed(client)
        assert replacement['attempt_id']==body['attempt_id'] and replacement['lease_token']!=body['lease_token']
        assert bridge.status().attempts[0].retry_count==1
        assert client.post('/permit',json=body).status_code==409
    finally:client.close()


def test_explicit_stop_and_account_pause_do_not_schedule_retries(app):
    add(app);bridge,client,_=connect(app);claimed(client)
    bridge.stop();client.close()
    item=bridge.status().attempts[0]
    assert item.reason_code=='cancelled' and item.retry_after is None
    with app.db.session() as s:s.delete(s.get(JobAutoAttempt,item.id))
    bridge,client,_=connect(app)
    try:
        claimed(client)
        assert client.post('/pause',json={'reason':'account_changed'}).status_code==200
        item=bridge.status().attempts[0]
        assert item.reason_code=='account_changed' and item.retry_after is None
    finally:client.close()


def test_corrupt_journal_stops_retries_instead_of_discarding_history(app):
    add(app);bridge,client,_=connect(app)
    try:
        body=claimed(client);hold(client,body)
        bridge._journal.write_text('corrupt',encoding='ascii')
        app.clock=lambda:NOW+timedelta(seconds=61)
        response=client.post('/next',json={'account_hash':ACCOUNT})
        assert response.status_code==503 and response.json()['error']=='journal_unavailable'
        with app.db.session() as s:assert s.get(JobAutoAttempt,body['attempt_id']).retry_count==0
    finally:
        bridge._journal.unlink(missing_ok=True);client.close()


def test_simultaneous_due_requests_claim_one_lease_for_one_unique_job(app):
    add(app);bridge,client,_=connect(app)
    try:
        body=claimed(client);hold(client,body)
        app.clock=lambda:NOW+timedelta(seconds=61)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:client.post('/next',json={'account_hash':ACCOUNT}).json(),range(3)))
        assert sum(result['job'] is not None for result in results)==1
        assert bridge.status().attempts[0].retry_count==1
        assert bridge.status().today_reserved==0
    finally:client.close()


def test_retry_rechecks_changed_conditions_and_does_not_bypass_daily_limit(app):
    first=add(app);second=add(app,key='ABCDEFGHIJK2');bridge,client,_=connect(app,daily_limit=1)
    try:
        body=claimed(client);hold(client,body)
        other=claimed(client);ready(client,other)
        assert client.post('/permit',json=other).json()['permit']
        app.clock=lambda:NOW+timedelta(seconds=61)
        assert claimed(client) is None
        assert bridge.status().queue_reason=='daily_limit'
        with app.db.session() as s:assert s.get(JobAutoAttempt,body['attempt_id']).retry_count==0
    finally:client.close()


def test_migration_accepts_0008_backup_and_preserves_unique_attempt(app):
    add(app);bridge,client,_=connect(app)
    body=claimed(client);hold(client,body,'cancelled');bridge.stop();client.close();app.db.dispose()
    command.downgrade(storage.alembic_config(app.config.db_path),'0008_job_auto_attempts')
    assert storage.validate_database(app.config.db_path)[0]=='0008_job_auto_attempts'
    storage.migrate(app.config.db_path,app.config.backup_dir)
    assert storage.validate_database(app.config.db_path)[0]=='0010_ai_assist'
    with app.db.session() as s:
        rows=list(s.scalars(select(JobAutoAttempt)))
        assert len(rows)==1 and rows[0].id==body['attempt_id'] and rows[0].retry_count==0 and rows[0].retry_after is None


def test_diagnostics_explain_rejections_refresh_states_and_first_connection_timeout(app):
    old=old_record(app);rejected=add(app,key='ABCDEFGHIJK2')
    with app.db.session() as s:s.get(Lead,rejected).user_mark='dismissed'
    bridge,client,_=connect(app)
    try:
        started=bridge._started;bridge.monotonic=lambda:started+91
        status=bridge.status()
        assert status.queue_reason=='connection_stale' and status.needs_user_action
        by_id={item.lead_id:item for item in status.candidates}
        assert by_id[old].stage=='source_refresh' and by_id[old].reason_code=='source_refresh_due'
        assert by_id[rejected].stage=='rejected' and by_id[rejected].reason_code=='condition_changed'
        assert claimed(client) is None
        # Drop diagnostics cache after changing queued worker evidence.
        bridge._diagnostic_cache=None
        item=next(item for item in bridge.status().candidates if item.lead_id==old)
        assert item.refresh_state=='queued' and item.reason_code=='refresh_queued'
        assert item.last_checked_at==NOW-timedelta(days=45)
        with app.db.session() as s:
            run=s.scalar(select(Run).where(Run.lead_id==old));run.state='failed';run.error_code='request_failed';run.finished_at=NOW
        bridge._diagnostic_cache=None
        item=next(item for item in bridge.status().candidates if item.lead_id==old)
        assert item.refresh_state=='failed' and item.refresh_error_code=='request_failed'
        assert bridge.status().needs_user_action
    finally:client.close()


def test_diagnostics_cache_bounds_analysis_and_never_queues_network_work(app,monkeypatch):
    old_record(app)
    from worklead.jobs import auto_apply
    original=auto_apply.condition;calls=[]
    def counted(*args,**kwargs):calls.append(args[1].id);return original(*args,**kwargs)
    monkeypatch.setattr(auto_apply,'condition',counted)
    bridge=runtime(app)
    first=bridge.status();initial=len(calls)
    assert 0<initial<=DIAGNOSTIC_LIMIT
    for _ in range(5):bridge.status()
    assert len(calls)==initial and first.candidates[0].stage=='source_refresh'
    with app.db.session() as s:assert list(s.scalars(select(Run)))==[]


def test_old_public_fresh_job_can_retry_pre_form_failure_but_still_needs_current_form(app):
    old_record(app,fresh=True);bridge,client,_=connect(app)
    try:
        body=claimed(client);hold(client,body,'navigation_failed')
        assert bridge.status().attempts[0].retry_after==NOW+timedelta(seconds=60)
        assert bridge.status().attempts[0].reason_code=='navigation_failed'
        app.clock=lambda:NOW+timedelta(seconds=60)
        replacement=claimed(client)
        assert replacement is not None and replacement['lease_token']!=body['lease_token']
        assert client.post('/permit',json=replacement).status_code==409
        # A retry does not replace the visible saved-form evidence requirement.
        result=client.post('/review',json={**replacement,'outcome':'ready','reason_code':'ready'})
        assert result.json()['status']=='held'
        assert bridge.status().attempts[0].reason_code=='source_stale'
    finally:client.close()


def test_due_retry_with_stale_terms_shows_existing_public_refresh_stage(app):
    lead_id=add(app);bridge,client,_=connect(app)
    try:
        body=claimed(client);hold(client,body)
        with app.db.session() as s:
            rec=s.get(SourceRecord,s.get(Lead,lead_id).primary_record_id)
            rec.last_checked_at=NOW-timedelta(days=2)
        app.clock=lambda:NOW+timedelta(seconds=60)
        assert claimed(client) is None
        candidate=next(item for item in bridge.status().candidates if item.lead_id==lead_id)
        assert candidate.stage=='source_refresh' and candidate.refresh_state=='queued'
        assert candidate.reason_code=='refresh_queued' and candidate.retry_count==0
        assert bridge.status().queue_reason=='source_refresh'
    finally:client.close()


def test_0008_transient_held_migrates_into_one_initial_backoff_without_resetting_it(app):
    add(app);bridge,client,_=connect(app);body=claimed(client)
    hold(client,body,'cancelled');bridge.stop();client.close();app.db.dispose()
    command.downgrade(storage.alembic_config(app.config.db_path),'0008_job_auto_attempts')
    import sqlite3
    with sqlite3.connect(app.config.db_path) as db:
        db.execute("update job_auto_attempts set reason_code='navigation_failed' where id=?",(body['attempt_id'],))
    storage.migrate(app.config.db_path,app.config.backup_dir)
    app._jobs_auto_apply_runtime=AutoApplyRuntime(app)
    item=app._jobs_auto_apply_runtime.status().attempts[0]
    assert item.state=='held' and item.retry_count==0 and item.retry_after==NOW+timedelta(seconds=60)
    app.clock=lambda:NOW+timedelta(seconds=30)
    app._jobs_auto_apply_runtime=AutoApplyRuntime(app)
    assert app._jobs_auto_apply_runtime.status().attempts[0].retry_after==NOW+timedelta(seconds=60)
    bridge,client,_=connect(app)
    try:
        assert claimed(client) is None
        app.clock=lambda:NOW+timedelta(seconds=60)
        replacement=claimed(client)
        assert replacement['attempt_id']==body['attempt_id'] and replacement['lease_token']!=body['lease_token']
        assert bridge.status().attempts[0].retry_count==1
    finally:client.close()


@pytest.mark.parametrize('boundary',['ui_unrecognized','questions_required','cancelled','uncertain','permitted','journal','already_scheduled'])
def test_legacy_adoption_excludes_uncertain_manual_and_submission_boundaries(app,boundary):
    add(app);bridge,client,_=connect(app);body=claimed(client);hold(client,body,'cancelled')
    try:
        with app.db.session() as s:
            row=s.get(JobAutoAttempt,body['attempt_id'])
            row.reason_code='connection_ended'
            if boundary in ('ui_unrecognized','questions_required','cancelled'):row.reason_code=boundary
            elif boundary=='uncertain':row.state='uncertain'
            elif boundary=='permitted':row.permitted_at=NOW
            elif boundary=='journal':bridge._remember_permit(row.job_key)
            elif boundary=='already_scheduled':row.retry_after=NOW+timedelta(seconds=900)
        bridge._recover()
        with app.db.session() as s:
            row=s.get(JobAutoAttempt,body['attempt_id'])
            assert row.retry_count==0
            assert row.retry_after==(NOW+timedelta(seconds=900) if boundary=='already_scheduled' else None)
    finally:client.close()


def test_corrupt_journal_cannot_adopt_a_legacy_held_attempt(app):
    add(app);bridge,client,_=connect(app);body=claimed(client);hold(client,body,'cancelled')
    try:
        with app.db.session() as s:s.get(JobAutoAttempt,body['attempt_id']).reason_code='lease_expired'
        bridge._journal.write_text('invalid journal',encoding='ascii')
        from worklead.jobs.applications import ApplicationError
        with pytest.raises(ApplicationError) as caught:bridge._recover()
        assert caught.value.code=='journal_unavailable'
        with app.db.session() as s:assert s.get(JobAutoAttempt,body['attempt_id']).retry_after is None
    finally:bridge._journal.unlink(missing_ok=True);client.close()
