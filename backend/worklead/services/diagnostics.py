"""A bounded support archive from typed metadata; free text is never copied."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable
import zipfile

from sqlalchemy import select

from ..config import APP_VERSION, CONTRACT_VERSION
from ..context import AppContext
from ..models import Run, Source
from .settings import get_setting

COUNT_KEYS = ('requests', 'details_fetched', 'created', 'updated', 'duplicates', 'excluded',
              'skipped', 'parse_failures', 'fetch_failures', 'policy_stops', 'ai_failures')
RUN_KINDS = {'discovery', 'recheck_recent', 'recheck_stale', 'recheck_lead', 'reanalyze_lead', 'reanalyze_all', 'import', 'export'}
RUN_STATES = {'queued', 'running', 'paused', 'succeeded', 'partial', 'failed', 'cancelled'}
HEALTH = {'ok', 'degraded', 'blocked', 'error', 'paused', 'unknown'}
# Error values are extensible in the DB. New codes require review before export.
ERROR_CODES = {'detail_errors', 'parse_failed', 'fetch_failed', 'policy_stop', 'permission_pending', 'source_stopped',
               'robots_disallowed', 'robots_unavailable', 'rate_limited', 'budget_exhausted', 'unsupported',
               'not_found', 'source_not_ready', 'internal_error', 'invalid_input', 'cancelled', 'collection_disallowed',
               'scheme_not_allowed', 'host_not_allowed', 'login_required', 'http_403', 'captcha_or_block_page',
               'robots_unreachable', 'too_many_redirects', 'unknown_source', 'policy_not_allowed', 'tasks_failed',
               'recheck_errors', 'import_invalid', 'unknown_kind', 'network_error', 'gone', 'server_error', 'client_error', 'too_large'}
OUTCOMES = {'ok', 'network_error', 'not_found', 'gone', 'server_error', 'client_error', 'too_large', 'blocked', 'rate_limited'}
LOG_BYTES = 256 * 1024
LOG_FILES = ('backend.log', 'backend.log.1', 'backend.log.2', 'backend.log.3', 'backend.log.4', 'backend.log.5')
HEADER = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (DEBUG|INFO|WARNING|ERROR|CRITICAL) (\S+) \S+ (.*)$')
FETCH = re.compile(r'^fetch host=\S+ status=(None|[0-9]{3}) outcome=([a-z_]+) ms=([0-9]{1,9})$')


def _dict(value: Any) -> dict:
    return value if type(value) is dict else {}


def _number(value: Any, maximum: int = 1_000_000_000) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def _boolean(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _enum(value: Any, values: set[str]) -> str:
    return value if type(value) is str and value in values else 'unknown'


def _code(value: Any) -> str | None:
    return None if value is None else _enum(value, ERROR_CODES)


def safe_settings(s) -> dict[str, Any]:
    values = {key: _dict(get_setting(s, key)) for key in ('recheck', 'notifications', 'ai', 'retention', 'query_groups')}
    notifications, ai, groups = values['notifications'], values['ai'], values['query_groups']
    group_rows = groups.get('groups') if type(groups.get('groups')) is list else []
    return {
        'recheck': {'ttl_hours': _number(values['recheck'].get('ttl_hours'), 8760)},
        'notifications': {key: _boolean(notifications.get(key)) for key in ('new_recommended', 'meaningful_change', 'source_issue')}
            | {'quiet_hours_enabled': _boolean(_dict(notifications.get('quiet_hours')).get('enabled'))},
        'ai': {key: _boolean(ai.get(key)) for key in ('enabled', 'external_transfer_consent')}
            | {'engine_configured': bool(ai.get('engine_label')), 'monthly_cap_configured': ai.get('monthly_cost_cap') is not None,
               'daily_cap_configured': ai.get('daily_cost_cap') is not None},
        'retention': {'raw_days': _number(values['retention'].get('raw_days'), 3650)},
        'query_groups': {'version': _number(groups.get('version')), 'group_count': len(group_rows),
                         'enabled_count': sum(_dict(group).get('enabled') is True for group in group_rows),
                         'keyword_counts': [len(_dict(group).get('keywords', [])) if type(_dict(group).get('keywords')) is list else 0 for group in group_rows[:100]]},
    }


def _is_plain(path: Path, *, directory: bool = False) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        return False
    return stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)


def summarize_log(path: Path) -> dict[str, Any]:
    """Only aggregate recognized event shapes; filenames, text and IDs stay local."""
    try:
        if not _is_plain(path):
            return {'state': 'skipped_nonregular'}
        # no-follow where the OS supports it; the archive never receives raw bytes.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return {'state': 'skipped_nonregular'}
            start = max(0, info.st_size - LOG_BYTES)
            stream.seek(start)
            raw = stream.read(LOG_BYTES)
        lines = raw.decode('utf-8', errors='replace').splitlines()
        if start and lines:
            lines = lines[1:]  # The first record might be cut at the byte boundary.
    except OSError:
        return {'state': 'unavailable'}
    levels, events, outcomes, statuses, latency = Counter(), Counter(), Counter(), Counter(), Counter()
    omitted = 0
    for line in lines:
        match = HEADER.fullmatch(line)
        if not match:
            omitted += 1
            continue
        timestamp, level, logger, message = match.groups()
        try:
            datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S,%f')
        except ValueError:
            omitted += 1
            continue
        levels[level] += 1
        fetch = FETCH.fullmatch(message) if logger == 'worklead.http' else None
        if fetch and fetch[2] in OUTCOMES:
            status, outcome, elapsed = fetch.groups()
            if status != 'None' and not 100 <= int(status) <= 599:
                omitted += 1
                continue
            events['fetch'] += 1
            outcomes[outcome] += 1
            statuses[status if status != 'None' else 'unavailable'] += 1
            millis = int(elapsed)
            latency['under_500ms' if millis < 500 else 'under_2000ms' if millis < 2000 else 'over_2000ms'] += 1
            continue
        event = None
        if logger == 'worklead.worker':
            if message.startswith('run start run_id='): event = 'run_start'
            elif message.startswith('run end run_id='): event = 'run_end'
            elif message == 'worker loop error': event = 'worker_loop_error'
            elif message == 'scheduler error': event = 'scheduler_error'
        elif logger == 'worklead.executor':
            if message.startswith('run failed run_id='): event = 'run_failed'
            elif message.startswith('detail parse failed source_id='): event = 'detail_parse_failed'
            elif message.startswith('detail skipped source_id='): event = 'detail_skipped'
        elif logger == 'worklead':
            if re.fullmatch(r'backend ready port=[0-9]{1,5} mode=(live|demo)', message): event = 'backend_ready'
            elif message == 'stdin closed — shutting down': event = 'shutdown'
            elif message == 'startup failed': event = 'startup_failed'
        if event:
            events[event] += 1
        else:
            omitted += 1
    return {'state': 'summarized', 'tail_bytes_limit': LOG_BYTES, 'truncated': start > 0,
            'sampled_lines': len(lines), 'levels': dict(levels), 'events': dict(events),
            'http_outcomes': dict(outcomes), 'http_statuses': dict(statuses), 'http_latency_buckets': dict(latency),
            'text_lines_omitted': omitted}


def write_diagnostics(app: AppContext, target: Path, checkpoint: Callable[[], None], complete: Callable[[], None]) -> None:
    temporary = target.with_suffix(target.suffix + '.part')
    try:
        with app.db.session() as s:
            s.connection().exec_driver_sql('BEGIN')
            settings = safe_settings(s)
            sources = [{'id': f'source_{i}', 'kind': _enum(source.kind, {'site', 'manual'}),
                        'health': _enum(source.health_status, HEALTH), 'code': _code(source.health_code),
                        'stopped': bool(source.stopped_reason)} for i, source in enumerate(s.scalars(select(Source).order_by(Source.id)), 1)]
            recent = [{'id': f'run_{i}', 'kind': _enum(run.kind, RUN_KINDS), 'state': _enum(run.state, RUN_STATES),
                       'error': _code(run.error_code), 'counts': {key: _number(_dict(run.counts).get(key, 0)) for key in COUNT_KEYS}}
                      for i, run in enumerate(s.scalars(select(Run).order_by(Run.created_at.desc(), Run.id.desc()).limit(50)), 1)]
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, value in (('versions.json', {'app': APP_VERSION, 'contract': CONTRACT_VERSION}),
                                ('settings.json', settings), ('sources.json', sources), ('runs.json', recent),
                                ('diagnostics.json', {'format_version': 2, 'raw_text_included': False, 'ids': 'per-export aliases',
                                  'logs': 'recognized event aggregates only', 'excluded': ['lead content', 'notes', 'profile', 'search terms', 'paths', 'tokens', 'raw logs', 'exception text']})):
                checkpoint()
                archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))
            try:
                log_dir_plain = _is_plain(app.config.log_dir, directory=True)
            except OSError:
                log_dir_plain = False
            for i, name in enumerate(LOG_FILES, 1):
                checkpoint()
                if log_dir_plain:
                    summary = summarize_log(app.config.log_dir / name)
                else:
                    summary = {'state': 'unavailable'}
                archive.writestr(f'logs/log-{i:02d}.json', json.dumps(summary, ensure_ascii=False, indent=2))
        complete()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
