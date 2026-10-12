"""Support archives exclude untrusted text, including legacy logs and exceptions."""
from datetime import UTC, datetime
import io
import json
import logging
from pathlib import Path
import sys
import zipfile

import pytest

from tests.conftest import client_for, make_ctx
from tests.test_api import drain
from worklead.engine.executors import RunScope
from worklead.models import Lead, Run, Source, UserProfile
from worklead.server import _Mask, _PrivateFormatter
from worklead.services.diagnostics import LOG_BYTES, summarize_log
from worklead.services.settings import put_setting

PRIVATE = ('SYNTHETIC_PRIVATE_NAME', 'SYNTHETIC_PRIVATE_TOKEN', 'SYNTHETIC_PRIVATE_SEARCH',
           '010-1234-5678', '02-1234-5678', '900101-1234567', 'private@example.test',
           '서울시 가상로 123번지', r'C:\Users\SYNTHETIC_PRIVATE_USER\private.txt')


def private_seed(ctx):
    with ctx.db.session() as s:
        put_setting(s, 'query_groups', {'version': 3, 'groups': [{'id': PRIVATE[0], 'label': PRIVATE[1], 'enabled': True, 'keywords': list(PRIVATE)}]})
        put_setting(s, 'ai', {'engine_label': PRIVATE[1], 'api_key': PRIVATE[1], 'monthly_cost_cap': PRIVATE[0]})
        put_setting(s, 'notifications', {'new_recommended': True, 'quiet_hours': {'enabled': True, 'start': PRIVATE[2], 'end': PRIVATE[3]}, 'private': PRIVATE[0]})
        s.add(Lead(id='private-lead', source_id='manual', title=PRIVATE[0], memo=' '.join(PRIVATE), draft_text=PRIVATE[2]))
        s.get(UserProfile, 1).data = {'skills': PRIVATE[2], 'private': list(PRIVATE)}
        source = s.get(Source, 'manual')
        source.name = PRIVATE[0]
        source.health_message = PRIVATE[7]
        source.health_code = PRIVATE[1]
        source.stopped_reason = PRIVATE[0]
        s.add(Run(id=PRIVATE[0], kind='import', state='failed', counts={'requests': 2, 'private': list(PRIVATE), 'created': PRIVATE[3]},
                  error_code=PRIVATE[1], error_message=' '.join(PRIVATE), checkpoint={'secret': PRIVATE[1]}, input={'private': PRIVATE[0]}, result={'path': PRIVATE[8]}))
    ctx.config.log_dir.mkdir(exist_ok=True)
    header = '2026-09-30 12:00:00,123 '
    (ctx.config.log_dir / 'backend.log').write_text(
        header + 'INFO worklead.http collector fetch host=' + PRIVATE[1] + ' status=200 outcome=ok ms=123\n'
        + header + 'ERROR worklead.executor collector run failed run_id=' + PRIVATE[0] + ' kind=import source_id=' + PRIVATE[2] + '\n'
        + 'Traceback\nValueError: ' + ' '.join(PRIVATE) + '\n', encoding='utf-8')
    (ctx.config.log_dir / 'backend.log.1').write_text(' '.join(PRIVATE), encoding='utf-8')
    (ctx.config.log_dir / (PRIVATE[0]+'.log')).write_text(' '.join(PRIVATE), encoding='utf-8')


def export(ctx):
    client = client_for(ctx)
    run_id = client.post('/v1/exports', json={'kind': 'diagnostics'}).json()['job_id']
    drain(ctx)
    return client.get(f'/v1/runs/{run_id}').json()


def archive_data(ctx, run):
    with zipfile.ZipFile(ctx.config.export_dir/run['result']['file_name']) as archive:
        assert archive.testzip() is None
        return {name: json.loads(archive.read(name)) for name in archive.namelist()}


def test_every_untrusted_text_path_is_excluded_and_metadata_is_useful(tmp_path):
    ctx = make_ctx(tmp_path)
    try:
        private_seed(ctx)
        run = export(ctx)
        assert run['state'] == 'succeeded' and run['progress']['done'] == run['progress']['total'] == 1
        data = archive_data(ctx, run)
        text = json.dumps(data, ensure_ascii=False)
        for marker in PRIVATE:
            assert marker not in text
        assert all(name.endswith('.json') for name in data)
        assert {'versions.json','settings.json','sources.json','runs.json','diagnostics.json'} <= data.keys()
        settings = data['settings.json']
        assert settings['query_groups'] == {'version':3,'group_count':1,'enabled_count':1,'keyword_counts':[len(PRIVATE)]}
        assert settings['notifications']['new_recommended'] is True
        assert settings['ai']['engine_configured'] is True
        logs = data['logs/log-01.json']
        assert logs['events'] == {'fetch':1,'run_failed':1}
        assert logs['http_statuses'] == {'200':1} and logs['http_latency_buckets'] == {'under_500ms':1}
        assert logs['text_lines_omitted'] == 2
        assert next(item for item in data['runs.json'] if item['kind']=='import')['counts']['requests'] == 2
        assert next(item for item in data['runs.json'] if item['kind']=='import')['counts']['created'] is None
        assert data['diagnostics.json']['raw_text_included'] is False
        assert not list(ctx.config.export_dir.glob('*.part'))
    finally: ctx.db.dispose()


@pytest.mark.parametrize('field,value', [('kind',PRIVATE[0]),('health_status',PRIVATE[1]),('health_code',PRIVATE[2])])
def test_invalid_metadata_enum_is_never_copied(tmp_path, field, value):
    ctx = make_ctx(tmp_path)
    try:
        with ctx.db.session() as s:
            setattr(s.get(Source,'manual'),field,value)
            put_setting(s,'recheck',{'ttl_hours':PRIVATE[3]})
            put_setting(s,'retention',{'raw_days':True})
        data = archive_data(ctx, export(ctx))
        assert value not in json.dumps(data)
        assert data['settings.json']['recheck']['ttl_hours'] is None
        assert data['settings.json']['retention']['raw_days'] is None
    finally: ctx.db.dispose()


def test_two_diagnostic_exports_in_same_second_preserve_both(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.clock = lambda: datetime(2026,9,30,tzinfo=UTC)
    try:
        first,second = export(ctx),export(ctx)
        assert first['result']['file_name'] != second['result']['file_name']
        assert archive_data(ctx,first)['versions.json'] == archive_data(ctx,second)['versions.json']
    finally: ctx.db.dispose()


def test_cancelled_diagnostic_never_publishes_partial_zip(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    original = RunScope.checkpoint
    calls = 0
    def cancel(scope, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            with ctx.db.session() as s: s.get(Run,scope.run_id).cancel_requested = True
        return original(scope,*args,**kwargs)
    monkeypatch.setattr(RunScope,'checkpoint',cancel)
    try:
        run = export(ctx)
        assert calls >= 4 and run['state'] == 'cancelled'
        assert 'file_name' not in run['result'] and not list(ctx.config.export_dir.iterdir())
    finally: ctx.db.dispose()


def test_write_failure_does_not_publish_or_retain_partial(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    def fail(*args,**kwargs): raise OSError(PRIVATE[1])
    monkeypatch.setattr(zipfile.ZipFile,'writestr',fail)
    try:
        run = export(ctx)
        assert run['state'] == 'failed' and 'file_name' not in run['result']
        assert not list(ctx.config.export_dir.iterdir())
    finally: ctx.db.dispose()


def test_large_legacy_log_is_bounded_and_only_valid_shapes_are_exported(tmp_path):
    path = tmp_path/'backend.log'
    path.write_bytes((PRIVATE[0]+'\n').encode()*30000 + b'2026-09-30 12:00:00,123 INFO worklead.http collector fetch host=example.test status=503 outcome=server_error ms=2100\n')
    result = summarize_log(path)
    assert result['truncated'] is True and result['tail_bytes_limit'] == LOG_BYTES
    assert result['http_outcomes'] == {'server_error':1} and result['http_latency_buckets'] == {'over_2000ms':1}
    assert PRIVATE[0] not in json.dumps(result)


@pytest.mark.parametrize('line', [
    '2026-99-30 12:00:00,123 INFO worklead.http collector fetch host=x status=200 outcome=ok ms=10',
    '2026-09-30 12:00:00,123 INFO worklead.http collector fetch host=x status=999 outcome=ok ms=10',
    '2026-09-30 12:00:00,123 INFO worklead.http collector fetch host=x status=200 outcome=SYNTHETIC_PRIVATE_TOKEN ms=10',
    '2026-09-30 12:00:00,123 INFO worklead.http collector fetch host=x status=200 outcome=ok ms=10 SYNTHETIC_PRIVATE_TOKEN',
])
def test_malformed_log_fields_are_not_mirrored(tmp_path,line):
    path=tmp_path/'backend.log'
    path.write_text(line,encoding='utf-8')
    result=summarize_log(path)
    assert result['events'] == {} and result['text_lines_omitted'] == 1
    assert PRIVATE[1] not in json.dumps(result)


def test_missing_and_directory_logs_report_only_fixed_state(tmp_path):
    assert summarize_log(tmp_path/PRIVATE[0]) == {'state':'unavailable'}
    assert summarize_log(tmp_path) == {'state':'skipped_nonregular'}


def test_symlink_log_is_not_followed(tmp_path):
    outside=tmp_path/'private'
    outside.write_text(PRIVATE[0],encoding='utf-8')
    link=tmp_path/'backend.log'
    try: link.symlink_to(outside)
    except OSError: pytest.skip('Symlink creation not available')
    assert summarize_log(link) == {'state':'skipped_nonregular'}


def test_formatter_omits_exception_args_paths_source_and_stale_cache():
    logger=logging.getLogger('worklead.privacy-probe')
    try: raise ValueError(' '.join(PRIVATE))
    except ValueError:
        record=logger.makeRecord(logger.name,logging.ERROR,PRIVATE[8],10,'run failed token=%s',(PRIVATE[1],),sys.exc_info())
    record.exc_text='ValueError: '+PRIVATE[0]
    formatter=_PrivateFormatter(_Mask([PRIVATE[1]]),'%(message)s')
    text=formatter.format(record)
    assert 'exception_type=ValueError' in text and '<library>' in text
    for marker in PRIVATE: assert marker not in text
    assert record.exc_text == 'ValueError: '+PRIVATE[0]  # Other handlers' cache is untouched.


def test_formatter_redacts_complete_message_and_omits_stack_source():
    record=logging.LogRecord('worklead',logging.ERROR,PRIVATE[8],10,' '.join(PRIVATE),(),None)
    record.stack_info=PRIVATE[0]
    text=_PrivateFormatter(_Mask([PRIVATE[0],PRIVATE[1],PRIVATE[2],PRIVATE[7],PRIVATE[8]])).format(record)
    assert 'Stack source text and absolute paths omitted' in text
    for marker in PRIVATE: assert marker not in text
