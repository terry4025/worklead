import { AlertTriangle, PlugZap } from 'lucide-react';
import type { ReactNode } from 'react';
import { PortError, toPortError } from '../data/port';
import { Button } from './Button';

export function EmptyState({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="state state-empty">
      <p className="state-title">{title}</p>
      {children ? <div className="state-body">{children}</div> : null}
      {actions ? <div className="state-actions">{actions}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  compact,
  extra,
}: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
  extra?: ReactNode;
}) {
  const e = error instanceof PortError ? error : toPortError(error);
  const conn = e.kind === 'connection';
  return (
    <div className={`state state-error${compact ? ' state-compact' : ''}`} role="alert">
      <p className="state-title">
        {conn ? <PlugZap size={16} aria-hidden="true" /> : <AlertTriangle size={16} aria-hidden="true" />}
        {conn ? '로컬 서비스에 연결할 수 없습니다' : '불러오지 못했습니다'}
      </p>
      <div className="state-body">
        {e.message !== '로컬 서비스에 연결할 수 없습니다' ? <p>{e.message}</p> : <p>데스크톱 앱이 백엔드를 시작하지 못했거나 연결이 끊겼습니다.</p>}
        <p className="state-meta">
          오류 코드 <code>{e.code}</code>
          {e.requestId ? (
            <>
              {' '}
              · 요청 ID <code>{e.requestId}</code>
            </>
          ) : null}
        </p>
      </div>
      {(onRetry && (e.retryable || conn)) || extra ? (
        <div className="state-actions">
          {onRetry && (e.retryable || conn) ? (
            <Button size="sm" onClick={onRetry}>
              다시 시도
            </Button>
          ) : null}
          {extra}
        </div>
      ) : null}
    </div>
  );
}

export function RowSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="skeleton-list" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-row">
          <span className="sk sk-score" />
          <span className="sk sk-title" />
          <span className="sk sk-meta" />
        </div>
      ))}
    </div>
  );
}

export function errorMessage(err: unknown): string {
  return toPortError(err).message;
}
