import type { FuzzyDate, IsoDateTime, Pay } from './model';
import { payUnitLabel } from './labels';

const TZ = 'Asia/Seoul';

const numberFmt = new Intl.NumberFormat('ko-KR');

/**
 * 원화 금액 표기. 10만원 미만은 원 단위(시급 등), 이상은 만원 단위로 줄인다.
 * null 은 호출하는 쪽에서 '미기재' 등으로 처리한다. 0 은 실제 0원일 때만 들어온다.
 */
export function formatKrw(amount: number): string {
  const sign = amount < 0 ? '−' : '';
  const n = Math.abs(amount);
  if (n < 100_000) return `${sign}${numberFmt.format(n)}원`;
  const eok = Math.floor(n / 100_000_000);
  const man = (n % 100_000_000) / 10_000;
  const manText = man === 0 ? '' : `${numberFmt.format(Math.round(man * 10) / 10)}만`;
  if (eok > 0) return `${sign}${numberFmt.format(eok)}억${manText ? ` ${manText}` : ''}원`;
  return `${sign}${manText}원`;
}

function formatRange(min: number | null, max: number | null): string | null {
  if (min === null && max === null) return null;
  if (min !== null && max !== null && min !== max) {
    // 같은 만원 단위면 "100~200만원" 으로 줄인다
    if (min >= 100_000 && max >= 100_000 && max < 100_000_000) {
      const a = numberFmt.format(Math.round((min / 10_000) * 10) / 10);
      const b = numberFmt.format(Math.round((max / 10_000) * 10) / 10);
      return `${a}~${b}만원`;
    }
    return `${formatKrw(min)}~${formatKrw(max)}`;
  }
  if (min === null) return `최대 ${formatKrw(max as number)}`;
  if (max === null) return `${formatKrw(min)} 이상`;
  return formatKrw(min);
}

export interface PayDisplay {
  /** 목록에 보이는 주 표기 */
  text: string;
  /** 금액 정보가 없음(미기재·협의·미확인) */
  missing: boolean;
  /** 원문 표현 */
  raw: string | null;
}

/** 보수 표기. 금액을 모르면 절대 0원으로 쓰지 않는다. */
export function formatPay(pay: Pay): PayDisplay {
  const range = pay.currency === 'KRW' ? formatRange(pay.min, pay.max) : null;
  if (range === null) {
    if (pay.min !== null || pay.max !== null) {
      // 원화 외 통화: 변환하지 않고 원문 우선
      return { text: pay.raw ?? `${pay.currency} ${pay.min ?? ''}~${pay.max ?? ''}`, missing: false, raw: pay.raw };
    }
    if (pay.unit === 'negotiable' || pay.negotiable) return { text: '금액 협의', missing: true, raw: pay.raw };
    if (pay.raw) return { text: '금액 해석 불가', missing: true, raw: pay.raw };
    return { text: '예산 미기재', missing: true, raw: null };
  }
  const unit = pay.unit === 'negotiable' ? '' : pay.unit === 'unknown' ? '' : `${payUnitLabel[pay.unit]} `;
  const suffix = pay.unit === 'unknown' ? ' (단위 미확인)' : '';
  const nego = pay.negotiable && pay.unit !== 'negotiable' ? ' · 협의 가능' : '';
  return { text: `${unit}${range}${suffix}${nego}`, missing: false, raw: pay.raw };
}

const dateFmt = new Intl.DateTimeFormat('ko-KR', { timeZone: TZ, month: 'long', day: 'numeric' });
const dateYearFmt = new Intl.DateTimeFormat('ko-KR', { timeZone: TZ, year: 'numeric', month: 'long', day: 'numeric' });
const timeFmt = new Intl.DateTimeFormat('ko-KR', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
const fullFmt = new Intl.DateTimeFormat('ko-KR', {
  timeZone: TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function yearOf(d: Date): number {
  return Number(new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric' }).format(d));
}

/** 한국 시간 날짜 (올해면 연도 생략) */
export function formatDate(iso: IsoDateTime, now: Date = new Date()): string {
  const d = new Date(iso);
  return yearOf(d) === yearOf(now) ? dateFmt.format(d) : dateYearFmt.format(d);
}

export function formatTime(iso: IsoDateTime): string {
  return timeFmt.format(new Date(iso));
}

export function formatDateTime(iso: IsoDateTime, now: Date = new Date()): string {
  return `${formatDate(iso, now)} ${formatTime(iso)}`;
}

/** 툴팁용 전체 표기 (KST 명시) */
export function formatFull(iso: IsoDateTime): string {
  return `${fullFmt.format(new Date(iso))} (한국 시간)`;
}

/** 상대 시간. 1주 이상은 날짜로. */
export function formatRelative(iso: IsoDateTime, now: Date = new Date()): string {
  const diffMs = now.getTime() - new Date(iso).getTime();
  // 몇 분 이내의 시계 차이는 미래로 표시하지 않는다
  const future = diffMs < -5 * 60_000;
  const abs = Math.abs(diffMs);
  const min = Math.floor(abs / 60_000);
  if (min < 1) return future ? '곧' : '방금';
  if (min < 60) return future ? `${min}분 후` : `${min}분 전`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return future ? `${hours}시간 후` : `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 7) return future ? `${days}일 후` : `${days}일 전`;
  return formatDate(iso, now);
}

/** 정밀도를 드러내는 날짜 표기. 부정확한 날짜를 정확한 시각처럼 쓰지 않는다. */
export function formatFuzzyDate(value: FuzzyDate, now: Date = new Date()): string {
  if (!value.at) return value.raw ? `원문 "${value.raw}" (날짜 미확정)` : '미확인';
  switch (value.precision) {
    case 'exact':
      return formatDateTime(value.at, now);
    case 'day':
      return formatDate(value.at, now);
    case 'approximate':
      return `${formatDate(value.at, now)}경${value.raw ? ` (원문 "${value.raw}")` : ''}`;
    case 'unknown':
      return value.raw ? `원문 "${value.raw}"` : '미확인';
  }
}

/** 목록용 짧은 표기 */
export function formatFuzzyShort(value: FuzzyDate, now: Date = new Date()): string {
  if (!value.at) return '미확인';
  if (value.precision === 'approximate') return `${formatDate(value.at, now)}경`;
  if (value.precision === 'unknown') return '미확인';
  return formatDate(value.at, now);
}

export function formatCount(n: number): string {
  return numberFmt.format(n);
}

/** 오늘 날짜 (KST) YYYY-MM-DD */
export function todayKst(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
  return parts;
}
