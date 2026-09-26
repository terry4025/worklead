/**
 * 표시용 분류. 판정 로직이 아니라, 백엔드가 준 값(판정 + 근거 유형)을
 * 어떤 표기·기호로 보여줄지 정하는 규칙이다.
 */
import type { DemandIntent, IntentFilter, LeadSummary, RecruitFilter, RemoteFilter } from './model';
import { accessStatusLabel, sourceStatusLabel } from './labels';
import { formatRelative } from './format';

/** 기호 모양 + 색으로 구분하는 상태 톤. 색만으로 의미를 전달하지 않는다. */
export type Tone = 'good' | 'warn' | 'unknown' | 'bad' | 'muted';

export type RemoteClass = 'confirmed' | 'inferred' | 'partial' | 'negotiable' | 'onsite' | 'unknown';

export function remoteClass(lead: Pick<LeadSummary, 'workMode'>): RemoteClass {
  const { value, basis } = lead.workMode;
  switch (value) {
    case 'fully_remote':
      return basis === 'explicit' || basis === 'user_confirmed' ? 'confirmed' : 'inferred';
    case 'hybrid':
      return 'partial';
    case 'negotiable':
      return 'negotiable';
    case 'onsite':
      return 'onsite';
    case 'unknown':
      return 'unknown';
  }
}

export const remoteDisplay: Record<RemoteClass, { label: string; tone: Tone }> = {
  confirmed: { label: '재택 확인', tone: 'good' },
  inferred: { label: '재택 추정', tone: 'warn' },
  partial: { label: '일부 재택', tone: 'warn' },
  negotiable: { label: '재택 협의', tone: 'warn' },
  onsite: { label: '출근 필요', tone: 'bad' },
  unknown: { label: '재택 미확인', tone: 'unknown' },
};

/** 재택 판정에 덧붙는 주의 사항 (대면 필요, 지역 제한) */
export function remoteCaveats(lead: Pick<LeadSummary, 'collaborationMode' | 'applicantScope'>): string[] {
  const out: string[] = [];
  if (lead.collaborationMode.value === 'onsite_required') out.push('대면 필요');
  if (lead.applicantScope.value === 'regional_restriction') out.push('지역 제한');
  return out;
}

export type RecruitClass = 'open' | 'recheck' | 'unknown' | 'closed' | 'deleted' | 'access_issue';

export function recruitClass(
  lead: Pick<LeadSummary, 'sourceStatus' | 'accessStatus' | 'recheckDue'>,
): RecruitClass {
  if (lead.sourceStatus === 'closed') return 'closed';
  if (lead.sourceStatus === 'deleted') return 'deleted';
  // 접근 오류는 마감이 아니다. 마지막으로 알려진 상태와 함께 따로 보여준다.
  if (lead.accessStatus !== 'accessible') return 'access_issue';
  if (lead.sourceStatus === 'open') return lead.recheckDue ? 'recheck' : 'open';
  return 'unknown';
}

export function recruitDisplay(
  lead: Pick<LeadSummary, 'sourceStatus' | 'accessStatus' | 'recheckDue' | 'lastCheckedAt'>,
  now: Date = new Date(),
): { label: string; tone: Tone; detail: string } {
  const cls = recruitClass(lead);
  const checked = lead.lastCheckedAt ? `${formatRelative(lead.lastCheckedAt, now)} 확인` : '확인 기록 없음';
  switch (cls) {
    case 'open':
      return { label: '모집 중', tone: 'good', detail: checked };
    case 'recheck':
      return { label: '재확인 필요', tone: 'warn', detail: `모집 중 · ${checked}` };
    case 'unknown':
      return { label: '모집 미확인', tone: 'unknown', detail: checked };
    case 'closed':
      return { label: '마감', tone: 'muted', detail: checked };
    case 'deleted':
      return { label: '삭제됨', tone: 'muted', detail: checked };
    case 'access_issue':
      return {
        label: accessStatusLabel[lead.accessStatus],
        tone: 'bad',
        detail: `마지막 상태 ${sourceStatusLabel[lead.sourceStatus]} · ${checked}`,
      };
  }
}

export type IntentClass = 'buyer' | 'hiring' | 'seller' | 'other';

export function intentClass(intent: DemandIntent): IntentClass {
  switch (intent) {
    case 'buyer_project':
    case 'buyer_ongoing':
    case 'short_gig':
      return 'buyer';
    case 'employee_hiring':
      return 'hiring';
    case 'seller_service':
      return 'seller';
    default:
      return 'other';
  }
}

export function intentTone(intent: DemandIntent): Tone {
  const cls = intentClass(intent);
  if (cls === 'buyer') return 'good';
  if (cls === 'seller') return 'bad';
  if (intent === 'unknown') return 'unknown';
  return 'muted';
}

// ── 필터 선택지 (표시 문구) ─────────────────────────────────────────
export const remoteFilterOptions: { value: RemoteFilter; label: string }[] = [
  { value: 'any', label: '재택 전체' },
  { value: 'confirmed', label: '재택 확인만' },
  { value: 'confirmed_or_inferred', label: '재택 확인·추정' },
  { value: 'unknown', label: '재택 미확인' },
  { value: 'onsite', label: '출근·대면 필요' },
];

export const recruitFilterOptions: { value: RecruitFilter; label: string }[] = [
  { value: 'any', label: '모집 상태 전체' },
  { value: 'not_closed', label: '마감 제외' },
  { value: 'open_fresh', label: '모집 중 (최근 확인)' },
  { value: 'recheck', label: '재확인 필요' },
  { value: 'unknown', label: '모집 미확인' },
  { value: 'access_issue', label: '접근 문제' },
  { value: 'closed', label: '마감·삭제' },
];

export const intentFilterOptions: { value: IntentFilter; label: string }[] = [
  { value: 'any', label: '유형 전체' },
  { value: 'buyer', label: '구매 의뢰' },
  { value: 'hiring', label: '일반 채용' },
  { value: 'seller', label: '판매자 홍보' },
  { value: 'other', label: '기타·미확인' },
];
