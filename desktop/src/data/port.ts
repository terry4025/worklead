import type {
  Bootstrap,
  JobAccepted,
  LeadDetail,
  LeadFeedback,
  LeadFilter,
  LeadPatch,
  LeadSummary,
  QueueId,
  ExportInput,
  ManualImportInput,
  OutcomeInput,
  Page,
  RunInfo,
  RunKind,
  SalesSummary,
  SettingsView,
  SourceInfo,
  StreamStatus,
  WorkleadEvent,
} from '../domain/model';

/**
 * 화면이 데이터 계층에 요구하는 기능 목록.
 *
 * 화면 코드는 이 인터페이스만 사용한다. 구현체:
 * - data/mock: 데모 데이터 (mode = 'demo')
 * - data/live: contracts/client 기반 어댑터 (계약 도착 후 구현)
 *
 * 메서드 이름·인자는 화면 관점의 요구이며 HTTP 경로와 1:1 대응을 강제하지 않는다.
 */
export interface WorkleadPort {
  readonly mode: 'demo' | 'live';

  bootstrap(): Promise<Bootstrap>;

  listLeads(req: { queue: QueueId; filter: LeadFilter; cursor: string | null; limit: number }): Promise<Page<LeadSummary>>;
  /** 대기열별 건수 (현재 필터 적용). 얻을 수 없으면 null 을 돌려주고 화면은 건수를 숨긴다. */
  countQueues(filter: LeadFilter): Promise<Record<QueueId, number> | null>;
  getLead(id: string): Promise<LeadDetail>;
  updateLead(id: string, patch: LeadPatch): Promise<LeadDetail>;
  recheckLead(id: string): Promise<JobAccepted>;
  reanalyzeLead(id: string): Promise<JobAccepted>;
  sendFeedback(id: string, feedback: LeadFeedback): Promise<LeadDetail>;
  addOutcome(id: string, input: OutcomeInput): Promise<LeadDetail>;
  /** 문의 초안 생성 요청. 발송 기능은 없다. */
  generateDraft(id: string): Promise<LeadDetail>;

  listSources(): Promise<SourceInfo[]>;
  updateSource(
    id: string,
    patch: {
      autoCollectEnabled?: boolean;
      intervalMinutes?: number;
      clearStop?: boolean;
      policy?: { status: 'permission_pending' | 'allowed' | 'restricted' | 'blocked'; basis: string | null; note: string | null };
    },
  ): Promise<SourceInfo>;
  listRuns(req: { sourceId?: string; limit: number }): Promise<RunInfo[]>;
  getRun(id: string): Promise<RunInfo>;
  startRun(req: { sourceId: string; kind: Extract<RunKind, 'discovery' | 'recheck_recent' | 'recheck_stale'> }): Promise<JobAccepted>;
  runAction(runId: string, action: 'pause' | 'resume' | 'cancel'): Promise<RunInfo>;
  importManual(input: ManualImportInput): Promise<JobAccepted>;
  exportData(input: ExportInput): Promise<JobAccepted>;

  getSettings(): Promise<SettingsView>;
  /** 바뀐 구획만 보낸다 */
  updateSettings(patch: Partial<Pick<SettingsView, 'profile' | 'recheck' | 'notifications' | 'retention'>> & {
    ai?: Partial<Pick<SettingsView['ai'], 'enabled' | 'externalTransferConsent' | 'monthlyCostCap' | 'dailyCostCap'>>;
    queryGroups?: SettingsView['queryGroups']['groups'];
  }): Promise<SettingsView>;
  getSalesSummary(): Promise<SalesSummary>;

  /** 이벤트 구독. 반환 함수로 해제한다. */
  subscribe(onEvent: (event: WorkleadEvent) => void, onStatus: (status: StreamStatus) => void): () => void;
}

export type PortErrorKind = 'connection' | 'api' | 'not_implemented' | 'invalid_input';

/** 화면에서 다루는 오류. 계약의 {error:{code,message,details,request_id,retryable}} 을 여기로 옮긴다. */
export class PortError extends Error {
  readonly kind: PortErrorKind;
  readonly code: string;
  readonly retryable: boolean;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(init: {
    kind: PortErrorKind;
    code: string;
    message: string;
    retryable?: boolean;
    requestId?: string | null;
    details?: unknown;
  }) {
    super(init.message);
    this.name = 'PortError';
    this.kind = init.kind;
    this.code = init.code;
    this.retryable = init.retryable ?? false;
    this.requestId = init.requestId ?? null;
    this.details = init.details;
  }
}

export function toPortError(error: unknown): PortError {
  if (error instanceof PortError) return error;
  const message = error instanceof Error ? error.message : String(error);
  return new PortError({ kind: 'api', code: 'unexpected', message, retryable: true });
}
