import type { LeadDetail, LeadSummary } from './model';

/** 상세에서 목록 행에 필요한 필드만 남긴다 */
export function toSummary(d: LeadDetail): LeadSummary {
  const {
    bodyText: _b,
    bodyRetainedUntil: _br,
    originalUrl: _o,
    contactChannel: _c,
    workplace: _w,
    applicantRegion: _a,
    sourceUpdated: _su,
    lastSeenAt: _ls,
    workPeriod: _wp,
    evidence: _e,
    score: _s,
    analysis: _an,
    risks: _r,
    profitability: _p,
    draft: _d,
    memo: _m,
    outcomes: _oc,
    related: _rel,
    discoveryPaths: _dp,
    activity: _ac,
    feedback: _fb,
    parserVersion: _pv,
    ...summary
  } = d;
  return summary;
}
