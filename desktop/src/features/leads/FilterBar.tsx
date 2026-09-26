import { Search, X } from 'lucide-react';
import { forwardRef, useEffect, useState } from 'react';
import type { IntentFilter, LeadFilter, RecruitFilter, RemoteFilter, SortKey, SourceInfo } from '../../domain/model';
import { intentFilterOptions, recruitFilterOptions, remoteFilterOptions } from '../../domain/present';

export const DEFAULT_FILTER: LeadFilter = {
  keyword: '',
  sourceId: null,
  category: null,
  remote: 'any',
  recruit: 'any',
  intent: 'any',
  sort: 'priority',
};

export function isDefaultFilter(f: LeadFilter): boolean {
  return (Object.keys(DEFAULT_FILTER) as (keyof LeadFilter)[]).every((k) => f[k] === DEFAULT_FILTER[k]);
}

const SORTS: { value: SortKey; label: string }[] = [
  { value: 'priority', label: '우선순위순' },
  { value: 'published', label: '최신 게시순' },
  { value: 'checked', label: '최근 확인순' },
];

export const FilterBar = forwardRef<HTMLInputElement, {
  filter: LeadFilter;
  onChange: (f: LeadFilter) => void;
  sources: SourceInfo[];
  categories: { id: string; label: string }[];
}>(function FilterBar({ filter, onChange, sources, categories }, searchRef) {
  const [kw, setKw] = useState(filter.keyword);
  useEffect(() => setKw(filter.keyword), [filter.keyword]);
  useEffect(() => {
    if (kw === filter.keyword) return;
    const t = window.setTimeout(() => onChange({ ...filter, keyword: kw }), 250);
    return () => window.clearTimeout(t);
  }, [kw, filter, onChange]);

  const set = <K extends keyof LeadFilter>(k: K, v: LeadFilter[K]) => onChange({ ...filter, [k]: v });

  return (
    <div className="filterbar" role="search" aria-label="리드 필터">
      <label className="search">
        <Search size={15} aria-hidden="true" />
        <span className="sr-only">검색</span>
        <input
          ref={searchRef}
          type="search"
          placeholder="제목·본문 검색  ( / )"
          value={kw}
          onChange={(e) => setKw(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              if (kw) setKw('');
              else (e.target as HTMLInputElement).blur();
            }
            if (e.key === 'Enter') onChange({ ...filter, keyword: kw });
          }}
        />
        {kw ? (
          <button type="button" className="icon-btn icon-btn-sm" aria-label="검색어 지우기" onClick={() => setKw('')}>
            <X size={14} aria-hidden="true" />
          </button>
        ) : null}
      </label>

      <Select label="사이트" value={filter.sourceId ?? ''} onChange={(v) => set('sourceId', v || null)} options={[{ value: '', label: '사이트 전체' }, ...sources.map((s) => ({ value: s.id, label: s.name }))]} />
      <Select label="업무" value={filter.category ?? ''} onChange={(v) => set('category', v || null)} options={[{ value: '', label: '업무 전체' }, ...categories.map((c) => ({ value: c.id, label: c.label }))]} />
      <Select label="재택" value={filter.remote} onChange={(v) => set('remote', v as RemoteFilter)} options={remoteFilterOptions} />
      <Select label="모집" value={filter.recruit} onChange={(v) => set('recruit', v as RecruitFilter)} options={recruitFilterOptions} />
      <Select label="유형" value={filter.intent} onChange={(v) => set('intent', v as IntentFilter)} options={intentFilterOptions} />
      <Select label="정렬" value={filter.sort} onChange={(v) => set('sort', v as SortKey)} options={SORTS} />
      {!isDefaultFilter(filter) ? (
        <button type="button" className="link-btn" onClick={() => onChange(DEFAULT_FILTER)}>
          필터 초기화
        </button>
      ) : null}
    </div>
  );
});

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  const active = options[0]?.value !== value;
  return (
    <label className={`select${active ? ' is-active' : ''}`}>
      <span className="sr-only">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
