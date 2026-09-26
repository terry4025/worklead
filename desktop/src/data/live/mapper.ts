/**
 * 계약(snake_case, contracts/client) → 화면 모델(camelCase) 변환.
 * 계약 필드가 바뀌면 여기서 타입 오류가 난다 (화면 코드는 그대로).
 */
import type * as C from '@contracts/index';
import type {
  Bootstrap,
  Evidence,
  EvidenceKey,
  FuzzyDate,
  LeadDetail,
  LeadFilter,
  LeadSummary,
  RunInfo,
  SalesSummary,
  SettingsView,
  SourceInfo,
  WorkleadEvent,
} from '../../domain/model';

type S = C.Schemas;

const fuzzy = (d: S['FuzzyDate']): FuzzyDate => ({ at: d.at, precision: d.precision, raw: d.raw });

export function mapSummary(x: C.LeadSummary): LeadSummary {
  return {
    id: x.id,
    sourceId: x.source_id,
    title: x.title,
    categories: x.categories,
    demandIntent: x.demand_intent,
    engagementType: x.engagement_type,
    workMode: x.work_mode,
    collaborationMode: x.collaboration_mode,
    applicantScope: x.applicant_scope,
    pay: { raw: x.pay.raw, currency: x.pay.currency, min: x.pay.min, max: x.pay.max, unit: x.pay.unit, negotiable: x.pay.negotiable },
    sourceStatus: x.source_status,
    accessStatus: x.access_status,
    analysisStatus: x.analysis_status,
    recheckDue: x.recheck_due,
    lastCheckedAt: x.last_checked_at,
    published: fuzzy(x.published),
    firstSeenAt: x.first_seen_at,
    deadline: fuzzy(x.deadline),
    postedRegion: x.posted_region,
    foundIn: x.found_in,
    recommendation: x.recommendation,
    priority: { total: x.priority.total, unknownFactors: x.priority.unknown_factors },
    reasons: x.reasons,
    userMark: x.user_mark,
    salesStage: x.sales_stage,
    hasMemo: x.has_memo,
    activeJob: x.active_job,
  };
}

const EVIDENCE_KEYS: Record<string, EvidenceKey> = {
  demand_intent: 'demandIntent',
  engagement_type: 'engagementType',
  work_mode: 'workMode',
  collaboration_mode: 'collaborationMode',
  applicant_scope: 'applicantScope',
  pay: 'pay',
  source_status: 'sourceStatus',
  deadline: 'deadline',
};

function mapEvidence(e: S['Evidence']): Evidence {
  return {
    id: e.id,
    quote: e.quote,
    span: e.span ? { start: e.span.start, end: e.span.end } : null,
    basis: e.basis,
    confidence: e.confidence,
    observedAt: e.observed_at,
    sourceRecordId: e.source_record_id,
    version: e.version,
    note: e.note,
  };
}

export function mapDetail(x: C.LeadDetail): LeadDetail {
  const evidence: LeadDetail['evidence'] = {};
  for (const [k, list] of Object.entries(x.evidence)) {
    const key = EVIDENCE_KEYS[k];
    if (key) evidence[key] = list.map(mapEvidence);
  }
  const p = x.profitability;
  return {
    ...mapSummary(x),
    bodyText: x.body_text,
    bodyRetainedUntil: x.body_retained_until,
    originalUrl: x.original_url,
    contactChannel: x.contact_channel,
    workplace: x.workplace,
    applicantRegion: x.applicant_region,
    sourceUpdated: fuzzy(x.source_updated),
    lastSeenAt: x.last_seen_at,
    workPeriod: { start: fuzzy(x.work_period.start), end: fuzzy(x.work_period.end) },
    evidence,
    score: x.score
      ? {
          total: x.score.total,
          factors: x.score.factors.map((f) => ({ key: f.key, label: f.label, max: f.max, score: f.score, reason: f.reason })),
          riskPenalty: { score: x.score.risk_penalty.score, reasons: x.score.risk_penalty.reasons },
          ruleVersion: x.score.rule_version,
        }
      : null,
    analysis: {
      status: x.analysis.status,
      summary: x.analysis.summary,
      summaryEngine: x.analysis.summary_engine,
      fit: x.analysis.fit,
      unfit: x.analysis.unfit,
      uncertain: x.analysis.uncertain,
      deliverables: x.analysis.deliverables,
      techRequirements: x.analysis.tech_requirements,
      questions: x.analysis.questions,
      nextAction: x.analysis.next_action,
      conversionOpportunity: x.analysis.conversion_opportunity,
      engine: x.analysis.engine,
      version: x.analysis.version,
      analyzedAt: x.analysis.analyzed_at,
      failure: x.analysis.failure,
    },
    risks: x.risks.map((r) => ({ id: r.id, label: r.label, quote: r.quote, span: r.span ? { start: r.span.start, end: r.span.end } : null })),
    profitability: p
      ? {
          status: p.status,
          basis: p.basis,
          reason: p.reason,
          assumptions: p.assumptions,
          scenarios: p.scenarios.map((sc) => ({
            key: sc.key,
            contractValue: sc.contract_value,
            directCost: sc.direct_cost,
            hours: sc.hours,
            contribution: sc.contribution,
            effectiveHourly: sc.effective_hourly,
            residualAfterTarget: sc.residual_after_target,
          })),
          revenueType: p.revenue_type,
          targetHourly: p.target_hourly,
        }
      : null,
    draft: x.draft ? { text: x.draft.text, generatedAt: x.draft.generated_at, editedByUser: x.draft.edited_by_user } : null,
    quickMessage: x.quick_message,
    memo: x.memo,
    outcomes: x.outcomes.map((o) => ({
      id: o.id,
      kind: o.kind,
      amount: o.amount,
      currency: o.currency,
      occurredOn: o.occurred_on,
      note: o.note,
      evidenceRef: o.evidence_ref,
      recordedAt: o.recorded_at,
    })),
    related: x.related.map((r) => ({ id: r.id, leadId: r.lead_id, sourceId: r.source_id, title: r.title, url: r.url, relation: r.relation, basis: r.basis, seenAt: r.seen_at })),
    discoveryPaths: x.discovery_paths.map((d) => ({
      regionScope: d.region_scope,
      regionLabel: d.region_label,
      queryGroup: d.query_group,
      firstSeenAt: d.first_seen_at,
      lastSeenAt: d.last_seen_at,
      timesSeen: d.times_seen,
    })),
    activity: x.activity.map((a) => ({ at: a.at, text: a.text })),
    feedback: { remote: x.feedback.remote, realRequest: x.feedback.real_request },
    parserVersion: x.parser_version,
  };
}

export function mapSource(x: C.SourceOut): SourceInfo {
  const cov = x.coverage;
  return {
    id: x.id,
    name: x.name,
    kind: x.kind,
    scopeNote: x.scope_note,
    adapterVersion: x.adapter_version,
    policy: {
      status: x.policy.status,
      basis: x.policy.basis,
      reviewedAt: x.policy.reviewed_at,
      note: x.policy.note,
      robotsStatus: x.policy.robots_status,
      robotsSummary: x.policy.robots_summary,
    },
    autoCollect: { enabled: x.auto_collect.enabled, intervalMinutes: x.auto_collect.interval_minutes },
    health: { status: x.health.status, checkedAt: x.health.checked_at, message: x.health.message, code: x.health.code },
    stoppedReason: x.stopped_reason,
    capabilities: x.capabilities.map((c) => ({ key: c.key, label: c.label, support: c.support, note: c.note })),
    coverage: cov
      ? {
          targetLabel: cov.target_label,
          regionListStatus: cov.region_list_status,
          unitLabel: cov.unit_label,
          planned: cov.planned,
          completed: cov.completed,
          pending: cov.pending,
          blocked: cov.blocked,
          failed: cov.failed,
          scanCycle: cov.scan_cycle,
          lastVisitedAt: cov.last_visited_at,
          nextUp: cov.next_up,
          depthLimit: cov.depth_limit,
          budget: cov.budget ? { used: cov.budget.used, limit: cov.budget.limit, unitLabel: cov.budget.unit_label } : null,
          marketCoverage: cov.market_coverage,
          targetUnitsTotal: cov.target_units_total,
          targetUnitsCovered: cov.target_units_covered,
          estimatedCycleDays: cov.estimated_cycle_days,
          notes: cov.notes,
        }
      : null,
    research: { ready: x.research.ready, missing: x.research.missing },
    lastRunId: x.last_run_id,
  };
}

export function mapRun(x: C.RunOut): RunInfo {
  return {
    id: x.id,
    sourceId: x.source_id,
    leadId: x.lead_id,
    kind: x.kind,
    state: x.state,
    trigger: x.trigger,
    startedAt: x.started_at,
    finishedAt: x.finished_at,
    progress: { done: x.progress.done, total: x.progress.total, label: x.progress.label },
    counts: {
      requests: x.counts.requests,
      detailsFetched: x.counts.details_fetched,
      created: x.counts.created,
      updated: x.counts.updated,
      duplicates: x.counts.duplicates,
      excluded: x.counts.excluded,
      parseFailures: x.counts.parse_failures,
      fetchFailures: x.counts.fetch_failures,
      policyStops: x.counts.policy_stops,
      aiFailures: x.counts.ai_failures,
    },
    error: x.error ? { code: x.error.code, message: x.error.message, retryAfter: x.error.retry_after } : null,
    note: x.note,
    scanCycle: x.scan_cycle,
    result: x.result,
  };
}

export function mapSettings(x: C.SettingsOut): SettingsView {
  return {
    profile: {
      services: x.profile.services,
      skills: x.profile.skills,
      excludedWork: x.profile.excluded_work,
      minContract: x.profile.min_contract,
      targetHourly: x.profile.target_hourly,
      weeklyHours: x.profile.weekly_hours,
      onsite: x.profile.onsite,
      allowShortTermEmployment: x.profile.allow_short_term_employment,
      gigOnly: x.profile.gig_only ?? true,
      intro: x.profile.intro ?? '',
      portfolioUrl: x.profile.portfolio_url ?? null,
    },
    recheck: { ttlHours: x.recheck.ttl_hours ?? 24 },
    notifications: {
      newRecommended: x.notifications.new_recommended,
      meaningfulChange: x.notifications.meaningful_change,
      sourceIssue: x.notifications.source_issue,
      quietHours: { enabled: x.notifications.quiet_hours.enabled, start: x.notifications.quiet_hours.start, end: x.notifications.quiet_hours.end },
    },
    ai: {
      enabled: x.ai.enabled,
      externalTransferConsent: x.ai.external_transfer_consent,
      engineLabel: x.ai.engine_label,
      monthlyCostCap: x.ai.monthly_cost_cap,
      dailyCostCap: x.ai.daily_cost_cap,
      keyConfigured: x.ai.key_configured,
      providerAvailable: x.ai.provider_available,
    },
    retention: { rawDays: x.retention.raw_days ?? 30 },
    queryGroups: { version: x.query_groups.version, groups: x.query_groups.groups.map((g) => ({ id: g.id, label: g.label, enabled: g.enabled, keywords: g.keywords })) },
  };
}

export function settingsPatchToContract(p: Parameters<import('../port').WorkleadPort['updateSettings']>[0]): C.SettingsPatch {
  const out: C.SettingsPatch = {};
  if (p.profile)
    out.profile = {
      services: p.profile.services,
      skills: p.profile.skills,
      excluded_work: p.profile.excludedWork,
      min_contract: p.profile.minContract,
      target_hourly: p.profile.targetHourly,
      weekly_hours: p.profile.weeklyHours,
      onsite: p.profile.onsite,
      allow_short_term_employment: p.profile.allowShortTermEmployment,
      gig_only: p.profile.gigOnly,
      intro: p.profile.intro,
      portfolio_url: p.profile.portfolioUrl || null,
    };
  if (p.recheck) out.recheck = { ttl_hours: p.recheck.ttlHours };
  if (p.notifications)
    out.notifications = {
      new_recommended: p.notifications.newRecommended,
      meaningful_change: p.notifications.meaningfulChange,
      source_issue: p.notifications.sourceIssue,
      quiet_hours: p.notifications.quietHours,
    };
  if (p.retention) out.retention = { raw_days: p.retention.rawDays };
  if (p.ai) {
    const ai: Record<string, boolean | number | null> = {};
    if (p.ai.enabled !== undefined) ai.enabled = p.ai.enabled;
    if (p.ai.externalTransferConsent !== undefined) ai.external_transfer_consent = p.ai.externalTransferConsent;
    if (p.ai.monthlyCostCap !== undefined) ai.monthly_cost_cap = p.ai.monthlyCostCap;
    if (p.ai.dailyCostCap !== undefined) ai.daily_cost_cap = p.ai.dailyCostCap;
    out.ai = ai as C.SettingsPatch['ai'];
  }
  if (p.queryGroups) out.query_groups = p.queryGroups;
  return out;
}

export function mapBootstrap(x: C.BootstrapOut): Bootstrap {
  return {
    mode: x.mode,
    appVersion: x.app_version,
    contractVersion: x.contract_version,
    categories: x.categories,
    queues: x.queues,
    recheckTtlHours: x.recheck_ttl_hours,
    eventSeq: x.event_seq,
  };
}

export function mapSales(m: C.MetricsOut): SalesSummary {
  const s = m.sales;
  return {
    periodDays: s.period_days,
    reviewed: s.reviewed,
    contacted: s.contacted,
    negotiating: s.negotiating,
    won: s.won,
    contractsConfirmed: s.contracts_confirmed,
    contractAmount: s.contract_amount,
    collected: s.collected,
    refunded: s.refunded,
    directCost: s.direct_cost,
  };
}

export function filterToQuery(f: LeadFilter): C.LeadQuery {
  return {
    keyword: f.keyword.trim() || undefined,
    source_id: f.sourceId ?? undefined,
    category: f.category ?? undefined,
    remote: f.remote,
    recruit: f.recruit,
    intent: f.intent,
    sort: f.sort,
  };
}

export function mapEvent(e: C.ApiEvent): WorkleadEvent | null {
  const base = { id: e.id, seq: e.seq, at: e.at };
  const str = (k: string) => (typeof e[k] === 'string' ? (e[k] as string) : null);
  switch (e.type) {
    case 'run.progress': {
      const p = (e.progress ?? {}) as { done?: number; total?: number | null; label?: string | null };
      return { ...base, type: e.type, runId: str('run_id') ?? '', sourceId: str('source_id'), progress: { done: p.done ?? 0, total: p.total ?? null, label: p.label ?? null } };
    }
    case 'run.state_changed':
      return { ...base, type: e.type, runId: str('run_id') ?? '', sourceId: str('source_id'), state: e.state as RunInfo['state'] };
    case 'lead.created':
    case 'lead.updated':
      return { ...base, type: e.type, leadId: str('lead_id') ?? '' };
    case 'analysis.completed':
      return { ...base, type: e.type, leadId: str('lead_id') ?? '', status: e.status as LeadSummary['analysisStatus'] };
    case 'source.health_changed':
      return { ...base, type: e.type, sourceId: str('source_id') ?? '', health: e.health as SourceInfo['health']['status'] };
    case 'notification.created':
      return {
        ...base,
        type: e.type,
        kind: e.kind as 'new_lead' | 'lead_changed' | 'source_issue',
        title: str('title') ?? '',
        leadId: str('lead_id'),
        sourceId: str('source_id'),
        suppressedReason: str('suppressed_reason'),
      };
    case 'stream.reset':
      return { ...base, type: e.type };
    default:
      return null;
  }
}
