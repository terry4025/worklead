/**
 * Worklead 로컬 API 클라이언트 (계약: contracts/openapi.json → schema.d.ts 생성 타입).
 *
 * - 모든 요청에 Authorization: Bearer <token> (토큰을 URL 에 넣지 않는다)
 * - 오류는 {error:{code,message,details,request_id,retryable}} → WorkleadApiError
 * - 이벤트는 fetch 스트리밍으로 SSE 를 읽는다 (EventSource 는 헤더를 보낼 수 없음)
 */
import type { components } from './schema';

export type Schemas = components['schemas'];
export interface JobApplicationCandidate {
  lead_id: string;
  title: string;
  original_url: string | null;
  recommendation: 'recommended' | 'needs_review' | 'excluded';
  published_at: string | null;
  last_checked_at: string | null;
  candidate: boolean;
  can_prepare: boolean;
  can_submit: false;
  mode: 'draft_only';
  submission_supported: false;
  hold_reasons: { code: string; message: string }[];
  questions: string[];
  draft: { text: string; generated_at: string | null; edited_by_user: boolean } | null;
}
export interface JobApplicationList {
  mode: 'draft_only';
  submission_supported: false;
  items: JobApplicationCandidate[];
  total: number;
}
export interface JobMessageWatchStatus {
  provider?: 'chrome' | 'edge';
  enabled: boolean;
  remembers_connection?: boolean;
  paired?: boolean;
  state: 'stopped' | 'starting' | 'login_required' | 'watching' | 'surface_unrecognized' | 'error';
  unread_count: number | null;
  last_observed_at: string | null;
  last_increase_at: string | null;
  error_code: string | null;
  message: string;
  poll_interval_seconds: number;
  observation: 'visible_unread_badge';
  captures_message_body: boolean;
  browser_notifications: 'default' | 'granted' | 'denied' | 'unsupported' | 'unknown';
  browser_open: boolean;
  needs_user_action: boolean;
}
export interface JobChromeMessageConnection {
  connection: { url: string; token: string; purpose?: 'messages'; version?: 1 } | null;
  status: JobMessageWatchStatus;
  extension_directory: string;
}
export interface JobChatView {
  enabled: boolean;
  state: 'off' | 'waiting' | 'visible' | 'panel_closed' | 'surface_unrecognized' | 'login_required' | 'stale' | 'account_changed';
  text: string | null;
  observed_at: string | null;
  lead_id: string | null;
  lead_title: string | null;
  source: 'visible_chat_panel';
  persists_body: false;
}
export interface JobAutoApplyAttempt {
  id: string;
  lead_id: string;
  title: string;
  state: 'claimed' | 'ready' | 'submitting' | 'submitted' | 'already_applied' | 'held' | 'uncertain';
  reason_code: string | null;
  created_at: string;
  finished_at: string | null;
  retry_count?: number;
  retry_after?: string | null;
}
export interface JobAutoApplyCandidate {
  lead_id: string;
  title: string;
  stage: 'queued' | 'source_refresh' | 'rejected' | 'form_review' | 'permit_issued' | 'submitted' | 'already_applied' | 'retry_wait' | 'held' | 'uncertain';
  reason_code: string | null;
  retry_count?: number;
  retry_after?: string | null;
  updated_at?: string | null;
  refresh_state?: string | null;
  refresh_error_code?: string | null;
  last_checked_at?: string | null;
}
export interface JobAutoApplyStatus {
  enabled: boolean;
  remembers_connection: boolean;
  paired: boolean;
  state: 'stopped' | 'waiting' | 'connected' | 'paused';
  min_score: number;
  daily_limit: number;
  today_submitted: number;
  today_reserved: number;
  held: number;
  uncertain: number;
  last_connected_at: string | null;
  error_code: string | null;
  message: string;
  attempts: JobAutoApplyAttempt[];
  queue_reason?: string | null;
  needs_user_action?: boolean;
  retry_waiting?: number;
  candidates?: JobAutoApplyCandidate[];
}
export interface JobAutoApplyStart {
  connection: { url: string; token: string; purpose?: 'auto_apply'; version?: 1 } | null;
  status: JobAutoApplyStatus;
  extension_directory: string;
}
export type LeadSummary = Schemas['LeadSummary'];
export type LeadDetail = Schemas['LeadDetail'];
export type LeadPage = Schemas['LeadPage'];
export type LeadPatch = Schemas['LeadPatch'];
export type FeedbackIn = Schemas['FeedbackIn'];
export type RelationUpdateIn = Schemas['RelationUpdateIn'];
export type RelationUpdateOut = Schemas['RelationUpdateOut'];
export type OutcomeIn = Schemas['OutcomeIn'];
export type SourceOut = Schemas['SourceOut'];
export type SourcePatch = Schemas['SourcePatch'];
export type RunOut = Schemas['RunOut'];
export type RunCreate = Schemas['RunCreate'];
export type JobAccepted = Schemas['JobAccepted'];
export type SettingsOut = Schemas['SettingsOut'];
export type SettingsPatch = Schemas['SettingsPatch'];
export type AIStatus = Schemas['AIStatus'];
export type AICredentialIn = Schemas['AICredentialIn'];
export type AICredentialAction = Schemas['AICredentialAction'];
export type BootstrapOut = Schemas['BootstrapOut'];
export type HealthOut = Schemas['HealthOut'];
export type MetricsOut = Schemas['MetricsOut'];
export type ImportIn = Schemas['ImportIn'];
export type ExportIn = Schemas['ExportIn'];
export type BackupOut = Schemas['BackupOut'];
export type BackupInspection = Schemas['BackupInspection'];
export type RestoreOut = Schemas['RestoreOut'];
export type NativeNotificationList = Schemas['NativeNotificationList'];
export type NativeNotificationClaimOut = Schemas['NativeNotificationClaimOut'];
export type NativeNotificationResultIn = Schemas['NativeNotificationResultIn'];
export type NativeNotificationResultOut = Schemas['NativeNotificationResultOut'];
export type QueueId = BootstrapOut['queues'][number]['id'];

export interface LeadQuery {
  queue?: QueueId;
  keyword?: string;
  source_id?: string;
  category?: string;
  remote?: 'any' | 'confirmed' | 'confirmed_or_inferred' | 'unknown' | 'onsite';
  recruit?: 'not_closed' | 'open_fresh' | 'recheck' | 'unknown' | 'closed' | 'access_issue' | 'any';
  intent?: 'any' | 'buyer' | 'hiring' | 'seller' | 'other';
  work_mode?: string[];
  recommendation?: string[];
  source_status?: string[];
  sales_stage?: string[];
  pay_unit?: string[];
  checked_since?: string;
  sort?: 'priority' | 'published' | 'checked';
  limit?: number;
  cursor?: string;
}

export interface ApiEvent {
  id: string;
  seq: number;
  type:
    | 'run.progress'
    | 'run.state_changed'
    | 'lead.created'
    | 'lead.updated'
    | 'source.health_changed'
    | 'analysis.completed'
    | 'notification.created'
    | 'stream.reset';
  at: string;
  [key: string]: unknown;
}

export interface Connection {
  baseUrl: string;
  token: string;
}

export class WorkleadApiError extends Error {
  readonly status: number | null;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly kind: 'api' | 'connection';

  constructor(init: {
    status: number | null;
    code: string;
    message: string;
    details?: unknown;
    requestId?: string | null;
    retryable?: boolean;
    kind: 'api' | 'connection';
  }) {
    super(init.message);
    this.name = 'WorkleadApiError';
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? null;
    this.requestId = init.requestId ?? null;
    this.retryable = init.retryable ?? false;
    this.kind = init.kind;
  }
}

type Query = Record<string, string | number | boolean | string[] | undefined | null>;

function toSearch(query?: Query): string {
  if (!query) return '';
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export type ConnectionSource = Connection | (() => Promise<Connection>);

export function createClient(source: ConnectionSource, fetchImpl: typeof fetch = (...a) => fetch(...a)) {
  async function conn(): Promise<Connection> {
    // The native host owns the current session. A sidecar restart rotates both
    // its port and token, including while an event stream is reconnecting.
    return typeof source === 'function' ? source() : source;
  }

  async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
    // A live SSE stream does not prove ordinary reads are completing. Bound
    // connection acquisition, headers AND JSON so a stalled read cannot leave
    // the list skeleton or refresh queue pending forever. Never time out/retry writes here.
    if (method !== 'GET') return requestBody<T>(method, path, opts);
    const controller = new AbortController();
    const cancelled = () => controller.abort();
    opts.signal?.addEventListener('abort', cancelled, { once: true });
    if (opts.signal?.aborted) controller.abort();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => {
        reject(new WorkleadApiError({ status: null, code: 'request_timeout', message: '목록 조회 응답이 늦어 다시 확인합니다', retryable: true, kind: 'connection' }));
        controller.abort();
      }, 15_000);
    });
    try { return await Promise.race([requestBody<T>(method, path, { ...opts, signal: controller.signal }), timeout]); }
    finally { clearTimeout(timer); opts.signal?.removeEventListener('abort', cancelled); }
  }

  async function requestBody<T>(method: string, path: string, opts: { query?: Query; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
    const c = await conn();
    if (opts.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    let res: Response;
    try {
      res = await fetchImpl(`${c.baseUrl}${path}${toSearch(opts.query)}`, {
        method,
        headers: {
          Authorization: `Bearer ${c.token}`,
          ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: opts.signal,
      });
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') throw err;
      throw new WorkleadApiError({
        status: null,
        code: 'connection_failed',
        message: '로컬 서비스에 연결할 수 없습니다',
        retryable: true,
        kind: 'connection',
        details: path.startsWith('/v1/ai/credentials') ? null : String(err),
      });
    }
    if (res.ok) return (await res.json()) as T;
    let body: { error?: { code: string; message: string; details?: unknown; request_id?: string; retryable?: boolean } } | null = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    throw new WorkleadApiError({
      status: res.status,
      code: body?.error?.code ?? `http_${res.status}`,
      message: body?.error?.message ?? `요청 실패 (HTTP ${res.status})`,
      details: body?.error?.details,
      requestId: body?.error?.request_id ?? res.headers.get('X-Request-Id'),
      retryable: body?.error?.retryable ?? res.status >= 500,
      kind: 'api',
    });
  }

  const enc = encodeURIComponent;

  return {
    jobApplications: (limit = 50) => request<JobApplicationList>('GET', '/v1/jobs/applications', { query: { limit } }),
    jobApplication: (id: string) => request<JobApplicationCandidate>('GET', `/v1/jobs/applications/${enc(id)}`),
    jobApplicationPrepare: (id: string) => request<JobApplicationCandidate>('POST', `/v1/jobs/applications/${enc(id)}/prepare`),
    jobApplicationDraft: (id: string, body: { text: string; expected_text: string | null }) => request<JobApplicationCandidate>('PATCH', `/v1/jobs/applications/${enc(id)}/draft`, { body }),
    jobMessagesStatus: () => request<JobMessageWatchStatus>('GET', '/v1/jobs/messages/status'),
    jobMessagesStart: () => request<JobMessageWatchStatus>('POST', '/v1/jobs/messages/start', { body: { confirm: true } }),
    jobMessagesStop: () => request<JobMessageWatchStatus>('POST', '/v1/jobs/messages/stop'),
    jobMessagesChromeStart: (captureVisibleChat = false, rememberConnection = false) => request<JobChromeMessageConnection>('POST', '/v1/jobs/messages/chrome/start', { body: { confirm: true, ...(captureVisibleChat ? { capture_visible_chat: true } : {}), ...(rememberConnection ? { remember_connection: true } : {}) } }),
    jobMessagesChromeForget: () => request<JobMessageWatchStatus>('POST', '/v1/jobs/messages/chrome/forget'),
    jobMessagesInbox: () => request<JobChatView>('GET', '/v1/jobs/messages/inbox'),
    jobAutoApplyStatus: () => request<JobAutoApplyStatus>('GET', '/v1/jobs/auto-apply/status'),
    jobAutoApplyStart: (body: { confirm: true; min_score: number; daily_limit: number; remember_connection?: boolean }) => request<JobAutoApplyStart>('POST', '/v1/jobs/auto-apply/start', { body }),
    jobAutoApplyStop: () => request<JobAutoApplyStatus>('POST', '/v1/jobs/auto-apply/stop'),
    jobAutoApplyForget: () => request<JobAutoApplyStatus>('POST', '/v1/jobs/auto-apply/forget'),
    cafeStatus: () => request<Schemas['CafeSnapshot']>('GET', '/v1/cafe/status'),
    cafeComments: () => request<Schemas['CafeCommentList']>('GET', '/v1/cafe/comments'),
    cafeCommentUpdate: (id: string, body: Schemas['CafeCommentPatch']) => request<Schemas['CafeCommentOut']>('PATCH', `/v1/cafe/comments/${enc(id)}`, { body }),
    cafeCommentLead: (id: string) => request<LeadDetail>('POST', `/v1/cafe/comments/${enc(id)}/lead`),
    cafeCommentDraft: (id: string) => request<Schemas['CafeCommentDraft']>('POST', `/v1/cafe/comments/${enc(id)}/draft`),
    cafeTemplate: () => request<Schemas['CafeTemplate']>('GET', '/v1/cafe/template'),
    cafeTemplateUpdate: (body: Schemas['CafeTemplatePatch']) => request<Schemas['CafeTemplate']>('PATCH', '/v1/cafe/template', { body }),
    cafeAttempts: () => request<Schemas['CafeAttemptList']>('GET', '/v1/cafe/attempts'),
    cafePost: (id: string) => request<Schemas['CafeJob']>('POST', `/v1/cafe/cafes/${enc(id)}/post`),
    cafeRepost: (id: string) => request<Schemas['CafeJob']>('POST', `/v1/cafe/attempts/${enc(id)}/repost`, { body: { confirm: true } }),
    cafeConsent: (version: string) => request<Schemas['CafeConsentOut']>('POST', '/v1/cafe/consent', { body: { version } }),
    cafeSettings: (body: Schemas['CafeSettingsPatch']) => request<Schemas['CafeSettings']>('PATCH', '/v1/cafe/settings', { body }),
    cafeList: () => request<Schemas['CafeList']>('GET', '/v1/cafe/cafes'),
    cafeAdd: (url: string) => request<Schemas['CafeEntry']>('POST', '/v1/cafe/cafes', { body: { url } }),
    cafeState: (id: string, status: Schemas['CafeEntry']['status']) => request<Schemas['CafeEntry']>('PATCH', `/v1/cafe/cafes/${enc(id)}`, { body: { status } }),
    cafeBoard: (id: string, approved: boolean) => request<Schemas['CafeBoardOut']>('PATCH', `/v1/cafe/boards/${enc(id)}`, { body: { approved } }),
    cafeJobs: () => request<Schemas['CafeJobList']>('GET', '/v1/cafe/jobs'),
    cafeAction: (kind: Schemas['CafeJob']['kind']) => request<Schemas['CafeJob']>('POST', `/v1/cafe/actions/${enc(kind)}`),
    connection: conn,
    aiStatus: () => request<AIStatus>('GET', '/v1/ai/status'),
    saveAiCredential: (body: AICredentialIn) => request<AIStatus>('POST', '/v1/ai/credentials', { body }),
    deleteAiCredential: (body: AICredentialAction) => request<AIStatus>('POST', '/v1/ai/credentials/delete', { body }),
    health: () => request<HealthOut>('GET', '/v1/health'),
    bootstrap: () => request<BootstrapOut>('GET', '/v1/bootstrap'),
    listSources: () => request<Schemas['SourceList']>('GET', '/v1/sources'),
    patchSource: (id: string, body: SourcePatch) => request<SourceOut>('PATCH', `/v1/sources/${enc(id)}`, { body }),
    createRun: (body: RunCreate) => request<JobAccepted>('POST', '/v1/runs', { body }),
    listRuns: (query: { source_id?: string; kind?: string[]; state?: string[]; limit?: number; cursor?: string } = {}) =>
      request<Schemas['RunPage']>('GET', '/v1/runs', { query }),
    getRun: (id: string) => request<RunOut>('GET', `/v1/runs/${enc(id)}`),
    runAction: (id: string, action: 'pause' | 'resume' | 'cancel') => request<RunOut>('POST', `/v1/runs/${enc(id)}/actions`, { body: { action } }),
    listLeads: (query: LeadQuery, signal?: AbortSignal) => request<LeadPage>('GET', '/v1/leads', { query: query as Query, signal }),
    queueCounts: (query: Omit<LeadQuery, 'queue' | 'limit' | 'cursor'>, signal?: AbortSignal) =>
      request<Schemas['QueueCounts']>('GET', '/v1/leads/queue-counts', { query: query as Query, signal }),
    getLead: (id: string) => request<LeadDetail>('GET', `/v1/leads/${enc(id)}`),
    patchLead: (id: string, body: LeadPatch) => request<LeadDetail>('PATCH', `/v1/leads/${enc(id)}`, { body }),
    recheckLead: (id: string) => request<JobAccepted>('POST', `/v1/leads/${enc(id)}/recheck`),
    reanalyzeLead: (id: string) => request<JobAccepted>('POST', `/v1/leads/${enc(id)}/reanalyze`),
    feedback: (id: string, body: FeedbackIn) => request<LeadDetail>('POST', `/v1/leads/${enc(id)}/feedback`, { body }),
    relation: (id: string, relationId: string, body: RelationUpdateIn) => request<RelationUpdateOut>('POST', `/v1/leads/${enc(id)}/relations/${enc(relationId)}`, { body }),
    addOutcome: (id: string, body: OutcomeIn) => request<LeadDetail>('POST', `/v1/leads/${enc(id)}/outcomes`, { body }),
    draft: (id: string) => request<LeadDetail>('POST', `/v1/leads/${enc(id)}/draft`),
    getSettings: () => request<SettingsOut>('GET', '/v1/settings'),
    patchSettings: (body: SettingsPatch) => request<SettingsOut>('PATCH', '/v1/settings', { body }),
    createImport: (body: ImportIn) => request<JobAccepted>('POST', '/v1/imports', { body }),
    createExport: (body: ExportIn) => request<JobAccepted>('POST', '/v1/exports', { body }),
    listBackups: () => request<Schemas['BackupList']>('GET', '/v1/backups'),
    createBackup: () => request<BackupOut>('POST', '/v1/backups'),
    inspectBackup: (id: string) => request<BackupInspection>('GET', `/v1/backups/${enc(id)}`),
    restoreBackup: (id: string, body: Schemas['RestoreIn']) => request<RestoreOut>('POST', `/v1/backups/${enc(id)}/restore`, { body }),
    listNativeNotifications: (query: { limit?: number; created_after?: string } = {}) => request<NativeNotificationList>('GET', '/v1/native-notifications', { query }),
    claimNativeNotification: (id: string) => request<NativeNotificationClaimOut>('POST', `/v1/native-notifications/${enc(id)}/claim`),
    nativeNotificationResult: (id: string, body: NativeNotificationResultIn) => request<NativeNotificationResultOut>('POST', `/v1/native-notifications/${enc(id)}/result`, { body }),
    metrics: () => request<MetricsOut>('GET', '/v1/metrics'),
    streamEvents: (opts: StreamOptions) => streamEvents(conn, fetchImpl, opts),
  };
}

export type WorkleadClient = ReturnType<typeof createClient>;

export interface StreamOptions {
  afterSeq: number | null;
  onEvent: (e: ApiEvent) => void;
  onStatus?: (s: 'connecting' | 'open' | 'retrying' | 'closed') => void;
  signal: AbortSignal;
}

/** SSE 를 fetch 스트리밍으로 읽는다. 끊기면 마지막 seq 부터 이어받고, 이미 받은 seq 는 버린다. */
async function streamEvents(conn: () => Promise<Connection>, fetchImpl: typeof fetch, opts: StreamOptions): Promise<void> {
  let last = opts.afterSeq;
  let attempt = 0;
  const delays = [1000, 2000, 5000, 10000];
  while (!opts.signal.aborted) {
    opts.onStatus?.(attempt === 0 ? 'connecting' : 'retrying');
    try {
      const c = await conn();
      const res = await fetchImpl(`${c.baseUrl}/v1/events${last !== null ? `?after_seq=${last}` : ''}`, {
        headers: { Authorization: `Bearer ${c.token}`, Accept: 'text/event-stream' },
        signal: opts.signal,
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      opts.onStatus?.('open');
      attempt = 0;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
        let idx: number;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const data = block
            .split('\n')
            .filter((l) => l.startsWith('data:'))
            .map((l) => l.slice(5).trimStart())
            .join('\n');
          if (!data) continue;
          let evt: ApiEvent;
          try {
            evt = JSON.parse(data) as ApiEvent;
          } catch {
            continue;
          }
          if (evt.type !== 'stream.reset' && last !== null && evt.seq <= last) continue;
          last = evt.seq;
          opts.onEvent(evt);
        }
      }
    } catch (err) {
      if (opts.signal.aborted) break;
      void err;
    }
    if (opts.signal.aborted) break;
    const delay = delays[Math.min(attempt, delays.length - 1)] ?? 10000;
    attempt += 1;
    opts.onStatus?.('retrying');
    await new Promise((r) => setTimeout(r, delay));
  }
  opts.onStatus?.('closed');
}
