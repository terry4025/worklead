import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { usePortCtx } from '../../app/PortContext';
import { getDatabaseEpoch } from '../../app/databaseCache';
import type { AiBudgetPeriod, AiStatus, SettingsView } from '../../domain/model';
import { formatCount, formatKrw } from '../../domain/format';
import { openExternal, safeExternalUrl } from '../../platform/external';
import { Button } from '../../ui/Button';

export const AI_STATUS_KEY = ['ai-status'] as const;
export const AI_EDITABLE = ['enabled', 'externalTransferConsent', 'dailyCostCap', 'monthlyCostCap', 'provider', 'model', 'usdToKrwRate', 'maxOutputTokens', 'pricingConfirmed', 'dailyCallCap', 'assistReview', 'assistRecheck', 'assistApplication', 'assistChat', 'assistQuote'] as const;

export function aiSettingsChanged(a: SettingsView['ai'], b: SettingsView['ai']): boolean {
  return AI_EDITABLE.some((key) => a[key] !== b[key]);
}

function amount(value: string): number | null {
  const text = value.replace(/[,\s원]/g, '');
  if (!text) return null;
  return /^\d+$/.test(text) && Number.isSafeInteger(Number(text)) ? Number(text) : NaN;
}

export function invalidAiSettings(ai: SettingsView['ai']): boolean {
  return [ai.dailyCostCap, ai.monthlyCostCap].some((v) => v !== null && (!Number.isInteger(v) || v < 0 || v > 100_000_000))
    || (ai.usdToKrwRate !== null && (!Number.isInteger(ai.usdToKrwRate) || ai.usdToKrwRate < 1 || ai.usdToKrwRate > 100_000))
    || !Number.isInteger(ai.maxOutputTokens) || ai.maxOutputTokens < 256 || ai.maxOutputTokens > 4096
    || !Number.isInteger(ai.dailyCallCap) || ai.dailyCallCap < 1 || ai.dailyCallCap > 5000;
}

export function aiStatusMatches(ai: SettingsView['ai'], status: AiStatus | null | undefined): boolean {
  return !!status && ai.revision === status.settingsRevision && ai.databaseGeneration === status.databaseGeneration;
}

function BudgetRow({ title, value, cap }: { title: string; value: AiBudgetPeriod; cap: number | null }) {
  const total = value.estimatedKrw + value.reservedKrw + value.uncertainKrw;
  return <div className="ai-budget-row">
    <dt>{title} <span className="muted-text">{value.period}</span></dt>
    <dd>사용 추정 {formatKrw(value.estimatedKrw)} · 예약 {formatKrw(value.reservedKrw)} · 과금 미확인 {formatKrw(value.uncertainKrw)}<br />
      상한 {cap === null ? '미설정' : formatKrw(cap)} · 요청 {formatCount(value.attempts)}회{cap !== null && total >= cap ? ' · 상한 도달' : ''}</dd>
  </div>;
}

function statusLabel(status: AiStatus): string {
  if (status.ready) return 'AI 실행 조건 충족 · API 연결 미검증';
  const reasons: Record<string, string> = {
    disabled: 'AI 꺼짐 · 규칙 분석 사용', consent_required: '외부 전송 동의 필요', not_configured: 'API 키 미설정',
    pricing_required: '요금·환산 확인 필요', budget_required: '일·월 상한 설정 필요', budget_exhausted: '비용 상한 도달 · 규칙 분석 사용',
    demo_credential_unsupported: '데모 · 실제 API 키 저장과 외부 AI 요청 없음', ai_ledger_unavailable: '비용 기록 확인 불가 · AI 중지',
    key_unsupported: 'Windows 보안 저장소를 사용할 수 없음', key_unavailable: 'API 키 저장 상태 확인 불가',
    call_cap_reached: '오늘 호출 상한 도달 · 내일 다시 사용',
  };
  return reasons[status.blockedReason ?? ''] ?? (status.errorCode ? 'AI 상태 확인 불가 · 규칙 분석 사용' : 'AI 실행 조건 확인 필요');
}

export function AiSettingsPanel({ ai, onChange, onStatusChange, onBusyChange, disabled = false }: {
  ai: SettingsView['ai'];
  onChange: (update: (value: SettingsView['ai']) => void) => void;
  onStatusChange: (value: AiStatus | null) => void;
  onBusyChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  const { port, mode } = usePortCtx();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: AI_STATUS_KEY, queryFn: async () => {
    const epoch = getDatabaseEpoch(qc);
    const value = await port.getAiStatus();
    if (epoch !== getDatabaseEpoch(qc)) throw new DOMException('Database changed', 'AbortError');
    const current = qc.getQueryData<AiStatus>(AI_STATUS_KEY);
    // A status read started before a credential write must not lower its revision.
    return current?.databaseGeneration === value.databaseGeneration && current.settingsRevision > value.settingsRevision ? current : value;
  }, retry: false, refetchInterval: 15_000, refetchOnWindowFocus: false });
  const input = useRef<HTMLInputElement>(null);
  const operation = useRef(false);
  const mounted = useRef(false);
  const serial = useRef(0);
  const [pending, setPending] = useState<'save' | 'delete' | 'reload' | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasKeyInput, setHasKeyInput] = useState(false);
  const [credentialUnknown, setCredentialUnknown] = useState(false);
  const clearInput = useCallback(() => { if (input.current) input.current.value = ''; setHasKeyInput(false); }, []);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; serial.current += 1; operation.current = false; if (input.current) input.current.value = ''; }; }, []);
  useEffect(() => { clearInput(); setNotice(null); setError(null); }, [ai.provider, ai.model, ai.databaseGeneration, clearInput]);
  useEffect(() => { onStatusChange(status.isError || credentialUnknown ? null : status.data ?? null); }, [status.data, status.isError, credentialUnknown, onStatusChange]);
  useEffect(() => { onBusyChange(pending !== null); return () => onBusyChange(false); }, [pending, onBusyChange]);

  const synchronized = !status.isError && !credentialUnknown && aiStatusMatches(ai, status.data);
  const providerSaved = status.data?.provider === ai.provider;
  const credentialAllowed = mode === 'live' && synchronized && providerSaved && status.data?.keySupported && !disabled && !pending;
  const hasBudget = (period: AiBudgetPeriod, cap: number | null) => cap !== null && period.estimatedKrw + period.reservedKrw + period.uncertainKrw < cap;
  const exaone = ai.provider === 'exaone';
  const canEnable = exaone
    ? synchronized && providerSaved && status.data?.keyConfigured === true && !status.data.errorCode && ai.externalTransferConsent
      && Number.isInteger(ai.dailyCallCap) && ai.dailyCallCap >= 1 && ai.dailyCallCap <= 5000 && !invalidAiSettings(ai)
    : synchronized && providerSaved && status.data?.keyConfigured === true && status.data.providerAvailable && !status.data.errorCode && status.data.ledger !== null
    && ai.externalTransferConsent && ai.pricingConfirmed && ai.usdToKrwRate !== null && ai.dailyCostCap !== null && ai.dailyCostCap > 0
    && ai.monthlyCostCap !== null && ai.monthlyCostCap > 0 && hasBudget(status.data.ledger.day, ai.dailyCostCap) && hasBudget(status.data.ledger.month, ai.monthlyCostCap) && !invalidAiSettings(ai);

  const credential = async (action: 'save' | 'delete') => {
    if (!credentialAllowed || operation.current || (action === 'save' && !input.current?.value)) return;
    operation.current = true;
    const request = ++serial.current;
    const epoch = getDatabaseEpoch(qc);
    const expected = { expectedRevision: ai.revision, expectedGeneration: ai.databaseGeneration };
    setPending(action); setNotice(null); setError(null);
    try {
      // Never use useMutation here: it retains submitted variables in its cache.
      // Clear the DOM immediately after constructing the one credential request.
      let response: Promise<AiStatus>;
      try { response = action === 'save' ? port.saveAiCredential({ ...expected, key: input.current!.value }) : port.deleteAiCredential(expected); }
      finally { clearInput(); }
      const result = await response;
      if (!mounted.current || request !== serial.current || epoch !== getDatabaseEpoch(qc) || result.databaseGeneration !== expected.expectedGeneration) return;
      const cached = qc.getQueryData<AiStatus>(AI_STATUS_KEY);
      if (cached && cached.databaseGeneration === result.databaseGeneration && cached.settingsRevision > result.settingsRevision) { setCredentialUnknown(true); return; }
      qc.setQueryData(AI_STATUS_KEY, result);
      setCredentialUnknown(false);
      onChange((value) => { value.enabled = false; value.keyConfigured = result.keyConfigured; value.providerAvailable = result.providerAvailable; value.revision = result.settingsRevision; });
      setNotice(action === 'save' ? 'API 키를 Windows 보안 저장소에 저장했습니다. AI 분석은 꺼졌습니다.' : 'API 키를 삭제했습니다. AI 분석은 꺼졌습니다.');
    } catch {
      if (!mounted.current || request !== serial.current || epoch !== getDatabaseEpoch(qc)) return;
      setCredentialUnknown(true);
      onChange((value) => { value.enabled = false; });
      setError('키 변경 결과를 확인하지 못했습니다. 키를 다시 보내지 않고 현재 저장 상태를 확인합니다.');
      try {
        const current = await port.getAiStatus();
        if (mounted.current && request === serial.current && epoch === getDatabaseEpoch(qc) && current.databaseGeneration === expected.expectedGeneration) {
          qc.setQueryData(AI_STATUS_KEY, current);
          setCredentialUnknown(false);
          onChange((value) => { value.enabled = false; value.keyConfigured = current.keyConfigured; value.providerAvailable = current.providerAvailable; value.revision = current.settingsRevision; });
        }
      } catch { /* Keep uncertainty visible. A read failure must not replay a write. */ }
    } finally {
      if (request === serial.current) { operation.current = false; if (mounted.current) setPending(null); }
    }
  };

  const reloadAi = async () => {
    if (disabled || pending || operation.current) return;
    operation.current = true; const request = ++serial.current; const epoch = getDatabaseEpoch(qc);
    setPending('reload'); setNotice(null); setError(null); clearInput();
    try {
      const latest = await port.getSettings();
      const current = await port.getAiStatus();
      if (!mounted.current || request !== serial.current || epoch !== getDatabaseEpoch(qc)) return;
      if (!aiStatusMatches(latest.ai, current)) throw new Error('changed');
      qc.setQueryData(AI_STATUS_KEY, current);
      setCredentialUnknown(false);
      onChange((value) => Object.assign(value, latest.ai));
      setNotice('AI 설정과 키 상태를 다시 불러왔습니다. 다른 편집 내용은 유지했습니다.');
    } catch { if (mounted.current && request === serial.current && epoch === getDatabaseEpoch(qc)) setError('AI 상태를 불러오지 못했습니다. 연결을 확인한 뒤 상태만 다시 불러오세요.'); }
    finally { if (request === serial.current) { operation.current = false; if (mounted.current) setPending(null); } }
  };

  return <fieldset className="ai-settings" aria-busy={pending !== null}>
    <legend>AI 분석 (선택)</legend>
    <p className="field-hint">규칙 분석은 AI 없이 동작합니다. 키 저장·설정 저장은 유료 AI 요청을 보내지 않습니다. AI를 켜면 수집·재분석에서 비용이 발생할 수 있습니다.</p>
    <div className="field-grid">
      <label className="field"><span className="field-label">AI 제공자</span><select value={ai.provider} disabled={disabled || !!pending} onChange={(e) => { clearInput(); const v = e.target.value as 'openai' | 'exaone'; onChange((x) => { x.provider = v; x.model = v === 'exaone' ? 'k-exaone_v2.5' : 'gpt-4.1-mini-2025-04-14'; x.enabled = false; }); }}><option value="openai">OpenAI</option><option value="exaone">EXAONE (LG)</option></select></label>
      <label className="field"><span className="field-label">AI 모델</span><select value={ai.model} disabled={disabled || !!pending} onChange={() => clearInput()}>{exaone ? <option value="k-exaone_v2.5">K-EXAONE 2.5</option> : <option value="gpt-4.1-mini-2025-04-14">GPT-4.1 mini (2025-04-14)</option>}</select></label>
    </div>
    <label className="field"><span className="field-label">{exaone ? 'EXAONE API 키' : 'OpenAI API 키'}</span><input ref={input} type="password" autoComplete="off" spellCheck={false} maxLength={2560} disabled={!credentialAllowed} onChange={() => setHasKeyInput(!!input.current?.value)} /></label>
    {status.data && !providerSaved ? <p className="field-hint">제공자를 저장한 뒤 키를 입력하세요.</p> : null}
    <div className="field-inline"><Button size="sm" busy={pending === 'save'} disabled={!credentialAllowed || !hasKeyInput} onClick={() => void credential('save')}>키 저장</Button>
      <Button size="sm" variant="ghost" busy={pending === 'delete'} disabled={!credentialAllowed || status.data?.keyConfigured !== true} onClick={() => void credential('delete')}>키 삭제</Button>
      <span className="field-hint">{credentialUnknown || status.isError || status.data?.keyConfigured === null ? '저장 상태 확인 불가' : status.isPending ? '저장 상태 확인 중…' : status.data?.keyConfigured ? 'Windows 보안 저장소에 저장됨' : '키 미설정'}</span></div>
    <p className="field-hint">키는 이 PC의 Windows 보안 저장소에 따로 보관하며 설정·기본 백업·내보내기에 포함하지 않습니다. 입력값은 저장 요청 후 바로 비웁니다. 키 교체·삭제는 AI를 끕니다.</p>
    {mode === 'demo' ? <p className="field-hint">데모에서는 실제 API 키를 입력하거나 저장하지 않습니다.</p> : status.data && !status.data.keySupported ? <p className="field-error">이 환경에서 Windows 보안 저장소를 사용할 수 없습니다.</p> : null}
    {!exaone && status.data ? <>
      <p className="field-hint">표준 요금 · USD / 100만 토큰: 입력 {status.data.price.inputUsdPerMillion} · 캐시 입력 {status.data.price.cachedInputUsdPerMillion} · 출력 {status.data.price.outputUsdPerMillion}. 확인일 {status.data.pricingCheckedAt}.{' '}
        {safeExternalUrl(status.data.price.sourceUrl) ? <a href={status.data.price.sourceUrl} onClick={(event) => { event.preventDefault(); void openExternal(status.data!.price.sourceUrl); }}>공식 모델·요금 근거</a> : null}</p>
    </> : null}
    {exaone ? <>
      <label className="field"><span className="field-label">하루 호출 상한</span><input inputMode="numeric" value={Number.isNaN(ai.dailyCallCap) ? '' : ai.dailyCallCap} disabled={disabled || !!pending} aria-invalid={!Number.isInteger(ai.dailyCallCap) || ai.dailyCallCap < 1 || ai.dailyCallCap > 5000} onChange={(e) => onChange((v) => { v.dailyCallCap = amount(e.target.value) ?? NaN; })} /></label>
      {status.data?.callsToday != null ? <p className="field-hint">오늘 호출 {formatCount(status.data.callsToday)}회</p> : null}
      <fieldset className="field"><legend className="field-label">EXAONE 기능</legend>
        {([['assistReview', '검토 보조 분석'], ['assistRecheck', '제외 공고 AI 재검토'], ['assistApplication', '맞춤 지원 문구 (자동 제출)'], ['assistChat', '채팅 답장 초안'], ['assistQuote', '견적서 초안']] as const).map(([k, label]) =>
          <label key={k} className="toggle"><input type="checkbox" checked={ai[k]} disabled={disabled || !!pending} onChange={(e) => onChange((v) => { v[k] = e.target.checked; })} /><span>{label}</span></label>)}
        <p className="field-hint">맞춤 지원 문구를 켜면 자동 지원이 공고마다 AI가 쓴 문구를 확인 없이 그대로 제출합니다. 문구를 만들지 못하면 그 공고는 보류합니다.</p>
      </fieldset>
    </> : <>
    <div className="field-grid">
      <label className="field"><span className="field-label">달러 환산 (1 USD 당 원)</span><input inputMode="numeric" value={Number.isNaN(ai.usdToKrwRate) ? '' : ai.usdToKrwRate ?? ''} disabled={disabled || !!pending} aria-invalid={ai.usdToKrwRate !== null && (!Number.isInteger(ai.usdToKrwRate) || ai.usdToKrwRate < 1 || ai.usdToKrwRate > 100_000)} onChange={(e) => onChange((v) => { v.usdToKrwRate = amount(e.target.value); v.pricingConfirmed = false; v.enabled = false; })} /></label>
      <label className="field"><span className="field-label">응답 토큰 상한 (256–4,096)</span><input inputMode="numeric" value={Number.isNaN(ai.maxOutputTokens) ? '' : ai.maxOutputTokens} disabled={disabled || !!pending} aria-invalid={!Number.isInteger(ai.maxOutputTokens) || ai.maxOutputTokens < 256 || ai.maxOutputTokens > 4096} onChange={(e) => onChange((v) => { v.maxOutputTokens = amount(e.target.value) ?? NaN; })} /></label>
      <label className="field"><span className="field-label">일 비용 상한 (원)</span><input inputMode="numeric" value={Number.isNaN(ai.dailyCostCap) ? '' : ai.dailyCostCap ?? ''} disabled={disabled || !!pending} aria-invalid={ai.dailyCostCap !== null && (!Number.isInteger(ai.dailyCostCap) || ai.dailyCostCap < 0 || ai.dailyCostCap > 100_000_000)} onChange={(e) => onChange((v) => { v.dailyCostCap = amount(e.target.value); if (v.dailyCostCap === null || !v.dailyCostCap) v.enabled = false; })} /></label>
      <label className="field"><span className="field-label">월 비용 상한 (원)</span><input inputMode="numeric" value={Number.isNaN(ai.monthlyCostCap) ? '' : ai.monthlyCostCap ?? ''} disabled={disabled || !!pending} aria-invalid={ai.monthlyCostCap !== null && (!Number.isInteger(ai.monthlyCostCap) || ai.monthlyCostCap < 0 || ai.monthlyCostCap > 100_000_000)} onChange={(e) => onChange((v) => { v.monthlyCostCap = amount(e.target.value); if (v.monthlyCostCap === null || !v.monthlyCostCap) v.enabled = false; })} /></label>
    </div>
    <p className="field-hint">상한 미설정·0원은 AI 요청을 중지합니다. 환산은 사용자가 확인한 값이며 세금·수수료를 포함한 청구액 보장은 아닙니다. 응답 유실 등 과금 미확인 금액도 상한에서 차감하며 유료 요청은 자동 재시도하지 않습니다.</p>
    <label className="toggle"><input type="checkbox" checked={ai.pricingConfirmed} disabled={disabled || !!pending || !status.data || status.isError || ai.usdToKrwRate === null || invalidAiSettings(ai)} onChange={(e) => onChange((v) => { v.pricingConfirmed = e.target.checked; if (!e.target.checked) v.enabled = false; })} /><span>위 모델 요금과 달러 환산 값을 확인했습니다</span></label>
    </>}
    {exaone
      ? <p className="field-hint">LG EXAONE으로 마스킹한 공고 제목·본문과, 켠 기능에 따라 채팅 내용·포트폴리오 주소·기준가 안내를 보냅니다. 전화번호·이메일·민감 표식을 감지해 가리지만 모든 이름·주소 제거를 보장하지 않습니다. 전송 전에 공고의 개인정보를 확인하세요. 입력은 EXAONE 플랫폼에서 학습 거부를 설정하기 전까지 학습에 쓰일 수 있습니다.</p>
      : <p className="field-hint">OpenAI로 마스킹한 공고 제목·본문, 규칙으로 분류한 업무 분류와 원문 첫 문장 기반 규칙 요약을 보냅니다. 전화번호·이메일·민감 표식을 감지해 가리지만 모든 이름·주소 제거를 보장하지 않습니다. 전송 전에 공고의 개인정보를 확인하세요. 내 조건·보유 기술·메모·영업 기록은 전송하지 않습니다.</p>}
    <label className="toggle"><input type="checkbox" checked={ai.externalTransferConsent} disabled={disabled || !!pending} onChange={(e) => onChange((v) => { v.externalTransferConsent = e.target.checked; if (!e.target.checked) v.enabled = false; })} /><span>{exaone ? '위 텍스트의 LG EXAONE 외부 전송에 동의합니다' : '위 텍스트의 OpenAI 외부 전송에 동의합니다'}</span></label>
    <label className="toggle"><input type="checkbox" checked={ai.enabled} disabled={disabled || !!pending || (!ai.enabled && !canEnable)} onChange={(e) => onChange((v) => { v.enabled = e.target.checked; })} /><span>AI 보조 분석 사용</span></label>
    {!canEnable && !ai.enabled ? <p className="field-hint">{exaone ? '켜려면 키 저장 상태·전송 동의·하루 호출 상한(1–5,000회)을 확인하세요.' : '켜려면 키 저장 상태·전송 동의·요금 확인·환산·일/월 상한을 확인하세요.'} 아래 저장 버튼을 눌러 적용합니다.</p> : <p className="field-hint">아래 저장 버튼을 눌러 적용합니다. AI 제안은 규칙 기반 리드 필터와 별도로 표시됩니다.</p>}
    {status.isPending ? <p className="muted-text" role="status">AI 상태 불러오는 중…</p> : status.isError ? <p className="field-error" role="alert">AI 상태를 불러오지 못했습니다. 키나 유료 요청을 다시 보내지 않습니다.</p> : status.data ? <p className="field-hint" role="status">현재 저장 상태: {statusLabel(status.data)}</p> : null}
    {!status.isPending && !synchronized ? <p className="field-error" role="alert">AI 설정 또는 서비스가 변경됐습니다. AI 상태를 다시 불러온 후 편집하세요.</p> : null}
    {exaone ? null : status.data?.ledger ? <><dl className="ai-budget"><BudgetRow title="오늘" value={status.data.ledger.day} cap={ai.dailyCostCap} /><BudgetRow title="이번 달" value={status.data.ledger.month} cap={ai.monthlyCostCap} /></dl><p className="field-hint">{mode === 'demo' ? '비용 기록은 합성 데모 값입니다.' : '비용 기록은 저장된 요청 집계입니다.'} 상한은 위 입력 값이며 저장 후 적용됩니다.</p></> : status.data ? <p className="field-error">비용 기록을 확인하지 못해 AI 요청이 중지됩니다.</p> : null}
    <Button size="sm" busy={pending === 'reload'} disabled={disabled || !!pending || status.isFetching} onClick={() => void reloadAi()}>AI 상태 다시 불러오기</Button>
    {notice ? <p className="field-hint" role="status">{notice}</p> : null}
    {error ? <p className="field-error" role="alert">{error}</p> : null}
  </fieldset>;
}
