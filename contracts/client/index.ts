/**
 * Worklead 로컬 API 클라이언트 (계약: contracts/openapi.json → schema.d.ts 생성 타입).
 *
 * - 모든 요청에 Authorization: Bearer <token> (토큰을 URL 에 넣지 않는다)
 * - 오류는 {error:{code,message,details,request_id,retryable}} → WorkleadApiError
 * - 이벤트는 fetch 스트리밍으로 SSE 를 읽는다 (EventSource 는 헤더를 보낼 수 없음)
 */
import type { components } from './schema';

export type Schemas = components['schemas'];
export type LeadSummary = Schemas['LeadSummary'];
export type LeadDetail = Schemas['LeadDetail'];
export type LeadPage = Schemas['LeadPage'];
export type LeadPatch = Schemas['LeadPatch'];
export type FeedbackIn = Schemas['FeedbackIn'];
export type OutcomeIn = Schemas['OutcomeIn'];
export type SourceOut = Schemas['SourceOut'];
export type SourcePatch = Schemas['SourcePatch'];
export type RunOut = Schemas['RunOut'];
export type RunCreate = Schemas['RunCreate'];
export type JobAccepted = Schemas['JobAccepted'];
export type SettingsOut = Schemas['SettingsOut'];
export type SettingsPatch = Schemas['SettingsPatch'];
export type BootstrapOut = Schemas['BootstrapOut'];
export type HealthOut = Schemas['HealthOut'];
export type MetricsOut = Schemas['MetricsOut'];
export type ImportIn = Schemas['ImportIn'];
export type ExportIn = Schemas['ExportIn'];
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
  let cached: Connection | null = typeof source === 'function' ? null : source;
  async function conn(): Promise<Connection> {
    if (cached) return cached;
    cached = await (source as () => Promise<Connection>)();
    return cached;
  }

  async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
    const c = await conn();
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
        details: String(err),
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
    connection: conn,
    health: () => request<HealthOut>('GET', '/v1/health'),
    bootstrap: () => request<BootstrapOut>('GET', '/v1/bootstrap'),
    listSources: () => request<Schemas['SourceList']>('GET', '/v1/sources'),
    patchSource: (id: string, body: SourcePatch) => request<SourceOut>('PATCH', `/v1/sources/${enc(id)}`, { body }),
    createRun: (body: RunCreate) => request<JobAccepted>('POST', '/v1/runs', { body }),
    listRuns: (query: { source_id?: string; kind?: string[]; limit?: number; cursor?: string } = {}) =>
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
    addOutcome: (id: string, body: OutcomeIn) => request<LeadDetail>('POST', `/v1/leads/${enc(id)}/outcomes`, { body }),
    draft: (id: string) => request<LeadDetail>('POST', `/v1/leads/${enc(id)}/draft`),
    getSettings: () => request<SettingsOut>('GET', '/v1/settings'),
    patchSettings: (body: SettingsPatch) => request<SettingsOut>('PATCH', '/v1/settings', { body }),
    createImport: (body: ImportIn) => request<JobAccepted>('POST', '/v1/imports', { body }),
    createExport: (body: ExportIn) => request<JobAccepted>('POST', '/v1/exports', { body }),
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
