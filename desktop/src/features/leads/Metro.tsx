import { useQuery } from '@tanstack/react-query';
import { usePort } from '../../app/PortContext';
import type { LeadSummary } from '../../domain/model';
import { salesStageLabel } from '../../domain/labels';
import { Button } from '../../ui/Button';
import { autoApplyKey } from '../jobs/AutoApplyPanel';

type Tone = 'line1' | 'line2' | 'line3' | 'sb' | 'off';

function Stop({ tone, label, value, reached = true }: { tone: Tone; label: string; value?: React.ReactNode; reached?: boolean }) {
  return (
    <li className="metro-stop" data-tone={tone} data-reached={reached || undefined}>
      <span className="metro-dot" aria-hidden="true" />
      <span className="metro-label">{label}</span>
      {value !== undefined ? <b className="metro-value num">{value}</b> : null}
    </li>
  );
}

/** 리드 화면 맨 위: 자동 지원이 오늘 어디까지 왔는지를 노선 한 줄로 보인다. */
export function AutoApplyLine({ onOpenJobs, onOpenLead }: { onOpenJobs: () => void; onOpenLead: (id: string) => void }) {
  const port = usePort();
  const q = useQuery({ queryKey: autoApplyKey, queryFn: () => port.jobs.autoApplyStatus(), refetchInterval: 10000 });
  const s = q.data;
  const state = q.isError ? { text: '상태를 읽지 못함', tone: 'sb' as Tone }
    : !s ? { text: '확인 중', tone: 'off' as Tone }
    : s.needs_user_action ? { text: '확인 필요', tone: 'line3' as Tone }
    : s.enabled && s.state === 'connected' ? { text: '운행 중', tone: 'line2' as Tone }
    : s.enabled ? { text: '연결 대기', tone: 'line3' as Tone }
    : { text: '정지', tone: 'off' as Tone };
  const recent = (s?.attempts ?? []).filter((a) => a.state === 'submitted').slice(0, 3);
  return (
    <section className="metro" aria-label="자동 지원 노선">
      <div className="metro-head">
        <span className="metro-badge">자동 지원</span>
        <span className="metro-state" data-tone={state.tone}>{state.text}</span>
      </div>
      <ol className="metro-line">
        <Stop tone="line1" label="대기 후보" value={s ? (s.candidates?.length ?? 0) : '–'} reached={!!s?.candidates?.length} />
        <Stop tone="line2" label="오늘 지원" value={s ? `${s.today_submitted}/${s.daily_limit}` : '–'} reached={!!s && s.today_submitted > 0} />
        <Stop tone="line3" label="보류" value={s ? s.held : '–'} reached={!!s && s.held > 0} />
        <Stop tone="sb" label="결과 미확인" value={s ? s.uncertain : '–'} reached={!!s && s.uncertain > 0} />
      </ol>
      <div className="metro-recent">
        {recent.length ? (
          <>
            <span className="metro-recent-label">최근 지원</span>
            {recent.map((a) => (
              <button key={a.id} type="button" className="metro-chip" onClick={() => onOpenLead(a.lead_id)} title={a.title}>
                {a.title}
              </button>
            ))}
          </>
        ) : (
          <span className="metro-recent-label">아직 나간 지원이 없습니다</span>
        )}
      </div>
      <Button size="sm" onClick={onOpenJobs}>자동 지원 열기</Button>
    </section>
  );
}

const APPLIED = new Set(['contacted', 'negotiating', 'won', 'lost', 'on_hold']);

/** 상세 머리: 공고 하나가 발견 → 판정 → 지원 → 진행 중 어디에 있는지. */
export function JourneyLine({ lead }: { lead: LeadSummary }) {
  const verdict = lead.recommendation === 'recommended' ? { label: '추천', tone: 'line2' as Tone }
    : lead.recommendation === 'needs_review' ? { label: '검토 필요', tone: 'line3' as Tone }
    : { label: '제외', tone: 'sb' as Tone };
  const applied = APPLIED.has(lead.salesStage);
  const beyond = applied && lead.salesStage !== 'contacted';
  return (
    <ol className="metro-line journey" aria-label="공고 진행 노선">
      <Stop tone="line1" label="발견" />
      <Stop tone={verdict.tone} label={verdict.label} />
      <Stop tone="line2" label={applied ? '지원함' : '지원 전'} reached={applied} />
      <Stop tone="line1" label={beyond ? salesStageLabel[lead.salesStage] : '진행'} reached={beyond} />
    </ol>
  );
}
