/**
 * 화면 보기(view)와 대기열(queue). 대기열 정의 자체는 백엔드가 소유하며(bootstrap.queues),
 * 여기서는 보기를 대기열 묶음으로 구성하고, 방금 바뀐 행이 아직 이 대기열에 속하는지 판단할 때만 규칙을 쓴다.
 */
import type { LeadSummary, QueueId } from './model';

export type ViewId = 'fresh' | 'review' | 'interested' | 'active' | 'excluded' | 'all';

export interface ViewDef {
  id: ViewId;
  label: string;
  key: string;
  sections: { queue: QueueId; title: string; hint: string }[];
  empty: string;
}

export const VIEWS: ViewDef[] = [
  { id: 'fresh', label: '지금 검토', key: '6', sections: [{ queue: 'fresh', title: '지금 검토', hint: '24시간 내 정확한 게시 시각 · 모집 확인된 제작 의뢰 · 추천과 확인 필요 함께' }], empty: '최근 24시간 내 게시된 검토 대상이 없습니다. 게시 시각이 불명확하거나 오래된 건은 검토 탭에서 확인하세요.' },
  {
    id: 'review',
    label: '검토',
    key: '1',
    sections: [
      { queue: 'recommended', title: '추천', hint: '구매 의뢰 · 최근 모집 확인 · 재택·온라인 근거 · 위험 신호 없음' },
      { queue: 'needs_review', title: '확인 필요', hint: '유망하지만 재택·모집·외주 여부 중 확인할 것이 있음' },
    ],
    empty: '검토할 리드가 없습니다',
  },
  { id: 'interested', label: '관심', key: '2', sections: [{ queue: 'interested', title: '관심', hint: '관심 표시 · 진행 전' }], empty: '관심 표시한 리드가 없습니다. 목록에서 S 키로 표시할 수 있습니다.' },
  { id: 'active', label: '진행 중', key: '3', sections: [{ queue: 'active', title: '진행 중', hint: '연락함 · 협상 중 · 수주 · 보류' }], empty: '진행 중인 영업이 없습니다' },
  {
    id: 'excluded',
    label: '제외',
    key: '4',
    sections: [
      { queue: 'dismissed', title: '내가 제외', hint: '직접 제외한 리드' },
      { queue: 'auto_excluded', title: '자동 제외', hint: '마감 · 판매자 홍보 · 출근 필수 · 위험 신호 · 관련 없는 업무' },
    ],
    empty: '제외된 리드가 없습니다',
  },
  { id: 'all', label: '전체', key: '5', sections: [{ queue: 'all', title: '전체', hint: '모든 리드' }], empty: '리드가 없습니다' },
];

const PRE = new Set(['new', 'reviewing']);
const ACTIVE = new Set(['contacted', 'negotiating', 'won', 'on_hold']);

/** 백엔드 services/leads.py queue_clause 와 같은 규칙 */
export function inQueue(l: Pick<LeadSummary, 'recommendation' | 'userMark' | 'salesStage'> & Partial<LeadSummary>, q: QueueId, now: Date = new Date()): boolean {
  const notDismissed = l.userMark !== 'dismissed' && l.salesStage !== 'ignored';
  switch (q) {
    case 'fresh': {
      const posted = l.published?.precision === 'exact' && l.published.at ? Date.parse(l.published.at) : NaN;
      const age = now.getTime() - posted;
      return notDismissed && PRE.has(l.salesStage) && l.recommendation !== 'excluded'
        && ['buyer_project', 'buyer_ongoing'].includes(l.demandIntent?.value ?? '')
        && l.engagementType?.value === 'project' && l.sourceStatus === 'open' && l.accessStatus === 'accessible'
        && l.recheckDue === false && l.lastCheckedAt != null && age >= 0 && age <= 24 * 60 * 60_000;
    }
    case 'recommended':
      return l.recommendation === 'recommended' && notDismissed && PRE.has(l.salesStage);
    case 'needs_review':
      return l.recommendation === 'needs_review' && notDismissed && PRE.has(l.salesStage);
    case 'auto_excluded':
      return l.recommendation === 'excluded' && notDismissed && PRE.has(l.salesStage);
    case 'interested':
      return l.userMark === 'interested' && PRE.has(l.salesStage);
    case 'active':
      return ACTIVE.has(l.salesStage);
    case 'dismissed':
      return l.userMark === 'dismissed' || l.salesStage === 'ignored';
    case 'all':
      return true;
  }
}
