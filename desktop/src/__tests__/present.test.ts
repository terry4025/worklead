import { describe, expect, it } from 'vitest';
import { recruitClass, recruitDisplay, remoteClass } from '../domain/present';
import { inQueue } from '../domain/queues';
import { safeExternalUrl } from '../platform/external';

describe('재택 표시', () => {
  it('근거 유형으로 확인/추정을 구분한다', () => {
    expect(remoteClass({ workMode: { value: 'fully_remote', basis: 'explicit' } })).toBe('confirmed');
    expect(remoteClass({ workMode: { value: 'fully_remote', basis: 'user_confirmed' } })).toBe('confirmed');
    expect(remoteClass({ workMode: { value: 'fully_remote', basis: 'inferred' } })).toBe('inferred');
    expect(remoteClass({ workMode: { value: 'unknown', basis: null } })).toBe('unknown');
  });
});

describe('모집 상태 표시', () => {
  it('접근 오류는 마감이 아니다', () => {
    const l = { sourceStatus: 'open' as const, accessStatus: 'blocked' as const, recheckDue: true, lastCheckedAt: null };
    expect(recruitClass(l)).toBe('access_issue');
    const d = recruitDisplay(l);
    expect(d.label).toBe('접근 차단');
    expect(d.detail).toContain('마지막 상태 모집 중');
  });
  it('마감 표시 없음(unknown)을 모집 중으로 바꾸지 않는다', () => {
    expect(recruitClass({ sourceStatus: 'unknown', accessStatus: 'accessible', recheckDue: false })).toBe('unknown');
  });
  it('TTL 이 지나면 재확인 필요', () => {
    expect(recruitClass({ sourceStatus: 'open', accessStatus: 'accessible', recheckDue: true })).toBe('recheck');
  });
});

describe('대기열 규칙 (백엔드와 동일)', () => {
  const base = { recommendation: 'recommended' as const, userMark: null, salesStage: 'new' as const };
  it('제외하면 추천에서 빠지고 내가 제외로 간다', () => {
    expect(inQueue(base, 'recommended')).toBe(true);
    const dismissed = { ...base, userMark: 'dismissed' as const };
    expect(inQueue(dismissed, 'recommended')).toBe(false);
    expect(inQueue(dismissed, 'dismissed')).toBe(true);
  });
  it('연락하면 진행 중으로 간다', () => {
    const contacted = { ...base, salesStage: 'contacted' as const };
    expect(inQueue(contacted, 'recommended')).toBe(false);
    expect(inQueue(contacted, 'active')).toBe(true);
  });
});

describe('외부 링크', () => {
  it('http/https 만 허용', () => {
    expect(safeExternalUrl('https://jobs.example.com/a?b=1')).toBe('https://jobs.example.com/a?b=1');
    expect(safeExternalUrl('javascript:alert(1)')).toBeNull();
    expect(safeExternalUrl('file:///C:/Windows/system32')).toBeNull();
    expect(safeExternalUrl('data:text/html,hi')).toBeNull();
    expect(safeExternalUrl('myapp://open')).toBeNull();
    expect(safeExternalUrl(' java\nscript:alert(1)')).toBeNull();
    expect(safeExternalUrl('https://user:pw@evil.example')).toBeNull();
    expect(safeExternalUrl(null)).toBeNull();
  });
});
