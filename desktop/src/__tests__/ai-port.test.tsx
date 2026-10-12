import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AIStatus } from '@contracts/index';
import settings from '../../../contracts/fixtures/settings.json';
import lead from '../../../contracts/fixtures/relation-candidate.json';
import { LivePort } from '../data/live/livePort';
import { mapDetail, mapSettings, settingsPatchToContract } from '../data/live/mapper';
import { MockPort } from '../data/mock/mockPort';
import { AiInterpretationPanel } from '../features/detail/AiInterpretationPanel';

const status: AIStatus = { provider: 'openai', model: 'gpt-4.1-mini-2025-04-14', key_supported: true, key_configured: true, error_code: null,
  provider_available: true, enabled: false, ready: false, blocked_reason: 'disabled', settings_revision: 12, database_generation: 54321,
  pricing_checked_at: '2026-09-30', price_metadata: { input_usd_per_million: 0.4, cached_input_usd_per_million: 0.1, output_usd_per_million: 1.6, source_url: 'https://platform.openai.com/docs/models/gpt-4.1-mini', version: 'owned-test' },
  ledger: { day: { period: '2026-09-30', estimated_krw: 1, reserved_krw: 2, uncertain_krw: 3, attempts: 4 }, month: { period: '2026-09', estimated_krw: 10, reserved_krw: 20, uncertain_krw: 30, attempts: 40 } } };

describe('AI 계약 어댑터', () => {
  it('키 상태·요금·예산을 옮기고 키 쓰기는 POST 본문/CAS로 한 번 보낸다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => Response.json(status));
    const port = new LivePort({ baseUrl: 'http://127.0.0.1:41001', token: 'owned-session' }, fetcher);
    expect(await port.getAiStatus()).toMatchObject({ keyConfigured: true, settingsRevision: 12, databaseGeneration: 54321,
      price: { inputUsdPerMillion: 0.4, cachedInputUsdPerMillion: 0.1, outputUsdPerMillion: 1.6 }, ledger: { day: { estimatedKrw: 1, reservedKrw: 2, uncertainKrw: 3, attempts: 4 } } });
    await port.saveAiCredential({ key: 'synthetic-request-secret', expectedRevision: 11, expectedGeneration: 54321 });
    await port.deleteAiCredential({ expectedRevision: 12, expectedGeneration: 54321 });
    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['http://127.0.0.1:41001/v1/ai/status', 'GET'], ['http://127.0.0.1:41001/v1/ai/credentials', 'POST'], ['http://127.0.0.1:41001/v1/ai/credentials/delete', 'POST']]);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({ key: 'synthetic-request-secret', expected_revision: 11, expected_generation: 54321 });
    expect(JSON.parse(String(fetcher.mock.calls[2]?.[1]?.body))).toEqual({ expected_revision: 12, expected_generation: 54321 });
    for (const [url, init] of fetcher.mock.calls) { expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer owned-session'); expect(String(url)).not.toContain('synthetic-request-secret'); }
  });

  it('저장 응답 유실과 revision 충돌은 키 POST를 자동 반복하지 않는다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce(Response.json({ error: { code: 'ai_settings_changed', message: '다시 읽으세요', retryable: false } }, { status: 409 }));
    const port = new LivePort({ baseUrl: 'http://127.0.0.1:41001', token: 'owned-session' }, fetcher);
    await expect(port.saveAiCredential({ key: 'synthetic-request-secret', expectedRevision: 0, expectedGeneration: 0 })).rejects.toMatchObject({ code: 'connection_failed' });
    expect(fetcher).toHaveBeenCalledOnce();
    await expect(port.deleteAiCredential({ expectedRevision: 0, expectedGeneration: 0 })).rejects.toMatchObject({ code: 'ai_settings_changed', retryable: false });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('설정 patch는 환산·토큰·동의·0원/null·CAS를 그대로 옮기고 비밀은 담지 않는다', () => {
    const mapped = settingsPatchToContract({ ai: { enabled: false, externalTransferConsent: false, dailyCostCap: 0, monthlyCostCap: null, provider: 'openai', model: 'gpt-4.1-mini-2025-04-14', usdToKrwRate: 1400, maxOutputTokens: 1024, pricingConfirmed: true, expectedRevision: 12, expectedGeneration: 54321 } });
    expect(mapped.ai).toEqual({ enabled: false, external_transfer_consent: false, daily_cost_cap: 0, monthly_cost_cap: null, provider: 'openai', model: 'gpt-4.1-mini-2025-04-14', usd_to_krw_rate: 1400, max_output_tokens: 1024, pricing_confirmed: true, expected_revision: 12, expected_generation: 54321 });
    expect(Object.hasOwn(mapped.ai!, 'key')).toBe(false);
  });

  it('이전 설정/리드 fixture는 새 기능 기본값으로 읽으며 원본 규칙 정보를 유지한다', () => {
    const mapped = mapSettings(settings as unknown as Parameters<typeof mapSettings>[0]);
    expect(mapped.ai).toMatchObject({ provider: 'openai', model: 'gpt-4.1-mini-2025-04-14', usdToKrwRate: null, maxOutputTokens: 1024, pricingConfirmed: false, revision: 0, databaseGeneration: 0 });
    expect(mapDetail(lead as Parameters<typeof mapDetail>[0]).analysis.aiInterpretation).toBeNull();
  });

  it('데모 키 작업은 실제 저장/삭제 성공으로 표시하지 않는다', async () => {
    const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
    expect(await port.getAiStatus()).toMatchObject({ keySupported: false, keyConfigured: false, ready: false });
    await expect(port.saveAiCredential({ key: 'synthetic', expectedRevision: 0, expectedGeneration: 0 })).rejects.toMatchObject({ code: 'demo_credential_unsupported', retryable: false });
    await expect(port.deleteAiCredential({ expectedRevision: 0, expectedGeneration: 0 })).rejects.toMatchObject({ code: 'demo_credential_unsupported', retryable: false }); port.dispose();
  });
});

describe('AI 의도 제안과 원문 근거', () => {
  it('규칙 점수·보수와 별도 표시하며 HTML처럼 생긴 외부 텍스트도 텍스트로만 그린다', () => {
    const value = structuredClone(lead) as unknown as Parameters<typeof mapDetail>[0];
    value.analysis.ai_interpretation = { suggested_intent: 'employee_hiring', evidence_quotes: ['<img src="x" onerror="alert(1)"> 일급 지급'], uncertain: ['근무 장소 미확인'], questions: ['프로젝트별 지급인가요?'] };
    const before = mapDetail(lead as Parameters<typeof mapDetail>[0]); const after = mapDetail(value);
    expect(after.priority).toEqual(before.priority); expect(after.pay).toEqual(before.pay); expect(after.recommendation).toEqual(before.recommendation);
    render(<AiInterpretationPanel value={after.analysis.aiInterpretation} />);
    expect(screen.getByRole('region', { name: 'AI 의도 제안' })).toBeTruthy(); expect(screen.getByText('일반 채용')).toBeTruthy();
    expect(screen.getByText('<img src="x" onerror="alert(1)"> 일급 지급')).toBeTruthy(); expect(document.querySelector('img')).toBeNull();
    expect(screen.getByText('근무 장소 미확인')).toBeTruthy(); expect(screen.getByText('프로젝트별 지급인가요?')).toBeTruthy();
  });

  it('AI 제안이 없으면 섹션을 만들지 않고 인용이 없으면 근거 미확인을 표시한다', () => {
    const view = render(<AiInterpretationPanel value={null} />); expect(screen.queryByRole('region', { name: 'AI 의도 제안' })).toBeNull();
    view.rerender(<AiInterpretationPanel value={{ suggestedIntent: 'unknown', evidenceQuotes: [], uncertain: [], questions: [] }} />);
    expect(screen.getByText('유형 제안을 뒷받침할 원문 인용이 없습니다.')).toBeTruthy();
  });
});
