/**
 * 수집 이벤트를 짧게 모아 활성 목록을 자동 재조회한다.
 * 상세 편집 상태는 유지하고, 연결 복구·실행 상태 폴링으로 누락을 보완한다.
 */
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import type { LeadDetail, RunInfo, StreamStatus, WorkleadEvent } from '../domain/model';
import { useToast } from '../ui/Toasts';
import { usePort } from './PortContext';
import { applyDetail, qk, useRunStatus } from './queries';
import { getDatabaseEpoch, resetDatabaseQueries } from './databaseCache';

export interface LiveState {
  stream: StreamStatus;
  newLeads: number;
  listStale: boolean;
  acknowledge: () => Promise<boolean>;
}

export function useLiveEvents(onOpenLead: (id: string) => void, onOpenMessages?: () => void): LiveState {
  const port = usePort();
  const qc = useQueryClient();
  const toast = useToast();
  const [stream, setStream] = useState<StreamStatus>('connecting');
  const [newLeads, setNewLeads] = useState(0);
  const [listStale, setListStale] = useState(false);
  const listRevision = useRef(0);
  const refreshSerial = useRef(0);
  const scheduleRef = useRef<() => void>(() => undefined);
  const runs = useRunStatus();
  const polledRuns = useRef<Map<string, string> | null>(null);
  const openRef = useRef(onOpenLead);
  openRef.current = onOpenLead;
  const messagesRef = useRef(onOpenMessages);
  messagesRef.current = onOpenMessages;

  useEffect(() => {
    let disconnected = false;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let refreshing = false;
    let pending = false;
    let resetting = false;
    const refreshLists = async () => {
      timer = undefined;
      if (disposed || resetting) return;
      refreshing = true;
      pending = false;
      const revision = listRevision.current;
      const serial = ++refreshSerial.current;
      const epoch = getDatabaseEpoch(qc);
      let failed = false;
      try {
        // Inactive filters must also be stale when the user next opens them.
        await qc.invalidateQueries({ queryKey: qk.leadsAll, refetchType: 'none' });
        await qc.refetchQueries({ queryKey: qk.leadsAll, type: 'active' }, { throwOnError: true, cancelRefetch: false });
        if (!disposed && epoch === getDatabaseEpoch(qc) && refreshSerial.current === serial && listRevision.current === revision) {
          setNewLeads(0);
          setListStale(false);
        }
      } catch {
        failed = true;
        if (!disposed && epoch === getDatabaseEpoch(qc)) setListStale(true);
      } finally {
        refreshing = false;
        if (pending && !disposed && !resetting) timer = setTimeout(() => void refreshLists(), 250);
        else if (failed && !disposed && !resetting) timer = setTimeout(() => void refreshLists(), 30_000);
      }
    };
    const scheduleRefresh = () => {
      listRevision.current += 1;
      setListStale(true);
      pending = true;
      if (!timer && !refreshing && !resetting) timer = setTimeout(() => void refreshLists(), 250);
    };
    scheduleRef.current = scheduleRefresh;
    const refreshAfterGap = () => {
      void qc.invalidateQueries({ queryKey: qk.sources });
      void qc.invalidateQueries({ queryKey: qk.runs });
      void qc.invalidateQueries({ queryKey: ['counts'] });
      void qc.invalidateQueries({ queryKey: ['lead'] });
      void qc.invalidateQueries({ queryKey: ['jobs'] });
      scheduleRefresh();
    };
    const refreshLead = (leadId: string) => {
      if (!qc.getQueryData(qk.lead(leadId)) && !qc.getQueryState(qk.lead(leadId))) return;
      const epoch = getDatabaseEpoch(qc);
      port
        .getLead(leadId)
        .then((d: LeadDetail) => { if (!disposed && getDatabaseEpoch(qc) === epoch) applyDetail(qc, d); })
        .catch(() => undefined);
    };
    const handle = (e: WorkleadEvent) => {
      switch (e.type) {
        case 'run.progress':
          qc.setQueriesData<RunInfo[]>({ queryKey: qk.runs }, (runs) => runs?.map((r) => (r.id === e.runId && ['queued', 'running'].includes(r.state) ? { ...r, state: 'running', progress: e.progress } : r)));
          if (!qc.getQueryData<RunInfo[]>([...qk.runs, 'status'])?.some((r) => r.id === e.runId)) void qc.invalidateQueries({ queryKey: qk.runs });
          scheduleRefresh();
          break;
        case 'run.state_changed':
          void qc.invalidateQueries({ queryKey: qk.runs });
          void qc.invalidateQueries({ queryKey: qk.sources });
          if (['succeeded', 'partial', 'failed', 'cancelled'].includes(e.state)) {
            void qc.invalidateQueries({ queryKey: ['counts'] });
            scheduleRefresh();
          }
          break;
        case 'lead.created':
          void qc.invalidateQueries({ queryKey: ['jobs', 'applications'] });
          scheduleRefresh();
          if (e.recommendation !== 'excluded') setNewLeads((n) => n + 1);
          void qc.invalidateQueries({ queryKey: ['counts'] });
          break;
        case 'lead.updated':
        case 'analysis.completed':
          scheduleRefresh();
          void qc.invalidateQueries({ queryKey: ['jobs', 'applications'] });
          void qc.invalidateQueries({ queryKey: ['jobs', 'application', e.leadId] });
          refreshLead(e.leadId);
          void qc.invalidateQueries({ queryKey: ['counts'] });
          break;
        case 'source.health_changed':
          void qc.invalidateQueries({ queryKey: qk.sources });
          break;
        case 'notification.created':
          if (e.kind === 'daangn_message') void qc.invalidateQueries({ queryKey: ['jobs', 'messages'] });
          toast.show(
            {
              tone: e.kind === 'source_issue' ? 'bad' : e.kind === 'lead_changed' ? 'warn' : 'info',
              text: e.title,
              action: e.kind === 'daangn_message' && messagesRef.current ? { label: '메시지 상태 보기', run: () => messagesRef.current?.() } : e.leadId ? { label: '보기', run: () => openRef.current(e.leadId as string) } : undefined,
            },
            8000,
          );
          break;
        case 'stream.reset':
          resetting = true;
          pending = false;
          if (timer) clearTimeout(timer);
          timer = undefined;
          listRevision.current += 1;
          refreshSerial.current += 1;
          setNewLeads(0);
          setListStale(true);
          {
            const revision = listRevision.current;
            void resetDatabaseQueries(qc).then(() => {
              if (!disposed && listRevision.current === revision) setListStale(false);
            }).catch(() => { if (!disposed) setListStale(true); }).finally(() => {
              resetting = false;
              if (!disposed && pending && !refreshing && !timer) timer = setTimeout(() => void refreshLists(), 250);
            });
          }
          break;
      }
    };
    const unsubscribe = port.subscribe(handle, (status) => {
      if (disposed) return;
      if (status === 'retrying' || status === 'closed') { disconnected = true; scheduleRefresh(); }
      if (status === 'open' && disconnected) {
        refreshAfterGap();
        disconnected = false;
      }
      setStream(status);
    });
    // Local DB reads only: this does not start collection or submit anything.
    const fallback = setInterval(() => { if (disconnected) refreshAfterGap(); }, 30_000);
    return () => {
      disposed = true;
      scheduleRef.current = () => undefined;
      if (timer) clearTimeout(timer);
      clearInterval(fallback);
      unsubscribe();
    };
  }, [port, qc, toast]);

  useEffect(() => {
    if (!runs.data) return;
    const current = new Map(runs.data.map((r) => [r.id, r.state]));
    const previous = polledRuns.current;
    polledRuns.current = current;
    if (previous && runs.data.some((r) => ['succeeded', 'partial', 'failed', 'cancelled'].includes(r.state) && previous.get(r.id) !== r.state)) {
      scheduleRef.current();
      void qc.invalidateQueries({ queryKey: ['counts'] });
      void qc.invalidateQueries({ queryKey: qk.sources });
    }
  }, [runs.data, qc]);

  return {
    stream,
    newLeads,
    listStale,
    acknowledge: async () => {
      const revision = listRevision.current;
      const serial = ++refreshSerial.current;
      try {
        await qc.refetchQueries({ queryKey: qk.leadsAll, type: 'active' }, { throwOnError: true, cancelRefetch: false });
      } catch {
        if (refreshSerial.current === serial) setListStale(true);
        return false;
      }
      if (refreshSerial.current !== serial) return false;
      if (listRevision.current === revision) {
        setNewLeads(0);
        setListStale(false);
      }
      void qc.invalidateQueries({ queryKey: ['counts'] });
      return true;
    },
  };
}
