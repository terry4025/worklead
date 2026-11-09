"""관리되는 워커와 스케줄러 (같은 프로세스의 스레드).

- 수집 워커: 당근 전용과 기타 소스로 분리. 같은 소스 실행은 직렬이며 호스트 예산은 공유한다.
- 대화형 워커: 단일 리드 재확인·재분석·가져오기·내보내기 (사용자 요청이 긴 수집에 막히지 않게)
- 스케줄러: 앱이 켜져 있을 때만 동작. 놓친 주기는 현재 구간 1회로만 보충 (대량 요청 방지)
"""

from __future__ import annotations

import logging
import os
import threading

from sqlalchemy import delete, exists, or_, select

from ..context import AppContext
from ..db import MaintenanceBusy
from ..models import DiscoveryListing, Lead, Run, Snapshot, Source, SourcePolicy, SourceRecord
from . import events, runs
from .executors import execute
from .power import SleepGuard, wanted
from .schedule import RECHECK_EVERY, next_due, regional_due, scheduler_delay

log = logging.getLogger("worklead.worker")

class Worker(threading.Thread):
    def __init__(self, app: AppContext, name: str, kinds: tuple[str, ...], *,
                 source_ids: tuple[str, ...] | None = None,
                 exclude_source_ids: tuple[str, ...] = ()):
        super().__init__(name=name, daemon=True)
        self.app = app
        self.kinds = kinds
        self.source_ids = source_ids
        self.exclude_source_ids = exclude_source_ids
        self.owner = f"{name}-{os.getpid()}"
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run_once(self) -> bool:
        with self.app.db.gate.operation():
            return self._run_once()

    def _run_once(self) -> bool:
        run = runs.claim_next(self.app.db, self.owner, self.kinds, self.app.config.lease_seconds,
                              source_ids=self.source_ids, exclude_source_ids=self.exclude_source_ids)
        if run is None:
            return False
        self.app.hub.notify()
        log.info("run start run_id=%s kind=%s source_id=%s", run.id, run.kind, run.source_id)
        execute(self.app, run, self.owner)
        log.info("run end run_id=%s", run.id)
        return True

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                if not self.run_once():
                    self._halt.wait(self.app.config.worker_poll_seconds)
            except MaintenanceBusy:
                self._halt.wait(self.app.config.worker_poll_seconds)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                self._halt.wait(5)


def schedule_due(app: AppContext) -> list[str]:
    """지금 필요한 예약 실행을 만든다. 멱등 키로 같은 구간에 한 번만 생성된다."""
    now = app.clock()
    created: list[str] = []
    with app.db.session() as s:
        for source in s.scalars(select(Source).where(Source.kind == "site", Source.auto_collect_enabled.is_(True))):
            if getattr(app.adapters.get(source.id), 'browser_collection', False):
                continue  # Dedicated cafe owner schedules its serial browser queue.
            policy = s.get(SourcePolicy, source.id)
            if policy is None or policy.status not in ("allowed", "restricted") or source.stopped_reason:
                continue
            for kind in ("discovery", *RECHECK_EVERY):
                due = next_due(s, source, kind, now)
                if due is None or due > now:
                    continue
                seconds = (source.interval_minutes or 360) * 60 if kind == "discovery" else RECHECK_EVERY[kind].total_seconds()
                bucket = int(now.timestamp() // seconds)
                key = f"sched:{source.id}:{kind}:{bucket}"
                if kind == "discovery":
                    last = s.scalar(select(Run).where(Run.source_id == source.id, Run.kind == kind)
                                    .order_by(Run.created_at.desc()).limit(1))
                    if (last and last.state == "partial" and not last.error_code
                            and (last.result or {}).get("continuation_pending")):
                        key = f"continue:{source.id}:{last.id}"
                    elif (early := regional_due(last)) is not None and early <= now:
                        # A short retry can share the old five-minute bucket.
                        # Its finished predecessor identifies the retry once.
                        key = f"regional:{source.id}:{last.id}"
                try:
                    run, new = runs.enqueue(s, kind, source_id=source.id, trigger="schedule", idempotency_key=key)
                except runs.RunConflict:
                    continue
                if new:
                    created.append(run.id)
    if created:
        app.hub.notify()
    return created


def housekeeping(app: AppContext) -> None:
    """원문 보존 기간이 지난 본문 삭제, 이벤트 정리."""
    now = app.clock()
    changed = False
    with app.db.session() as s:
        # Drop stale, unselected public list metadata after 30 days. Retain
        # rows linked to stored records so their first observation stays stable.
        from datetime import timedelta
        linked = exists(select(SourceRecord.id).where(
            SourceRecord.source_id == DiscoveryListing.source_id,
            or_(SourceRecord.source_post_id == DiscoveryListing.source_post_id,
                SourceRecord.canonical_url == DiscoveryListing.url)))
        s.execute(delete(DiscoveryListing).where(
            DiscoveryListing.last_listed_at < now - timedelta(days=30), ~linked))
        for snap in s.scalars(select(Snapshot).where(Snapshot.retained_until < now, Snapshot.body_text.is_not(None)).limit(500)):
            snap.body_text = None
        # 원문 본문은 보존 기간 이후 제거하되, 사용자가 진행 중인 리드는 유지 (메모·영업 기록 판단 근거)
        q = (
            select(SourceRecord)
            .join(Lead, Lead.id == SourceRecord.lead_id)
            .where(SourceRecord.raw_retained_until < now, SourceRecord.body_text.is_not(None), Lead.sales_stage.in_(("new", "ignored", "lost")), or_(Lead.user_mark.is_(None), Lead.user_mark != "interested"))
            .limit(500)
        )
        for rec in s.scalars(q):
            rec.body_text = None
            lead = s.get(Lead, rec.lead_id)
            if lead is not None and lead.primary_record_id == rec.id:
                lead.search_text = rec.title.lower()
                events.emit(s, "lead.updated", {"lead_id": lead.id, "changes": ["body_retention"]}, dedupe_key=f"lead.retention:{rec.id}:{rec.raw_retained_until.isoformat()}")
                changed = True
        events.trim(s)
    if changed:
        app.hub.notify()
    # App-only morning summaries do not depend on Windows notification opt-in.
    from ..services.native_notifications import release_quiet_summary
    release_quiet_summary(app)


class Scheduler(threading.Thread):
    def __init__(self, app: AppContext):
        super().__init__(name="scheduler", daemon=True)
        self.app = app
        self._halt = threading.Event()
        self.sleep_guard = SleepGuard()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.is_set():
            wait_delay = self.app.config.scheduler_interval_seconds
            try:
                with self.app.db.gate.operation():
                    runs.recover_expired(self.app.db)
                    schedule_due(self.app)
                    self.sleep_guard.update(wanted(self.app))
                    housekeeping(self.app)
                    wait_delay = scheduler_delay(self.app)
            except MaintenanceBusy:
                self._halt.wait(self.app.config.worker_poll_seconds)
                continue
            except Exception:  # noqa: BLE001
                log.exception("scheduler error")
            self._halt.wait(wait_delay)
        self.sleep_guard.update(False)


class Workers:
    def __init__(self, app: AppContext):
        self.threads: list[threading.Thread] = [
            Worker(app, "collector-daangn", runs.COLLECTION_KINDS, source_ids=("daangn-alba",)),
            Worker(app, "collector-other", runs.COLLECTION_KINDS, exclude_source_ids=("daangn-alba",)),
            Worker(app, "interactive", runs.INTERACTIVE_KINDS),
            Scheduler(app),
        ]

    def start(self) -> None:
        for t in self.threads:
            t.start()

    def stop(self) -> None:
        for t in self.threads:
            t.stop()  # type: ignore[attr-defined]
        for t in self.threads:
            t.join(timeout=10)
