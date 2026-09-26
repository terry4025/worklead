import { useRef } from 'react';
import type { QueueId } from '../../domain/model';
import { VIEWS, type ViewId } from '../../domain/queues';

export function viewCount(view: ViewId, counts: Record<QueueId, number> | null | undefined): number | null {
  if (!counts) return null;
  const def = VIEWS.find((v) => v.id === view);
  return def ? def.sections.reduce((a, s) => a + (counts[s.queue] ?? 0), 0) : null;
}

export function QueueTabs({ view, onChange, counts }: { view: ViewId; onChange: (v: ViewId) => void; counts: Record<QueueId, number> | null | undefined }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div className="tabs" role="tablist" aria-label="보기">
      {VIEWS.map((v, i) => {
        const n = viewCount(v.id, counts);
        const sel = v.id === view;
        return (
          <button
            key={v.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            aria-selected={sel}
            aria-controls="lead-list"
            tabIndex={sel ? 0 : -1}
            className={`tab${sel ? ' is-active' : ''}`}
            title={`${v.label} (${v.key})`}
            onClick={() => onChange(v.id)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
              e.preventDefault();
              const next = (i + (e.key === 'ArrowRight' ? 1 : -1) + VIEWS.length) % VIEWS.length;
              const nv = VIEWS[next];
              if (nv) {
                onChange(nv.id);
                refs.current[next]?.focus();
              }
            }}
          >
            <span>{v.label}</span>
            {n !== null ? <span className="tab-count num">{n}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
