import { useMemo, useState } from 'react';

export interface Mark {
  id: string;
  start: number;
  end: number;
  tone: 'evidence' | 'risk';
}

/**
 * 원문 본문을 안전한 텍스트로 표시한다 (HTML 해석 없음). 근거 구간을 강조하고,
 * 선택된 근거(activeId)는 더 진하게 표시한다.
 */
export function SourceText({ text, marks, activeId, collapsedLines = 12 }: { text: string; marks: Mark[]; activeId: string | null; collapsedLines?: number }) {
  const [expanded, setExpanded] = useState(false);
  const segments = useMemo(() => {
    const valid = marks
      .filter((m) => m.start >= 0 && m.end <= text.length && m.end > m.start)
      .sort((a, b) => a.start - b.start || b.end - a.end);
    const out: { text: string; mark: Mark | null }[] = [];
    let pos = 0;
    for (const m of valid) {
      if (m.start < pos) continue; // 겹치는 구간은 앞의 것만
      if (m.start > pos) out.push({ text: text.slice(pos, m.start), mark: null });
      out.push({ text: text.slice(m.start, m.end), mark: m });
      pos = m.end;
    }
    if (pos < text.length) out.push({ text: text.slice(pos), mark: null });
    return out;
  }, [text, marks]);

  const long = text.split('\n').length > collapsedLines || text.length > 900;
  const activeInside = activeId !== null && marks.some((m) => m.id === activeId);
  const show = expanded || !long || activeInside;

  return (
    <div className="source-text-wrap">
      <div className={`source-text${show ? '' : ' is-collapsed'}`} style={{ '--lines': collapsedLines } as React.CSSProperties}>
        {segments.map((s, i) =>
          s.mark ? (
            <mark key={i} id={`mark-${s.mark.id}`} className={`hl hl-${s.mark.tone}${s.mark.id === activeId ? ' is-active' : ''}`}>
              {s.text}
            </mark>
          ) : (
            <span key={i}>{s.text}</span>
          ),
        )}
      </div>
      {long && !activeInside ? (
        <button type="button" className="link-btn" onClick={() => setExpanded((v) => !v)} aria-expanded={show}>
          {show ? '접기' : '원문 전체 보기'}
        </button>
      ) : null}
    </div>
  );
}
