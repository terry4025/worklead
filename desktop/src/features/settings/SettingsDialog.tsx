import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import type { SettingsView } from '../../domain/model';
import { usePort } from '../../app/PortContext';
import { applyDisplayPrefs, type FontScale, type ThemePref } from '../../app/prefs';
import { qk, useBootstrap, useSettings } from '../../app/queries';
import { Button } from '../../ui/Button';
import { Dialog } from '../../ui/Dialog';
import { ErrorState, errorMessage } from '../../ui/States';

function numOrNull(v: string): number | null {
  const t = v.replace(/[,\s원]/g, '');
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? Math.round(n) : NaN;
}

export function SettingsDialog({
  open,
  onClose,
  fontScale,
  setFontScale,
  theme,
  setTheme,
}: {
  open: boolean;
  onClose: () => void;
  fontScale: FontScale;
  setFontScale: (v: FontScale) => void;
  theme: ThemePref;
  setTheme: (v: ThemePref) => void;
}) {
  const q = useSettings();
  const boot = useBootstrap();
  const port = usePort();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<SettingsView | null>(null);
  const [kwText, setKwText] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (open && q.data) {
      setDraft(structuredClone(q.data));
      setKwText(Object.fromEntries(q.data.queryGroups.groups.map((g) => [g.id, g.keywords.join(', ')])));
      setErr(null);
      setSaved(false);
    }
  }, [open, q.data]);

  const save = useMutation({
    mutationFn: (d: SettingsView) =>
      port.updateSettings({
        profile: d.profile,
        recheck: d.recheck,
        notifications: d.notifications,
        retention: d.retention,
        ai: { enabled: d.ai.enabled, externalTransferConsent: d.ai.externalTransferConsent, monthlyCostCap: d.ai.monthlyCostCap, dailyCostCap: d.ai.dailyCostCap },
        queryGroups: d.queryGroups.groups.map((g) => ({
          ...g,
          keywords: (kwText[g.id] ?? '')
            .split(/[,\n]/)
            .map((k) => k.trim())
            .filter(Boolean),
        })),
      }),
    onSuccess: (s) => {
      qc.setQueryData(qk.settings, s);
      void qc.invalidateQueries({ queryKey: qk.bootstrap });
      setSaved(true);
      setErr(null);
    },
    onError: (e) => setErr(errorMessage(e)),
  });

  const d = draft;
  const set = (fn: (x: SettingsView) => void) =>
    setDraft((prev) => {
      if (!prev) return prev;
      const next = structuredClone(prev);
      fn(next);
      setSaved(false);
      return next;
    });
  const invalidMoney = d ? [d.profile.minContract, d.profile.targetHourly].some((v) => Number.isNaN(v)) : false;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="설정"
      width="min(720px, 96vw)"
      footer={
        d ? (
          <>
            {err ? <span className="field-error" role="alert">{err}</span> : saved ? <span className="save-state save-saved">저장됨</span> : null}
            <Button variant="ghost" onClick={onClose}>
              닫기
            </Button>
            <Button variant="primary" busy={save.isPending} disabled={invalidMoney} onClick={() => save.mutate(d)}>
              저장
            </Button>
          </>
        ) : null
      }
    >
      {q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
      {!d ? (
        q.isPending ? <p className="muted-text">불러오는 중…</p> : null
      ) : (
        <div className="settings">
          <fieldset>
            <legend>내 조건</legend>
            <p className="field-hint">추천·점수·수익성 계산에 쓰입니다. 비워 둔 값은 추정하지 않습니다.</p>
            <div className="field">
              <span className="field-label">제공 서비스</span>
              <div className="chips">
                {(boot.data?.categories ?? []).filter((c) => c.id !== 'other' && c.id !== 'data').map((c) => (
                  <label key={c.id} className="chip">
                    <input
                      type="checkbox"
                      checked={d.profile.services.includes(c.id)}
                      onChange={(e) =>
                        set((x) => {
                          x.profile.services = e.target.checked ? [...x.profile.services, c.id] : x.profile.services.filter((s) => s !== c.id);
                        })
                      }
                    />
                    {c.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="field-grid">
              <label className="field">
                <span className="field-label">최소 계약 금액 (원)</span>
                <input inputMode="numeric" value={d.profile.minContract ?? ''} onChange={(e) => set((x) => void (x.profile.minContract = numOrNull(e.target.value)))} aria-invalid={Number.isNaN(d.profile.minContract)} />
              </label>
              <label className="field">
                <span className="field-label">목표 시간당 가치 (원)</span>
                <input inputMode="numeric" value={d.profile.targetHourly ?? ''} onChange={(e) => set((x) => void (x.profile.targetHourly = numOrNull(e.target.value)))} aria-invalid={Number.isNaN(d.profile.targetHourly)} />
              </label>
              <label className="field">
                <span className="field-label">주당 가용 시간</span>
                <input inputMode="numeric" value={d.profile.weeklyHours ?? ''} onChange={(e) => set((x) => void (x.profile.weeklyHours = numOrNull(e.target.value)))} />
              </label>
              <label className="field">
                <span className="field-label">방문 가능 여부</span>
                <select value={d.profile.onsite} onChange={(e) => set((x) => void (x.profile.onsite = e.target.value as SettingsView['profile']['onsite']))}>
                  <option value="no">방문 불가 (완전 원격만)</option>
                  <option value="first_meeting">첫 미팅 정도는 가능</option>
                  <option value="yes">방문 가능</option>
                </select>
              </label>
            </div>
            <label className="field">
              <span className="field-label">보유 기술</span>
              <input value={d.profile.skills} placeholder="예: React, Python, Excel VBA" onChange={(e) => set((x) => void (x.profile.skills = e.target.value))} />
            </label>
            <label className="field">
              <span className="field-label">제외 업무 (쉼표로 구분)</span>
              <input value={d.profile.excludedWork} placeholder="예: 쇼핑몰, 게임" onChange={(e) => set((x) => void (x.profile.excludedWork = e.target.value))} />
            </label>
            <label className="toggle">
              <input type="checkbox" checked={d.profile.allowShortTermEmployment} onChange={(e) => set((x) => void (x.profile.allowShortTermEmployment = e.target.checked))} />
              <span>개발 관련 재택 단기 고용도 확인 필요 후보로 남기기 (외주와 섞지 않음)</span>
            </label>
          </fieldset>

          <fieldset>
            <legend>모집 확인·검색어</legend>
            <label className="field field-inline">
              <span className="field-label">모집 상태 재확인 주기</span>
              <select value={d.recheck.ttlHours} onChange={(e) => set((x) => void (x.recheck.ttlHours = Number(e.target.value)))}>
                {[6, 12, 24, 48, 72].map((h) => (
                  <option key={h} value={h}>
                    {h}시간 지나면 재확인 필요
                  </option>
                ))}
              </select>
            </label>
            <p className="field-hint">검색어 묶음 (버전 {d.queryGroups.version}) — 켤수록 전국 한 바퀴가 길어집니다.</p>
            {d.queryGroups.groups.map((g, i) => (
              <div key={g.id} className="qgroup">
                <label className="toggle">
                  <input type="checkbox" checked={g.enabled} onChange={(e) => set((x) => void ((x.queryGroups.groups[i] as (typeof x.queryGroups.groups)[number]).enabled = e.target.checked))} />
                  <span>{g.label}</span>
                </label>
                <input aria-label={`${g.label} 검색어`} value={kwText[g.id] ?? ''} onChange={(e) => setKwText((k) => ({ ...k, [g.id]: e.target.value }))} />
              </div>
            ))}
          </fieldset>

          <fieldset>
            <legend>알림</legend>
            <label className="toggle">
              <input type="checkbox" checked={d.notifications.newRecommended} onChange={(e) => set((x) => void (x.notifications.newRecommended = e.target.checked))} />
              <span>새 추천 리드 (같은 공고 재수집은 알리지 않음)</span>
            </label>
            <label className="toggle">
              <input type="checkbox" checked={d.notifications.meaningfulChange} onChange={(e) => set((x) => void (x.notifications.meaningfulChange = e.target.checked))} />
              <span>관심·진행 중 리드의 의미 있는 조건 변경</span>
            </label>
            <label className="toggle">
              <input type="checkbox" checked={d.notifications.sourceIssue} onChange={(e) => set((x) => void (x.notifications.sourceIssue = e.target.checked))} />
              <span>소스 장애·차단</span>
            </label>
            <div className="field-inline">
              <label className="toggle">
                <input type="checkbox" checked={d.notifications.quietHours.enabled} onChange={(e) => set((x) => void (x.notifications.quietHours.enabled = e.target.checked))} />
                <span>조용한 시간</span>
              </label>
              <input type="time" aria-label="조용한 시간 시작" value={d.notifications.quietHours.start} onChange={(e) => set((x) => void (x.notifications.quietHours.start = e.target.value))} />
              <span>~</span>
              <input type="time" aria-label="조용한 시간 끝" value={d.notifications.quietHours.end} onChange={(e) => set((x) => void (x.notifications.quietHours.end = e.target.value))} />
            </div>
          </fieldset>

          <fieldset>
            <legend>AI 분석 (선택)</legend>
            <p className="field-hint">규칙 분석은 AI 없이 항상 동작합니다. AI 를 켜면 개인정보를 가린 공고 본문이 외부 제공자로 전송됩니다. API 키는 Windows 자격 증명 저장소에 보관하며 화면·로그·내보내기에 표시하지 않습니다.</p>
            <label className="toggle">
              <input type="checkbox" checked={d.ai.externalTransferConsent} onChange={(e) => set((x) => {
                x.ai.externalTransferConsent = e.target.checked;
                if (!e.target.checked) x.ai.enabled = false;
              })} />
              <span>외부 전송 고지를 확인했습니다</span>
            </label>
            <label className="toggle">
              <input type="checkbox" checked={d.ai.enabled} disabled={!d.ai.externalTransferConsent} onChange={(e) => set((x) => void (x.ai.enabled = e.target.checked))} />
              <span>AI 보조 분석 사용 {d.ai.providerAvailable ? '' : '— 제공자 미설정 (현재 버전은 규칙 분석만 실행)'}</span>
            </label>
            <label className="field field-inline">
              <span className="field-label">월 비용 상한 (원)</span>
              <input inputMode="numeric" value={d.ai.monthlyCostCap ?? ''} onChange={(e) => set((x) => void (x.ai.monthlyCostCap = numOrNull(e.target.value)))} />
            </label>
          </fieldset>

          <fieldset>
            <legend>데이터 보존</legend>
            <label className="field field-inline">
              <span className="field-label">수집 원문 보존</span>
              <select value={d.retention.rawDays} onChange={(e) => set((x) => void (x.retention.rawDays = Number(e.target.value)))}>
                {[7, 14, 30, 60, 90].map((n) => (
                  <option key={n} value={n}>
                    {n}일 (원천 정책이 더 짧으면 그에 따름)
                  </option>
                ))}
              </select>
            </label>
            <p className="field-hint">진행 중·관심 리드의 원문과 내 메모·영업 기록은 따로 보존됩니다.</p>
          </fieldset>

          <fieldset>
            <legend>화면 (이 PC 에만 저장)</legend>
            <div className="field-grid">
              <label className="field">
                <span className="field-label">글자 크기</span>
                <select
                  value={fontScale}
                  onChange={(e) => {
                    const v = e.target.value as FontScale;
                    setFontScale(v);
                    applyDisplayPrefs(v, theme);
                  }}
                >
                  <option value="normal">보통</option>
                  <option value="large">크게</option>
                  <option value="xlarge">아주 크게</option>
                </select>
              </label>
              <label className="field">
                <span className="field-label">테마</span>
                <select
                  value={theme}
                  onChange={(e) => {
                    const v = e.target.value as ThemePref;
                    setTheme(v);
                    applyDisplayPrefs(fontScale, v);
                  }}
                >
                  <option value="system">시스템 설정 따르기</option>
                  <option value="light">밝게</option>
                  <option value="dark">어둡게</option>
                </select>
              </label>
            </div>
          </fieldset>
        </div>
      )}
    </Dialog>
  );
}
