"""Bounded public-list watches and metadata, without changing HTTP policies."""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import datetime, timedelta
from dataclasses import replace

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.sqlite import insert

from ..models import CollectionTask, DetailFetchState, DiscoveryListing, DiscoveryPath, Lead, Run, Source, SourceRecord
from ..sources.base import DiscoverResult, PostRef, TaskSpec
from ..sources.profiled import canonicalize


def ref_key(ref):
    identity = f"id:{ref.source_post_id}" if ref.source_post_id else f"url:{canonicalize(ref.url)}"
    return hashlib.sha256(identity.encode()).hexdigest()


SAVED_SCOPE = "saved-candidates"


def saved_candidates(session, adapter, query, now):
    """Drain never-attempted metadata even after a moving feed loses the post.

    Only previously selected, recent public links are eligible. Reapply the
    current enabled queries; existing records/failures retain their own refresh
    and backoff paths. This does not rediscover, widen, or refresh list evidence.
    """
    config = getattr(adapter, "profile", {}).get("discovery", {})
    limit = min(40, max(0, int(config.get("saved_candidate_limit", 0))))
    if not limit or not hasattr(adapter, "saved_candidate_kind"):
        return DiscoverResult([], metadata={"saved_candidate_backlog": True, "backlog_candidates": 0})
    cutoff = now - timedelta(hours=min(168, max(1, int(config.get("saved_candidate_max_hours", 168)))))
    rows = session.scalars(select(DiscoveryListing).where(
        DiscoveryListing.source_id == adapter.source_id,
        DiscoveryListing.selection.in_(("core", "exploration", "audit")),
        DiscoveryListing.last_listed_at >= cutoff,
        or_(DiscoveryListing.source_updated_at.is_(None), DiscoveryListing.source_updated_at >= cutoff),
        ~select(DetailFetchState.ref_key).where(DetailFetchState.source_id == adapter.source_id,
            DetailFetchState.ref_key == DiscoveryListing.ref_key).exists(),
        ~select(SourceRecord.id).where(SourceRecord.source_id == adapter.source_id,
            or_(SourceRecord.source_post_id == DiscoveryListing.source_post_id,
                SourceRecord.canonical_url == DiscoveryListing.url)).exists(),
    ).order_by(DiscoveryListing.source_updated_at.desc(), DiscoveryListing.first_listed_at.desc(), DiscoveryListing.ref_key).limit(2000))
    core, exploration, audit_ids = [], [], []
    for row in rows:
        if not adapter._link_re.fullmatch(row.url): continue
        audit = row.selection == "audit" and getattr(adapter, "title_audit_candidate", lambda _: False)(row.title_hint or "")
        kind = "exploration" if audit else adapter.saved_candidate_kind(row.title_hint or "", query)
        if kind is None: continue
        # A former optional candidate (e.g. monthly salary) is never promoted by
        # the fact that its public title disappeared from the current page.
        kind = "exploration" if row.selection == "exploration" else kind
        if audit:
            audit_ids.append(row.source_post_id)
        (core if kind == "core" else exploration).append(PostRef(row.url, row.source_post_id, row.title_hint, row.source_updated_at))
        if len(core) + len(exploration) >= limit: break
    return DiscoverResult(core, metadata={"saved_candidate_backlog": True, "backlog_candidates": len(core) + len(exploration),
                                         "title_audit_ids": audit_ids}, exploration_refs=exploration)


def append_saved_candidates(session, adapter, plan, now):
    query = next((t.query for t in plan.tasks if t.region_scope.startswith("feed-")), None)
    if query and (found := saved_candidates(session, adapter, query, now)).metadata["backlog_candidates"]:
        plan.tasks.append(TaskSpec("national-title-v1", SAVED_SCOPE, "저장된 미처리 후보 이어받기", query, min_revisit_minutes=5))
        plan.notes.append("최근 7일에 발견했지만 아직 상세를 확인하지 못한 후보는 목록에서 사라져도 신규 후보부터 기존 예산 안에서 이어서 확인합니다.")
    return plan


def observe_listings(app, source_id, result):
    """Preserve all public list entries, including title-filtered ones.

    Bulk statements are bounded; first observation survives title/URL changes.
    Metadata linked to a retained SourceRecord is kept by housekeeping.
    """
    if not result.listed_refs:
        return
    now = app.clock()
    core = {ref_key(r) for r in result.refs}
    exploration = {ref_key(r) for r in result.exploration_refs}
    unique = {ref_key(r): r for r in result.listed_refs}
    with app.db.session() as s:
        existing = set()
        keys = list(unique)
        for start in range(0, len(keys), 500):
            existing.update(s.scalars(select(DiscoveryListing.ref_key).where(
                DiscoveryListing.source_id == source_id, DiscoveryListing.ref_key.in_(keys[start:start + 500]))))
        # Count a listing's first observation once globally, even when adjacent
        # anchors overlap or a still-visible candidate is revisited repeatedly.
        result.metadata["new_core_candidates"] = len((core & unique.keys()) - existing)
        result.metadata["new_listed_entries"] = len(unique.keys() - existing)
        audit_ids = set(result.metadata.get("title_audit_ids", []))
        rows = [{"source_id": source_id, "ref_key": key, "source_post_id": ref.source_post_id,
                 "url": ref.url, "title_hint": ref.title_hint,
                 "title_digest": hashlib.sha256((ref.title_hint or "").encode()).hexdigest(),
                 "first_listed_at": now, "last_listed_at": now,
                 "source_updated_at": ref.source_updated_at,
                 "selection": "audit" if ref.source_post_id in audit_ids else "core" if key in core else "exploration" if key in exploration else "filtered"}
                for key, ref in unique.items()]
        # Existing detail timestamps cannot prove a prior list observation.
        # Keep listing rows referenced by records during retention instead.
        statement = insert(DiscoveryListing)
        statement = statement.on_conflict_do_update(
            index_elements=["source_id", "ref_key"],
            set_={name: getattr(statement.excluded, name) for name in (
                "source_post_id", "url", "title_hint", "title_digest", "last_listed_at", "source_updated_at", "selection")})
        for start in range(0, len(rows), 1000):
            # DBAPI executemany reuses one bounded SQL statement instead of
            # recompiling thousands of parameterized VALUES per watch.
            s.execute(statement, rows[start:start + 1000])
    result.metadata["listing_observed_at"] = now.isoformat()
    result.metadata["listing_digest"] = hashlib.sha256("\n".join(
        sorted(f"{key}:{hashlib.sha256((ref.title_hint or '').encode()).hexdigest()}" for key, ref in unique.items())).encode()).hexdigest()


def prioritize_feed_tasks(session, adapter, plan, now):
    """Bounded hot anchors, based on unique discoveries rather than duplicates.

    Legacy tasks have no first-observation counter. Only until a source has
    measured new core candidates, bootstrap from recent candidate observations;
    repeated result_count values are never summed into productivity.
    """
    config = getattr(adapter, "profile", {}).get("discovery", {}).get("feed", {})
    adaptive = config.get("adaptive")
    if not adaptive:
        return plan
    adapter._regional_normal_minutes = {t.region_scope: t.min_revisit_minutes for t in plan.tasks}
    scopes = {t.region_scope for t in plan.tasks if t.region_scope.startswith("feed-")}
    cutoff = now - timedelta(days=int(adaptive["history_days"]))
    criteria = (
            CollectionTask.source_id == adapter.source_id,
            CollectionTask.region_scope.in_(scopes), CollectionTask.state == "done",
            CollectionTask.updated_at >= cutoff)
    history = list(session.scalars(select(CollectionTask).where(*criteria, CollectionTask.depth == 0)))
    def weight(at):
        return 0.5 ** (max(0, (now - at).total_seconds()) / (7 * 86400))
    polls, gains, latest = defaultdict(float), defaultdict(float), {}
    for task in history:
        polls[task.region_scope] += weight(task.updated_at)
        gains[task.region_scope] += max(0, (task.checkpoint or {}).get("new_core_candidates", 0)) * weight(task.updated_at)
        latest[task.region_scope] = max(latest.get(task.region_scope, task.updated_at), task.updated_at)
    ranked = sorted((scope for scope in gains if gains[scope]),
                    key=lambda scope: (-gains[scope] / max(1, polls[scope]), -latest[scope].timestamp(), scope))
    # Title matches such as "school activity program" are not completed
    # software buying demand. Once we have classified deliveries, rank those
    # instead. One lead credits only its first observed feed, never all overlaps.
    proven = session.execute(select(SourceRecord.lead_id, DiscoveryPath.region_scope, DiscoveryPath.first_seen_at)
        .join(DiscoveryPath, DiscoveryPath.source_record_id == SourceRecord.id)
        .join(Lead, Lead.id == SourceRecord.lead_id)
        .where(SourceRecord.source_id == adapter.source_id, SourceRecord.first_seen_at >= cutoff,
            DiscoveryPath.region_scope.in_(scopes),
            Lead.recommendation.in_(("recommended", "needs_review")),
            Lead.demand_intent.in_(("buyer_project", "buyer_ongoing")), Lead.pay_unit == "project",
            or_(Lead.user_mark.is_(None), Lead.user_mark != "dismissed"))
        .order_by(DiscoveryPath.first_seen_at, DiscoveryPath.region_scope, SourceRecord.lead_id)).all()
    owners = {}
    proven_latest, deliveries = {}, defaultdict(float)
    for lead_id, scope, at in proven:
        if lead_id in owners: continue
        owners[lead_id] = scope
        proven_latest[scope] = max(proven_latest.get(scope, at), at)
        deliveries[scope] += weight(at)
    if owners:
        ranked = sorted(deliveries, key=lambda scope: (-deliveries[scope] / max(1, polls[scope]),
                                                      -proven_latest[scope].timestamp(), scope))
    if not ranked:
        observed = session.execute(select(CollectionTask.region_scope, func.max(CollectionTask.updated_at))
            .where(*criteria, CollectionTask.result_count > 0).group_by(CollectionTask.region_scope)).all()
        ranked = [scope for scope, _ in sorted(observed, key=lambda row: (-row[1].timestamp(), row[0]))]
    cap = int(adaptive["max_hot_anchors"])
    # A changing rank cannot turn every region into a fast poll. Eight
    # completed priority polls share one rolling ten-minute allowance.
    # A failed task can be adopted by another run rather than inserted again.
    # Counting only rows would lose its earlier accelerated HTTP attempts.
    # Charge every attempt of a recently completed priority task, conservatively
    # including a possible ordinary first attempt or interrupted reservation.
    recent_fast = session.scalar(select(func.sum(case(
        (CollectionTask.attempts > 0, CollectionTask.attempts), else_=1))).where(
        CollectionTask.source_id == adapter.source_id, CollectionTask.depth == 0,
        CollectionTask.state.in_(("done", "failed", "blocked")),
        CollectionTask.updated_at > now - timedelta(minutes=int(adaptive["hot_minutes"])),
        CollectionTask.checkpoint["priority_anchor"].as_boolean() == True)) or 0
    slots = max(0, cap - recent_fast)
    # Failures never erase a successful visit; their own timestamps enforce a
    # bounded retry/backoff. All accelerated reasons share the same eight-slot
    # allowance, including failed requests, so saturation cannot expand budget.
    latest_tasks = {}
    for task in session.scalars(select(CollectionTask).where(
            CollectionTask.source_id == adapter.source_id, CollectionTask.region_scope.in_(scopes),
            CollectionTask.depth == 0).order_by(CollectionTask.updated_at.desc(), CollectionTask.id.desc())):
        latest_tasks.setdefault(task.region_scope, task)
    adapter._regional_failure_counts = {scope: int((task.checkpoint or {}).get("feed_failure_count", 0))
                                        for scope, task in latest_tasks.items()}
    retries, excluded, saturated = [], set(), []
    for scope, task in latest_tasks.items():
        cp = task.checkpoint or {}
        if task.state in ("failed", "blocked"):
            try:
                retry_at = datetime.fromisoformat(cp["feed_retry_after"]) if cp.get("feed_retry_after") else None
                retry_at = retry_at if retry_at and retry_at.tzinfo is not None else None
            except (TypeError, ValueError):
                retry_at = None
            if retry_at and retry_at > now:
                excluded.add(scope)
            elif cp.get("feed_transient_retry"):
                retries.append(scope)
        elif (cp.get("feed_saturated") and cp.get("new_listed_entries", 0)
              and now - timedelta(hours=2) <= task.updated_at <= now - timedelta(minutes=max(5, int(adaptive.get("saturated_minutes", 5))))):
            saturated.append(scope)
    retry = set(retries[:min(2, slots)])
    excluded.update(set(retries) - retry)
    slots -= len(retry)
    saturated.sort(key=lambda scope: (-int((latest_tasks[scope].checkpoint or {}).get("new_listed_entries", 0)),
                                      latest_tasks[scope].updated_at, scope))
    saturation = set(saturated[:min(int(adaptive.get("max_saturated_anchors", 2)), slots)])
    slots -= len(saturation)
    hot = set(scope for scope in ranked if scope not in retry | saturation | excluded)
    hot = set(sorted(hot, key=ranked.index)[:slots])
    accelerated = retry | saturation | hot
    plan.tasks = [replace(t, min_revisit_minutes=0 if t.region_scope in retry else
                         max(5, int(adaptive.get("saturated_minutes", 5))) if t.region_scope in saturation else
                         min(t.min_revisit_minutes, int(adaptive["hot_minutes"])))
                  if t.region_scope in accelerated else t for t in plan.tasks if t.region_scope not in excluded]
    adapter._priority_anchor_scopes = accelerated
    adapter._saturated_anchor_scopes = saturation
    basis = "원문 분석에서 확인한 제작 구매 수요" if owners else "신규 제작 제목 후보 관찰(원문 분석 전 초기 기준)"
    plan.notes.append(f"최근 {adaptive['history_days']}일 {basis}의 최근 가중·조회당 성과로 우선 확인합니다. 포화·일시 오류 재시도·일반 우선 확인은 {adaptive['hot_minutes']}분당 최대 {cap}회 한도를 공유합니다. 현재 우선 앵커 {len(accelerated)}개이며 실제 간격은 부하에 따라 길어질 수 있습니다.")
    return plan


def ordered_candidates(app, source_id, result):
    """New core first, one weak slot after at most three core candidates.

    Tried candidates rotate oldest-first; a cached core backlog cannot precede
    a never-tried weak request. Actual fetch quotas are enforced separately.
    """
    items = [("core", r) for r in result.refs] + [("exploration", r) for r in result.exploration_refs]
    candidate_keys = [ref_key(ref) for _, ref in items]
    first_listed = {}
    with app.db.session() as s:
        for start in range(0, len(candidate_keys), 500):
            first_listed.update(s.execute(select(DiscoveryListing.ref_key, DiscoveryListing.first_listed_at).where(
                DiscoveryListing.source_id == source_id,
                DiscoveryListing.ref_key.in_(candidate_keys[start:start + 500]))).all())
        visits = {row.ref_key: row.attempted_at for row in s.scalars(
            select(DetailFetchState).where(DetailFetchState.source_id == source_id))}
        for post_id, url, checked in s.execute(select(
                SourceRecord.source_post_id, SourceRecord.canonical_url, SourceRecord.last_checked_at
        ).where(SourceRecord.source_id == source_id)):
            key = ref_key(PostRef(url or "", post_id))
            if key not in visits and checked:
                visits[key] = checked
    def ordering(item):
        kind, ref = item
        key = ref_key(ref)
        return (visits[key].timestamp() if key in visits else
                -first_listed[key].timestamp() if key in first_listed else 0,
                -ref.source_updated_at.timestamp() if ref.source_updated_at else 0, key)
    audit_ids = set(result.metadata.get("title_audit_ids", []))
    def fresh_ordering(item):
        ref = item[1]
        # Auditing samples use oldest unseen observations, independently of
        # frequent normal candidates; a moving feed cannot pin the same sample.
        if ref.source_post_id in audit_ids:
            return (1, first_listed[ref_key(ref)].timestamp() if ref_key(ref) in first_listed else 0,
                    -ref.source_updated_at.timestamp() if ref.source_updated_at else 0, ref_key(ref))
        return (0, -ref.priority, *ordering(item))
    fresh = {kind: sorted([item for item in items if item[0] == kind and ref_key(item[1]) not in visits],
                          key=fresh_ordering)
             for kind in ("core", "exploration")}
    ordered = []
    while fresh["core"] or fresh["exploration"]:
        ordered.extend(fresh["core"][:3])
        del fresh["core"][:3]
        if fresh["exploration"]:
            ordered.append(fresh["exploration"].pop(0))
    ordered.extend(sorted([item for item in items if ref_key(item[1]) in visits], key=ordering))
    return [(kind, ref, ref_key(ref) in visits) for kind, ref in ordered]


def watch_reserve(session, app, source_id, adapter):
    """Reserve remaining UTC-day list polls before optional detail/rechecks.

    Uses actual observed map count; robots and retries still consume the same
    host budget. This reserves requests, not a promise about upstream freshness.
    """
    if not getattr(adapter, "profile", {}).get("discovery", {}).get("detail_requests_per_watch"):
        return 0
    source = session.get(Source, source_id)
    if source is None or not source.auto_collect_enabled:
        return 0
    now = app.clock()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if hasattr(adapter, "daily_list_requests"):
        # Tiered list plans declare their own daily request volume.
        interval = max(5, source.interval_minutes or 30)
        if hasattr(adapter, "remaining_list_requests"):
            return adapter.remaining_list_requests((tomorrow - now).total_seconds(), interval)
        daily = adapter.daily_list_requests(interval)
        return math.ceil(daily * (tomorrow - now).total_seconds() / 86400)
    # A newly queued watch has no metadata yet. Find the latest actual list
    # observation rather than under-reserving after a multi-map discovery.
    maps = 1
    for last in session.scalars(select(Run).where(Run.source_id == source_id, Run.kind == "discovery")
                               .order_by(Run.created_at.desc()).limit(20)):
        observed_maps = (last.result or {}).get("discovery", {}).get("sitemaps")
        if observed_maps is not None:
            maps = max(1, int(observed_maps))
            break
    interval = max(30, source.interval_minutes or 30) * 60
    return max(0, math.ceil((tomorrow - now).total_seconds() / interval) - 1) * (maps + 1)


GAP_CATCH_UP_MINUTES = 60


def catch_up_after_gap(session, source_id, plan, now):
    """A 60+ minute pause means the feeds' latest-20 window lost posts; the sitemap still lists them."""
    if not any(t.region_scope == "nationwide-sitemap" for t in plan.tasks):
        return plan
    last = session.scalar(select(func.max(Run.finished_at)).where(
        Run.source_id == source_id, Run.kind == "discovery", Run.finished_at.is_not(None)))
    if last is None or now - last <= timedelta(minutes=GAP_CATCH_UP_MINUTES):
        return plan
    plan.tasks = [replace(t, min_revisit_minutes=0) if t.region_scope == "nationwide-sitemap" else t for t in plan.tasks]
    plan.notes.append(f"수집이 {int((now - last).total_seconds() // 60)}분 멈췄다가 다시 시작해 전국 공고 목록을 바로 확인합니다.")
    return plan
