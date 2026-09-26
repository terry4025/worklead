import type { ReactNode } from 'react';
import type { Tone } from '../domain/present';

/**
 * 상태 표시: 기호 모양(채움·반채움·빈 원·마름모·빗금) + 색 + 글자.
 * 색을 구분하기 어려워도 모양과 글자로 읽힌다.
 */
export function StatusGlyph({ tone }: { tone: Tone }) {
  const common = { width: 10, height: 10, viewBox: '0 0 10 10', 'aria-hidden': true, className: `glyph glyph-${tone}` } as const;
  switch (tone) {
    case 'good':
      return (
        <svg {...common}>
          <circle cx="5" cy="5" r="4.2" fill="currentColor" />
        </svg>
      );
    case 'warn':
      return (
        <svg {...common}>
          <circle cx="5" cy="5" r="3.9" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M5 0.9 A4.1 4.1 0 0 0 5 9.1 Z" fill="currentColor" />
        </svg>
      );
    case 'unknown':
      return (
        <svg {...common}>
          <circle cx="5" cy="5" r="3.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2.2 1.6" />
        </svg>
      );
    case 'bad':
      return (
        <svg {...common}>
          <path d="M5 0.6 L9.4 5 L5 9.4 L0.6 5 Z" fill="currentColor" />
        </svg>
      );
    case 'muted':
      return (
        <svg {...common}>
          <circle cx="5" cy="5" r="3.9" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <path d="M2.4 7.6 L7.6 2.4" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      );
  }
}

export function StatusMark({ tone, children, title, className }: { tone: Tone; children: ReactNode; title?: string; className?: string }) {
  return (
    <span className={`status status-${tone}${className ? ` ${className}` : ''}`} title={title}>
      <StatusGlyph tone={tone} />
      <span className="status-text">{children}</span>
    </span>
  );
}
