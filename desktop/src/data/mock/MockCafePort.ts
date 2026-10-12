import type { CafeEntry, CafeJob, CafePort, CafeSnapshot } from '../../domain/cafe';
import defaultTemplate from '../../../../contracts/fixtures/cafe-template.json';

/** Synthetic demonstration, no browser or external site requests. */
export class MockCafePort implements CafePort {
  private value = structuredClone(defaultTemplate);
  async template() { return structuredClone(this.value); }
  async updateTemplate(body: Parameters<CafePort['updateTemplate']>[0]) {
    if (this.value.title !== body.title || this.value.body !== body.body) this.value = { ...body, version: this.value.version + 1 };
    return structuredClone(this.value);
  }
  async attempts() { return []; }
  async post(): ReturnType<CafePort['post']> { throw new Error('내 글 전체 대조 경로가 없어 게시를 차단했습니다'); }
  private snapshot: CafeSnapshot = {
    consent: null, consent_version: 'cafe-risk-2026-10-12-v1',
    consent_text: '당근 이용약관은 자동화 도구 사용에 사전 서면 동의를 요구하며, 반복 홍보글은 계정 제재 사유가 될 수 있습니다. 확인은 당근의 허가를 대신하지 않습니다. 인증·차단 화면에서는 멈춥니다.',
    session: { state: 'logged_out', checked_at: null, reason: null },
    usage: { day: '2026-10-12', page_count: 0, daily_page_limit: 300, post_count: 0, daily_post_limit: 5 },
    capabilities: { collection: false, own_posts: false, full_comments: false, posting: false },
    settings: { auto_post: false, auto_approve: false, min_navigation_seconds: 8, daily_page_limit: 300, daily_post_limit: 5, min_post_minutes: 30, collect_interval_minutes: 180, comment_interval_minutes: 30, comment_monitor_days: 60, keywords: ['알바', '외주', '프리랜서', '개발', 'IT', '재택'] },
    active_job: null,
  };
  private entries: CafeEntry[] = [];
  private history: CafeJob[] = [];
  async status() { return structuredClone(this.snapshot); }
  async consent(version: string) {
    if (version !== this.snapshot.consent_version) throw new Error('최신 안내를 확인하세요');
    this.snapshot.consent = { version, accepted_at: new Date().toISOString() };
  }
  async settings(patch: Parameters<CafePort['settings']>[0]) {
    if (patch.auto_post) throw new Error('내 글 대조가 확보되지 않아 게시할 수 없습니다');
    const values = Object.fromEntries(Object.entries(patch).filter(([, value]) => value !== null));
    Object.assign(this.snapshot.settings, values);
    return structuredClone(this.snapshot.settings);
  }
  async list() { return structuredClone(this.entries); }
  async add(url: string) {
    if (!/^https:\/\/cafe\.daangn\.com\/[a-zA-Z0-9_-]+\/?$/.test(url)) throw new Error('당근 카페 홈 주소를 입력하세요');
    const existing = this.entries.find((row) => row.url === url);
    if (existing) return structuredClone(existing);
    const row: CafeEntry = { id: `demo-${this.entries.length}`, url, name: '직접 추가한 카페 (합성)', region: null, category: null, member_count: null, post_count: null, joined: null, status: 'candidate', status_reason: '규칙 조사 전', rule_evidence: null, first_seen_at: new Date().toISOString(), last_checked_at: null, approved_at: null, boards: [] };
    this.entries.push(row);
    return structuredClone(row);
  }
  async state(id: string, status: CafeEntry['status']) {
    const row = this.entries.find((r) => r.id === id);
    if (!row) throw new Error('카페를 찾을 수 없습니다');
    if (status === 'approved' && row.status !== 'eligible') throw new Error('가입과 게시 허용 근거를 먼저 확인하세요');
    row.status = status;
    return structuredClone(row);
  }
  async board(): ReturnType<CafePort['board']> { throw new Error('합성 시나리오에서 승인 가능한 게시판이 없습니다'); }
  async jobs() { return structuredClone(this.history); }
  async action(kind: CafeJob['kind']) {
    if (kind === 'collect') throw new Error('내 글 제외·작성 시각의 지원 경로가 필요합니다');
    if (!this.snapshot.consent) throw new Error('위험 안내를 먼저 확인하세요');
    this.snapshot.session = { state: 'logged_in', checked_at: new Date().toISOString(), reason: null };
    this.snapshot.usage.page_count += kind === 'discover' ? 4 : 1;
    if (kind === 'discover' && !this.entries.length) {
      await this.add('https://cafe.daangn.com/synthetic-freelance');
      this.entries[0]!.name = '재택 개발 의뢰 (합성)';
      this.entries[0]!.status_reason = '게시판 이름은 후보 근거입니다. 홍보 허용 규칙은 확인되지 않았습니다.';
    }
    const job: CafeJob = { id: `demo-job-${this.history.length}`, kind, state: 'succeeded', created_at: new Date().toISOString(), started_at: new Date().toISOString(), finished_at: new Date().toISOString(), error_code: null, error_message: null, result: { synthetic: true } };
    this.history.unshift(job);
    return structuredClone(job);
  }
}
