/** 실제 로컬 API 에 연결하는 데이터 계층 (mode = 'live'). */
import { WorkleadApiError, createClient, type ConnectionSource, type WorkleadClient } from '@contracts/index';
import type { QueueId, StreamStatus, WorkleadEvent } from '../../domain/model';
import { PortError, type WorkleadPort } from '../port';
import {
  filterToQuery,
  mapBootstrap,
  mapDetail,
  mapEvent,
  mapRun,
  mapSales,
  mapSettings,
  mapSource,
  mapSummary,
  settingsPatchToContract,
} from './mapper';

function toPortError(err: unknown): PortError {
  if (err instanceof PortError) return err;
  if (err instanceof WorkleadApiError) {
    return new PortError({
      kind: err.kind === 'connection' ? 'connection' : err.status === 422 ? 'invalid_input' : 'api',
      code: err.code,
      message: err.message,
      retryable: err.retryable,
      requestId: err.requestId,
      details: err.details,
    });
  }
  if (err instanceof Error && err.name === 'AbortError') throw err;
  return new PortError({ kind: 'api', code: 'unexpected', message: err instanceof Error ? err.message : String(err), retryable: true });
}

async function wrap<T>(p: Promise<T>): Promise<T> {
  try {
    return await p;
  } catch (err) {
    throw toPortError(err);
  }
}

export class LivePort implements WorkleadPort {
  readonly mode = 'live' as const;
  private client: WorkleadClient;

  constructor(connection: ConnectionSource, fetchImpl?: typeof fetch) {
    this.client = createClient(connection, fetchImpl);
  }

  async bootstrap() {
    return mapBootstrap(await wrap(this.client.bootstrap()));
  }

  async listLeads(req: Parameters<WorkleadPort['listLeads']>[0]) {
    const page = await wrap(this.client.listLeads({ ...filterToQuery(req.filter), queue: req.queue, limit: req.limit, cursor: req.cursor ?? undefined }));
    return { items: page.items.map(mapSummary), nextCursor: page.next_cursor, hasMore: page.has_more };
  }

  async countQueues(filter: Parameters<WorkleadPort['countQueues']>[0]): Promise<Record<QueueId, number>> {
    const counts = (await wrap(this.client.queueCounts(filterToQuery(filter)))).counts;
    const ids: QueueId[] = ['recommended', 'needs_review', 'interested', 'active', 'dismissed', 'auto_excluded', 'all'];
    return Object.fromEntries(ids.map((id) => [id, counts[id] ?? 0])) as Record<QueueId, number>;
  }

  async getLead(id: string) {
    return mapDetail(await wrap(this.client.getLead(id)));
  }

  async updateLead(id: string, patch: Parameters<WorkleadPort['updateLead']>[1]) {
    const body: Record<string, unknown> = {};
    if (patch.userMark !== undefined) body.user_mark = patch.userMark;
    if (patch.salesStage !== undefined) body.sales_stage = patch.salesStage;
    if (patch.memo !== undefined) body.memo = patch.memo;
    if (patch.draftText !== undefined) body.draft_text = patch.draftText;
    return mapDetail(await wrap(this.client.patchLead(id, body)));
  }

  async recheckLead(id: string) {
    const r = await wrap(this.client.recheckLead(id));
    return { jobId: r.job_id, runId: r.run_id };
  }

  async reanalyzeLead(id: string) {
    const r = await wrap(this.client.reanalyzeLead(id));
    return { jobId: r.job_id, runId: r.run_id };
  }

  async sendFeedback(id: string, fb: Parameters<WorkleadPort['sendFeedback']>[1]) {
    return mapDetail(await wrap(this.client.feedback(id, { kind: fb.kind, value: fb.value })));
  }

  async addOutcome(id: string, input: Parameters<WorkleadPort['addOutcome']>[1]) {
    return mapDetail(
      await wrap(
        this.client.addOutcome(id, {
          kind: input.kind,
          amount: input.amount,
          currency: 'KRW',
          occurred_on: input.occurredOn,
          note: input.note,
          evidence_ref: input.evidenceRef,
        }),
      ),
    );
  }

  async generateDraft(id: string) {
    return mapDetail(await wrap(this.client.draft(id)));
  }

  async listSources() {
    return (await wrap(this.client.listSources())).items.map(mapSource);
  }

  async updateSource(id: string, patch: Parameters<WorkleadPort['updateSource']>[1]) {
    return mapSource(
      await wrap(
        this.client.patchSource(id, {
          auto_collect_enabled: patch.autoCollectEnabled,
          interval_minutes: patch.intervalMinutes,
          clear_stop: patch.clearStop,
          policy: patch.policy,
        }),
      ),
    );
  }

  async listRuns(req: { sourceId?: string; limit: number }) {
    return (await wrap(this.client.listRuns({ source_id: req.sourceId, limit: req.limit }))).items.map(mapRun);
  }

  async getRun(id: string) {
    return mapRun(await wrap(this.client.getRun(id)));
  }

  async startRun(req: Parameters<WorkleadPort['startRun']>[0]) {
    const r = await wrap(this.client.createRun({ source_id: req.sourceId, kind: req.kind }));
    return { jobId: r.job_id, runId: r.run_id };
  }

  async runAction(runId: string, action: 'pause' | 'resume' | 'cancel') {
    return mapRun(await wrap(this.client.runAction(runId, action)));
  }

  async importManual(input: Parameters<WorkleadPort['importManual']>[0]) {
    const r = await wrap(this.client.createImport({ format: input.format, content: input.content, file_name: input.fileName, original_url: input.originalUrl }));
    return { jobId: r.job_id, runId: r.run_id };
  }

  async exportData(input: Parameters<WorkleadPort['exportData']>[0]) {
    const r = await wrap(this.client.createExport({ kind: input.kind }));
    return { jobId: r.job_id, runId: r.job_id };
  }

  async getSettings() {
    return mapSettings(await wrap(this.client.getSettings()));
  }

  async updateSettings(patch: Parameters<WorkleadPort['updateSettings']>[0]) {
    return mapSettings(await wrap(this.client.patchSettings(settingsPatchToContract(patch))));
  }

  async getSalesSummary() {
    return mapSales(await wrap(this.client.metrics()));
  }

  subscribe(onEvent: (e: WorkleadEvent) => void, onStatus: (s: StreamStatus) => void): () => void {
    const ctrl = new AbortController();
    void this.client.streamEvents({
      afterSeq: null,
      signal: ctrl.signal,
      onStatus,
      onEvent: (raw) => {
        const e = mapEvent(raw);
        if (e) onEvent(e);
      },
    });
    return () => ctrl.abort();
  }
}
