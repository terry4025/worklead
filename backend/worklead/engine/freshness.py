"""Bounded public-list watches and metadata, without changing HTTP policies."""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from datetime import timedelta
from dataclasses import replace

from sqlalchemy import func, or_, select
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
        DiscoveryListing.selection.in_(("core", "exploration")),
        DiscoveryListing.last_listed_at >= cutoff,
        or_(DiscoveryListing.source_updated_at.is_(None), DiscoveryListing.source_updated_at >= cutoff),
        ~select(DetailFetchState.ref_key).where(DetailFetchState.source_id == adapter.source_id,
            DetailFetchState.ref_key == DiscoveryListing.ref_key).exists(),
        ~select(SourceRecord.id).where(SourceRecord.source_id == adapter.source_id,
            or_(SourceRecord.source_post_id == DiscoveryListing.source_post_id,
                SourceRecord.canonical_url == DiscoveryListing.url)).exists(),
    ).order_by(DiscoveryListing.source_updated_at.desc(), DiscoveryListing.first_listed_at.desc(), DiscoveryListing.ref_key).limit(2000))
    core, exploration = [], []
    for row in rows:
        if not adapter._link_re.fullmatch(row.url): continue
        kind = adapter.saved_candidate_kind(row.title_hint or "", query)
        if kind is None: continue
        # A former optional candidate (e.g. monthly salary) is never promoted by
        # the fact that its public title disappeared from the current page.
        kind = "exploration" if row.selection == "exploration" else kind
        (core if kind == "core" else exploration).append(PostRef(row.url, row.source_post_id, row.title_hint, row.source_updated_at))
        if len(core) + len(exploration) >= limit: break
    return DiscoverResult(core, metadata={"saved_candidate_backlog": True, "backlog_candidates": len(core) + len(exploration)}, exploration_refs=exploration)


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
        rows = [{"source_id": source_id, "ref_key": key, "source_post_id": ref.source_post_id,
                 "url": ref.url, "title_hint": ref.title_hint,
                 "title_digest": hashlib.sha256((ref.title_hint or "").encode()).hexdigest(),
                 "first_listed_at": now, "last_listed_at": now,
                 "source_updated_at": ref.source_updated_at,
                 "selection": "core" if key in core else "exploration" if key in exploration else "filtered"}
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
    scopes = {t.region_scope for t in plan.tasks if t.region_scope.startswith("feed-")}
    cutoff = now - timedelta(days=int(adaptive["history_days"]))
    criteria = (
            CollectionTask.source_id == adapter.source_id,
            CollectionTask.region_scope.in_(scopes), CollectionTask.state == "done",
            CollectionTask.updated_at >= cutoff)
    rows = session.execute(select(CollectionTask.region_scope,
        func.sum(CollectionTask.checkpoint["new_core_candidates"].as_integer()),
        func.max(CollectionTask.updated_at)).where(*criteria,
        CollectionTask.checkpoint["new_core_candidates"].as_integer() > 0)
        .group_by(CollectionTask.region_scope)).all()
    ranked = [scope for scope, _, _ in sorted(rows, key=lambda row: (-row[1], -row[2].timestamp(), row[0]))]
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
    latest = {}
    for lead_id, scope, at in proven:
        if lead_id in owners: continue
        owners[lead_id] = scope
        latest[scope] = max(latest.get(scope, at), at)
    if owners:
        counts = Counter(owners.values())
        ranked = sorted(counts, key=lambda scope: (-counts[scope], -latest[scope].timestamp(), scope))
    if not ranked:
        observed = session.execute(select(CollectionTask.region_scope, func.max(CollectionTask.updated_at))
            .where(*criteria, CollectionTask.result_count > 0).group_by(CollectionTask.region_scope)).all()
        ranked = [scope for scope, _ in sorted(observed, key=lambda row: (-row[1].timestamp(), row[0]))]
    cap = int(adaptive["max_hot_anchors"])
    # A changing rank cannot turn every region into a fast poll. Eight
    # completed priority polls share one rolling ten-minute allowance.
    recent_fast = session.scalar(select(func.count()).select_from(CollectionTask).where(
        CollectionTask.source_id == adapter.source_id, CollectionTask.state == "done",
        CollectionTask.updated_at > now - timedelta(minutes=int(adaptive["hot_minutes"])),
        CollectionTask.checkpoint["priority_anchor"].as_boolean() == True)) or 0
    hot = set(ranked[:max(0, cap - recent_fast)])
    plan.tasks = [replace(t, min_revisit_minutes=min(t.min_revisit_minutes, int(adaptive["hot_minutes"])))
                  if t.region_scope in hot else t for t in plan.tasks]
    adapter._priority_anchor_scopes = hot
    basis = "원문 분석에서 확인한 제작 구매 수요" if owners else "신규 제작 제목 후보 관찰(원문 분석 전 초기 기준)"
    plan.notes.append(f"최근 {adaptive['history_days']}일 {basis}를 기준으로 최대 {adaptive['max_hot_anchors']}개 앵커를 {adaptive['hot_minutes']}분 우선 확인합니다. 현재 우선 앵커 {len(hot)}개이며, 부하·응답 시간 때문에 실제 확인 간격은 더 길 수 있습니다.")
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
    fresh = {kind: sorted([item for item in items if item[0] == kind and ref_key(item[1]) not in visits],
                          key=lambda item: (-item[1].priority, *ordering(item)))
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
