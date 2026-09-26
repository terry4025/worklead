/**
 * 이벤트 구독 → 캐시 갱신. 새 리드는 목록에 바로 끼워 넣지 않고 "새 리드 N건" 으로 알린다
 * (읽는 중인 목록이 움직이지 않도록).
 */
import { nativeNotify } from '../platform/notify';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import type { LeadDetail, RunInfo, StreamStatus, WorkleadEvent } from '../domain/model';
import { useToast } from '../ui/Toasts';
import { usePort } from './PortContext';
import { applyDetail, qk } from './queries';

export interface LiveState {
  stream: StreamStatus;
  newLeads: number;
  listStale: boolean;
  acknowledge: () => void;
}

export function useLiveEvents(onOpenLead: (id: string) => void): LiveState {
  const port = usePort();
  const qc = useQueryClient();
  const toast = useToast();
  const [stream, setStream] = useState<StreamStatus>('connecting');
  const [newLeads, setNewLeads] = useState(0);
  const [listStale, setListStale] = useState(false);
  const openRef = useRef(onOpenLead);
  openRef.current = onOpenLead;

  useEffect(() => {
    const refreshLead = (leadId: string) => {
      if (!qc.getQueryData(qk.lead(leadId)) && !qc.getQueryState(qk.lead(leadId))) return;
      port
        .getLead(leadId)
        .then((d: LeadDetail) => applyDetail(qc, d))
        .catch(() => undefined);
    };
    const handle = (e: WorkleadEvent) => {
      switch (e.type) {
        case 'run.progress':
          qc.setQueryData<RunInfo[]>(qk.runs, (runs) => runs?.map((r) => (r.id === e.runId ? { ...r, state: 'running', progress: e.progress } : r)));
          if (!qc.getQueryData<RunInfo[]>(qk.runs)?.some((r) => r.id === e.runId)) void qc.invalidateQueries({ queryKey: qk.runs });
          break;
        case 'run.state_changed':
          void qc.invalidateQueries({ queryKey: qk.runs });
          void qc.invalidateQueries({ queryKey: qk.sources });
          if (['succeeded', 'partial', 'failed'].includes(e.state)) {
            void qc.invalidateQueries({ queryKey: ['counts'] });
            setListStale(true);
          }
          break;
        case 'lead.created':
          setNewLeads((n) => n + 1);
          void qc.invalidateQueries({ queryKey: ['counts'] });
          break;
        case 'lead.updated':
        case 'analysis.completed':
          refreshLead(e.leadId);
          void qc.invalidateQueries({ queryKey: ['counts'] });
          break;
        case 'source.health_changed':
          void qc.invalidateQueries({ queryKey: qk.sources });
          break;
        case 'notification.created':
          // 창을 보고 있지 않을 때만 Windows 알림 (보고 있으면 앱 안 알림으로 충분)
          if (typeof document !== 'undefined' && (document.hidden || !document.hasFocus())) void nativeNotify('Worklead', e.title);
          toast.show(
            {
              tone: e.kind === 'source_issue' ? 'bad' : e.kind === 'lead_changed' ? 'warn' : 'info',
              text: e.title,
              action: e.leadId ? { label: '보기', run: () => openRef.current(e.leadId as string) } : undefined,
            },
            8000,
          );
          break;
        case 'stream.reset':
          void qc.invalidateQueries();
          break;
      }
    };
    return port.subscribe(handle, setStream);
  }, [port, qc, toast]);

  return {
    stream,
    newLeads,
    listStale,
    acknowledge: () => {
      setNewLeads(0);
      setListStale(false);
      void qc.invalidateQueries({ queryKey: qk.leadsAll });
      void qc.invalidateQueries({ queryKey: ['counts'] });
    },
  };
}
