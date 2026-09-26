"""관리되는 워커와 스케줄러 (같은 프로세스의 스레드).

- 수집 워커: discovery / recheck_recent / recheck_stale (호스트 예산을 공유하므로 1개)
- 대화형 워커: 단일 리드 재확인·재분석·가져오기·내보내기 (사용자 요청이 긴 수집에 막히지 않게)
- 스케줄러: 앱이 켜져 있을 때만 동작. 놓친 주기는 현재 구간 1회로만 보충 (대량 요청 방지)
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import timedelta

from sqlalchemy import or_, select

from ..context import AppContext
from ..models import Lead, Run, Snapshot, Source, SourcePolicy, SourceRecord
from . import events, runs
from .executors import execute

log = logging.getLogger("worklead.worker")

RECHECK_EVERY = {"recheck_recent": timedelta(hours=6), "recheck_stale": timedelta(hours=24)}


class Worker(threading.Thread):
    def __init__(self, app: AppContext, name: str, kinds: tuple[str, ...]):
        super().__init__(name=name, daemon=True)
        self.app = app
        self.kinds = kinds
        self.owner = f"{name}-{os.getpid()}"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> bool:
        run = runs.claim_next(self.app.db, self.owner, self.kinds, self.app.config.lease_seconds)
        if run is None:
            return False
        self.app.hub.notify()
        log.info("run start run_id=%s kind=%s source_id=%s", run.id, run.kind, run.source_id)
        execute(self.app, run, self.owner)
        log.info("run end run_id=%s", run.id)
        return True

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(self.app.config.worker_poll_seconds)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                self._stop.wait(5)


def schedule_due(app: AppContext) -> list[str]:
    """지금 필요한 예약 실행을 만든다. 멱등 키로 같은 구간에 한 번만 생성된다."""
    now = app.clock()
    created: list[str] = []
    with app.db.session() as s:
        for source in s.scalars(select(Source).where(Source.kind == "site", Source.auto_collect_enabled.is_(True))):
            policy = s.get(SourcePolicy, source.id)
            if policy is None or policy.status not in ("allowed", "restricted") or source.stopped_reason:
                continue
            last = s.scalar(select(Run).where(Run.source_id == source.id, Run.kind == "discovery").order_by(Run.created_at.desc()).limit(1))
            if last is not None and last.retry_after and last.retry_after > now:
                continue  # 429 Retry-After 존중
            interval = timedelta(minutes=source.interval_minutes or 360)
            plans = [("discovery", interval)] + list(RECHECK_EVERY.items())
            for kind, every in plans:
                bucket = int(now.timestamp() // every.total_seconds())
                key = f"sched:{source.id}:{kind}:{bucket}"
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
    with app.db.session() as s:
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
        events.trim(s)


class Scheduler(threading.Thread):
    def __init__(self, app: AppContext):
        super().__init__(name="scheduler", daemon=True)
        self.app = app
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                runs.recover_expired(self.app.db)
                schedule_due(self.app)
                housekeeping(self.app)
            except Exception:  # noqa: BLE001
                log.exception("scheduler error")
            self._stop.wait(self.app.config.scheduler_interval_seconds)


class Workers:
    def __init__(self, app: AppContext):
        self.threads: list[threading.Thread] = [
            Worker(app, "collector", runs.COLLECTION_KINDS),
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
