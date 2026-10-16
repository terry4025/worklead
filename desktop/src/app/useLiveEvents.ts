/**
 * 이벤트 구독 → 캐시 갱신. 새 리드는 목록에 바로 끼워 넣지 않고 "새 리드 N건" 으로 알린다
 * (읽는 중인 목록이 움직이지 않도록).
 */
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import type { LeadDetail, RunInfo, StreamStatus, WorkleadEvent } from '../domain/model';
import { useToast } from '../ui/Toasts';
import { usePort } from './PortContext';
import { applyDetail, qk } from './queries';
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
  const openRef = useRef(onOpenLead);
  openRef.current = onOpenLead;
  const messagesRef = useRef(onOpenMessages);
  messagesRef.current = onOpenMessages;

  useEffect(() => {
    let disconnected = false;
    let disposed = false;
    const refreshAfterGap = () => {
      void qc.invalidateQueries({ queryKey: qk.sources });
      void qc.invalidateQueries({ queryKey: qk.runs });
      void qc.invalidateQueries({ queryKey: ['counts'] });
      void qc.invalidateQueries({ queryKey: ['lead'] });
      void qc.invalidateQueries({ queryKey: ['jobs'] });
      listRevision.current += 1;
      setListStale(true);
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
          qc.setQueriesData<RunInfo[]>({ queryKey: qk.runs }, (runs) => runs?.map((r) => (r.id === e.runId ? { ...r, state: 'running', progress: e.progress } : r)));
          if (!qc.getQueryData<RunInfo[]>([...qk.runs, 'status'])?.some((r) => r.id === e.runId)) void qc.invalidateQueries({ queryKey: qk.runs });
          break;
        case 'run.state_changed':
          void qc.invalidateQueries({ queryKey: qk.runs });
          void qc.invalidateQueries({ queryKey: qk.sources });
          if (['succeeded', 'partial', 'failed'].includes(e.state)) {
            void qc.invalidateQueries({ queryKey: ['counts'] });
            listRevision.current += 1;
            setListStale(true);
          }
          break;
        case 'lead.created':
          void qc.invalidateQueries({ queryKey: ['jobs', 'applications'] });
          listRevision.current += 1;
          if (e.recommendation !== 'excluded') setNewLeads((n) => n + 1);
          void qc.invalidateQueries({ queryKey: ['counts'] });
          break;
        case 'lead.updated':
        case 'analysis.completed':
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
          listRevision.current += 1;
          refreshSerial.current += 1;
          setNewLeads(0);
          setListStale(true);
          {
            const revision = listRevision.current;
            void resetDatabaseQueries(qc).then(() => {
              if (!disposed && listRevision.current === revision) setListStale(false);
            }).catch(() => { if (!disposed) setListStale(true); });
          }
          break;
      }
    };
    const unsubscribe = port.subscribe(handle, (status) => {
      if (status === 'retrying' || status === 'closed') disconnected = true;
      if (status === 'open' && disconnected) {
        refreshAfterGap();
        disconnected = false;
      }
      setStream(status);
    });
    return () => { disposed = true; unsubscribe(); };
  }, [port, qc, toast]);

  return {
    stream,
    newLeads,
    listStale,
    acknowledge: async () => {
      const revision = listRevision.current;
      const serial = ++refreshSerial.current;
      try {
        await qc.refetchQueries({ queryKey: qk.leadsAll, type: 'active' }, { throwOnError: true });
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
