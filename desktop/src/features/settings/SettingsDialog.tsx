import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { AiStatus, RestoreResult, SettingsView } from '../../domain/model';
import { usePort } from '../../app/PortContext';
import { applyDisplayPrefs, type FontScale, type ThemePref } from '../../app/prefs';
import { qk, useBootstrap, useSettings } from '../../app/queries';
import { Button } from '../../ui/Button';
import { Dialog } from '../../ui/Dialog';
import { ErrorState, errorMessage } from '../../ui/States';
import { BackupPanel } from './BackupPanel';
import { DesktopSettingsPanel } from './DesktopSettingsPanel';
import { AI_EDITABLE, AI_STATUS_KEY, AiSettingsPanel, aiSettingsChanged, aiStatusMatches, invalidAiSettings } from './AiSettingsPanel';
import { formatCount } from '../../domain/format';
import { getDatabaseEpoch, resetDatabaseQueries } from '../../app/databaseCache';

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
  const lastSaved = useRef<{ data: SettingsView; epoch: number } | null>(null);
  const wasOpen = useRef(false);
  const [backupBusy, setBackupBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [restoreResult, setRestoreResult] = useState<{ result: RestoreResult; refreshFailed: boolean } | null>(null);
  const restored = useCallback((result: RestoreResult, refreshFailed: boolean) => {
    lastSaved.current = null;
    setRestoreResult({ result, refreshFailed });
    setErr(null);
    setSaved(false);
  }, []);
  const refreshRestored = useMutation({
    mutationFn: () => resetDatabaseQueries(qc),
    onSuccess: () => setRestoreResult((prev) => prev ? { ...prev, refreshFailed: false } : null),
  });

  useEffect(() => {
    const opening = open && !wasOpen.current;
    wasOpen.current = open;
    if (!open) {
      lastSaved.current = null;
      setSaved(false);
      return;
    }
    if (open && !q.data) {
      lastSaved.current = null;
      setDraft(null);
      setKwText({});
      setSaved(false);
    } else if (open && q.data) {
      setDraft(structuredClone(q.data));
      setKwText(Object.fromEntries(q.data.queryGroups.groups.map((g) => [g.id, g.keywords.join(', ')])));
      setErr(null);
      // Keep the success state when our own saved response reaches the form.
      // Reopening or a restored/external settings value starts a fresh form.
      if (opening || lastSaved.current?.data !== q.data || lastSaved.current?.epoch !== getDatabaseEpoch(qc)) {
        lastSaved.current = null;
        setSaved(false);
      }
    }
  }, [open, q.data, q.dataUpdatedAt, qc]);

  const save = useMutation({
    retry: false,
    onMutate: () => getDatabaseEpoch(qc),
    mutationFn: (d: SettingsView) =>
      port.updateSettings({
        profile: d.profile,
        recheck: d.recheck,
        notifications: d.notifications,
        retention: d.retention,
        ...(q.data && aiSettingsChanged(d.ai, q.data.ai) ? { ai: {
          ...Object.fromEntries(AI_EDITABLE.map((key) => [key, d.ai[key]])),
          expectedRevision: d.ai.revision, expectedGeneration: d.ai.databaseGeneration,
        } } : {}),
        queryGroups: d.queryGroups.groups.map((g) => ({
          ...g,
          keywords: (kwText[g.id] ?? '')
            .split(/[,\n]/)
            .map((k) => k.trim())
            .filter(Boolean),
        })),
      }),
    onSuccess: (s, _variables, epoch) => {
      if (epoch !== getDatabaseEpoch(qc)) return;
      const data = qc.setQueryData<SettingsView>(qk.settings, s);
      lastSaved.current = data ? { data, epoch } : null;
      void qc.invalidateQueries({ queryKey: qk.bootstrap });
      void qc.invalidateQueries({ queryKey: AI_STATUS_KEY });
      setSaved(true);
      setErr(null);
    },
    onError: (e, _variables, epoch) => { if (epoch === getDatabaseEpoch(qc)) { setErr(errorMessage(e)); void qc.invalidateQueries({ queryKey: AI_STATUS_KEY }); } },
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
  const aiChanged = !!(d && q.data && aiSettingsChanged(d.ai, q.data.ai));
  const aiInvalid = !!(d && aiChanged && (invalidAiSettings(d.ai) || !aiStatusMatches(d.ai, aiStatus)));

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
            <Button variant="primary" busy={save.isPending} disabled={invalidMoney || aiInvalid || backupBusy || aiBusy} onClick={() => save.mutate(d)}>
              저장
            </Button>
          </>
        ) : null
      }
    >
      {restoreResult ? <p className="field-hint" role="status">복원했습니다. 리드 {formatCount(restoreResult.result.leadCount)}건 · 복원 전 안전 백업: {restoreResult.result.safetyBackup.fileName}. 자동 수집과 AI 분석은 꺼졌습니다. 수집 화면에서 자동 수집을 직접 다시 켜고, AI 설정에서 전송·요금 확인 후 AI를 다시 켜세요.</p> : null}
      {restoreResult?.refreshFailed ? <><p className="field-error" role="alert">복원은 완료했지만 화면 데이터를 다시 불러오지 못했습니다.</p><Button size="sm" busy={refreshRestored.isPending} onClick={() => refreshRestored.mutate()}>화면 데이터 다시 불러오기</Button></> : null}
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
            <p className="muted">리드 대상: 건당·프로젝트 지급이 확인되는 프리랜서 제작 의뢰. 시급·일급·주급·월급과 인력 채용, 지급 방식 미확인 글은 제외합니다.</p>
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
            <p className="field-hint">검색어 묶음 (버전 {d.queryGroups.version}) — 검색어를 사용하는 소스에만 적용됩니다. 아이보스 공개 목록 수집은 검색어로 제목을 제한하지 않습니다.</p>
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

          <DesktopSettingsPanel />

          <AiSettingsPanel ai={d.ai} onChange={(update) => set((value) => update(value.ai))} onStatusChange={setAiStatus} onBusyChange={setAiBusy} disabled={save.isPending || backupBusy || refreshRestored.isPending} />
          {d && aiChanged && invalidAiSettings(d.ai) ? <p className="field-error" role="alert">AI 상한은 0–100,000,000원, 달러 환산은 1–100,000원, 응답 토큰은 256–4,096의 정수로 입력하세요.</p> : null}

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

          <BackupPanel onRestored={restored} onBusyChange={setBackupBusy} disabled={save.isPending || refreshRestored.isPending || aiBusy} />

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
