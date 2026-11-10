import { connectionFrom, isJobPage } from './protocol.js';
import { connectionError, ping } from './connection-errors.js';
import { AUTO_ALARM, JOBS_PERMISSION, jobId, validJob, hashShape } from './auto-protocol.js';
import { createReconnect, isPairCode, PAIR_ALARM } from './reconnect.js';

let busy = false;
let nextPoll = 0;
let reconnectNeeded = false;
let alarmWork = Promise.resolve();
const changeAlarm = operation => { const result = alarmWork.then(operation); alarmWork = result.catch(() => {}); return result; };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function bounded(promise, milliseconds = 8000) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('browser_timeout')), milliseconds); })])
    .finally(() => clearTimeout(timer));
}
const setStatus = message => chrome.storage.session.set({ autoStatus: { message, observedAt: Date.now() } });
const queueMessages = {
  stopped: 'Worklead에서 자동 지원이 중지되어 있습니다.',
  paused: '자동 지원이 일시 중지되었습니다. Worklead에서 확인이 필요한 이유를 확인하세요.',
  connection_wait: '자동 지원 전용 탭 연결을 기다립니다.',
  connection_stale: 'Worklead와의 작업 응답이 늦습니다. 연결 상태를 확인하세요.',
  daily_limit: '오늘 제출 상한에 도달했습니다. 다음 허용 시간을 기다립니다.',
  processing: '이전 공고의 양식 또는 제출 결과를 확인 중입니다.',
  source_permission_unverified: '당근 수집 허용 상태를 확인하기 전까지 지원을 기다립니다.',
  source_refresh: '저장 공고의 현재 원문·보수·모집 상태를 재확인하고 있습니다.',
  retry_wait: '제출 전 일시 실패의 재시도 시간을 기다립니다. Worklead에서 예정 시각을 확인하세요.',
  no_candidates: '연결 중 · 현재 조건에 맞는 미지원 공고가 없습니다.',
  ready: '조건에 맞는 공고의 작업 배정을 기다립니다.',
};
function queueMessage(next) {
  const reason = next?.reason_code || next?.queue_reason;
  const message = queueMessages[reason];
  return typeof message === 'string' ? message : reason ? '연결 중 · Worklead에서 지원 대기 사유를 확인하세요.' : '연결 중 · 기준 점수 이상의 새 지원 대상을 기다립니다.';
}
async function config() { return (await bounded(chrome.storage.session.get('autoBridge'))).autoBridge; }
async function alive(c) { const current = await config(); return current?.token === c.token && current.url === c.url && current.tabId === c.tabId; }
async function ensureAlarm(c, active = () => true) {
  await changeAlarm(async () => {
    if (!c || !active() || !await alive(c)) throw new Error('cancelled');
    if (!await chrome.alarms.get(AUTO_ALARM)) {
      if (!active() || !await alive(c)) throw new Error('cancelled');
      await chrome.alarms.create(AUTO_ALARM, { periodInMinutes: 0.5 });
    }
    if (!active() || !await alive(c)) { await chrome.alarms.clear(AUTO_ALARM); throw new Error('cancelled'); }
  });
}
async function post(c, path, body) {
  if (!['/next', '/review', '/permit', '/result', '/reconcile', '/pause'].includes(path) || !await alive(c)) throw new Error('stopped');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch(c.url + path, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + c.token }, body: JSON.stringify(body), signal: controller.signal, credentials: 'omit', redirect: 'error', cache: 'no-store' });
    if (!response.ok) { if (response.status === 401 || response.status === 403) reconnectNeeded = true; throw new Error('not_accepted'); }
    return await response.json();
  } catch (error) { if (error.message !== 'not_accepted') reconnectNeeded = true; throw error; }
  finally { clearTimeout(timer); }
}
async function ui(c, operation, expected = null) {
  return bounded(readUi(c, operation, expected));
}
async function readUi(c, operation, expected) {
  if (!await alive(c)) throw new Error('stopped');
  return readTab(c.tabId, operation, expected);
}
async function readTab(tabId, operation = 'inspect', expected = null) {
  const tab = await chrome.tabs.get(tabId);
  if (!isJobPage(tab.url)) throw new Error('wrong_tab');
  await chrome.scripting.executeScript({ target: { tabId, allFrames: false }, files: ['auto-content.js'], world: 'ISOLATED' });
  const result = await chrome.scripting.executeScript({ target: { tabId, allFrames: false }, world: 'ISOLATED', func: (op, data) => globalThis.__workleadAutoUi(op, data), args: [operation, expected] });
  if (result.length !== 1 || !result[0].result) throw new Error('unrecognized');
  return result[0].result;
}
async function waitUi(c, expected, states, timeout = 10000) {
  const until = Date.now() + timeout;
  let result;
  do {
    result = await ui(c, 'inspect', expected);
    if (states.includes(result.state)) return result;
    if (result.reason_code === 'login_required' || result.reason_code === 'account_changed') return result;
    if (result.state === 'held' && ['questions_required', 'profile_changed', 'condition_conflict', 'closed'].includes(result.reason_code)) return result;
    await sleep(400);
  } while (Date.now() < until);
  return result;
}
async function savePending(c, pending) {
  if (!await alive(c)) throw new Error('stopped');
  await chrome.storage.session.set({ autoPending: pending });
  if (!await alive(c)) { await chrome.storage.session.remove('autoPending'); throw new Error('stopped'); }
}
async function clear() {
  const previous = await config();
  await chrome.storage.session.remove(['autoBridge', 'autoPending']);
  await changeAlarm(() => chrome.alarms.clear(AUTO_ALARM));
  if (previous) {
    try {
      const tab = await chrome.tabs.get(previous.tabId);
      if (isJobPage(tab.url)) await chrome.scripting.executeScript({ target: { tabId: previous.tabId, allFrames: false }, world: 'ISOLATED', func: () => {
        if (globalThis.__workleadAutoTicker) clearInterval(globalThis.__workleadAutoTicker);
        delete globalThis.__workleadAutoTicker;
      } });
    } catch {}
  }
  await setStatus('자동 지원 탭 연결을 중지했습니다. Worklead에서도 감시를 중지할 수 있습니다.');
}
async function configure(message, sender) {
  if (sender.tab || sender.url !== chrome.runtime.getURL('popup.html') || message.confirm !== true) throw new Error('popup_required');
  if (busy) throw new Error('busy');
  const value = connectionFrom(message.code, 'auto_apply');
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !Number.isInteger(tab.id) || !isJobPage(tab.url)) throw new Error('jobs_tab_required');
  if (!await chrome.permissions.contains({ origins: ['http://127.0.0.1/*', JOBS_PERMISSION] })) throw new Error('permission_required');
  // Do not navigate the unread-monitoring tab. Its purpose remains read-only.
  if ((await chrome.storage.session.get('bridge')).bridge?.tabId === tab.id) throw new Error('separate_tab_required');
  if (isPairCode(value)) return reconnect.initial(value, tab.id);
  await ping(value, 'auto_apply');
  if (chrome.storage.local) await reconnect.forget();
  else await clear();
  await chrome.storage.session.set({ autoBridge: { ...value, tabId: tab.id, ready: false } });
  try {
    const c = await config();
    const result = await ui(c, 'inspect');
    if (result.state !== 'account' || !hashShape(result.account_hash)) throw new Error('login_required');
    await chrome.storage.session.set({ autoBridge: { ...c, ready: true, account_hash: result.account_hash } });
  } catch (error) { await clear(); throw error; }
  // Pairing tests the purpose-specific endpoint before issuing any click.
  await setStatus('자동 지원 전용 탭을 연결했습니다. Worklead의 50점 이상 후보를 기다립니다.');
  await ensureAlarm(await config());
  nextPoll = 0;
  void tick();
  return { ok: true };
}

const reconnect = createReconnect({
  validateConnection: c => ping(c, 'auto_apply'),
  readAccount: tabId => bounded(readTab(tabId)),
  clearSession: clear,
  status: setStatus,
  async resumeConnected(connection, tabId, account_hash, current) {
    if (!current() || !await alive({ ...connection, tabId })) return;
    // Restore scheduling without replacing the session or its read-only
    // pending receipt. Reconciliation must retain its at-most-once boundary.
    await ensureAlarm({ ...connection, tabId }, current);
    if (current()) void tick();
  },
  async connected(connection, tabId, account_hash, current) {
    if (!current()) throw new Error('cancelled');
    // Rotating a connection never resumes an old permit or either application click.
    const previous = await config();
    if (previous?.token !== connection.token || previous?.url !== connection.url) await chrome.storage.session.remove('autoPending');
    if (!current()) throw new Error('cancelled');
    await chrome.storage.session.set({ autoBridge: { ...connection, tabId, account_hash, ready: true } });
    if (!current()) {
      if ((await config())?.token === connection.token) await chrome.storage.session.remove('autoBridge');
      throw new Error('cancelled');
    }
    await ensureAlarm({ ...connection, tabId }, current);
    // A 401 immediately after resume must not turn into an unpaced handshake loop.
    if (!previous) nextPoll = 0;
    void tick();
  },
});

function pendingValid(p, c) {
  return p && validJob(p.job) && p.tab_id === c.tabId && p.connection_url === c.url &&
    p.identity?.attempt_id === p.job.attempt_id && p.identity.lease_token === p.job.lease_token &&
    hashShape(p.identity.account_hash) && hashShape(p.identity.profile_hash) &&
    ['opening', 'permitting', 'clicked'].includes(p.phase) && Number.isInteger(p.checks) && p.checks >= 0 &&
    Number.isFinite(p.created_at) && Number.isFinite(p.next_check_at) &&
    (p.phase === 'opening' || /^[A-Za-z0-9_-]{32,128}$/.test(p.receipt_token));
}

async function pauseIdentity(c, reason) {
  try { await post(c, '/pause', { reason }); } catch {}
  if (chrome.storage.local) await reconnect.pause('당근 로그인 상태가 바뀌어 자동 지원을 중지했습니다. 처음 연결한 계정으로 로그인하고 Worklead에서 자동 지원을 다시 시작한 뒤 저장한 연결 재개를 누르세요.');
  else { await clear(); await setStatus('당근 로그인 상태가 바뀌어 연결을 해제했습니다. 계정을 확인한 뒤 새 코드로 연결하세요.'); }
}

async function recover(c, p) {
  if (!pendingValid(p, c) || Date.now() - p.created_at > 15 * 60 * 1000 || p.checks >= 3) {
    await chrome.storage.session.remove('autoPending');
    await setStatus('완료 표시를 자동 대조하지 못했습니다. 결과 미확인을 유지하며 재제출하지 않습니다.');
    return;
  }
  if (p.phase === 'opening') {
    // A permit is requested only AFTER the durable phase changes to permitting.
    // Report interruption to Worklead; only its journal/permit checks may retry.
    try { await post(c, '/review', { ...p.identity, outcome: 'held', reason_code: 'connection_ended' }); } catch {}
    await chrome.storage.session.remove('autoPending');
    await setStatus('중단된 양식 확인을 Worklead에 보고했습니다. 제출 전 기록과 재시도 조건을 다시 확인합니다.');
    return;
  }
  if (Date.now() < p.next_check_at) return;
  // Reserve each read before awaiting, so worker suspension cannot loop forever.
  await savePending(c, { ...p, checks: p.checks + 1, next_check_at: Date.now() + [15000, 30000, 0][p.checks] });
  try {
    const account = await ui(c, 'inspect');
    if (account.reason_code === 'login_required' || (account.account_hash && account.account_hash !== p.identity.account_hash)) {
      await pauseIdentity(c, account.reason_code === 'login_required' ? 'login_required' : 'account_changed'); return;
    }
    if (account.account_hash !== p.identity.account_hash) throw new Error('identity_unverified');
    const expected = { job_id: jobId(p.job.url), title: p.job.title, account_hash: p.identity.account_hash };
    const tab = await chrome.tabs.get(c.tabId);
    if (jobId(tab.url) !== expected.job_id) {
      if (!await alive(c)) return;
      await bounded(chrome.tabs.update(c.tabId, { url: p.job.url }));
    }
    // Recovery has no open/submit/permit path. Only existing official evidence.
    const view = await waitUi(c, expected, ['already_applied'], 3000);
    if (view.reason_code === 'login_required' || view.reason_code === 'account_changed') {
      await pauseIdentity(c, view.reason_code); return;
    }
    if (view.state === 'already_applied' && view.account_hash === expected.account_hash) {
      const result = await post(c, '/reconcile', { attempt_id: p.identity.attempt_id, receipt_token: p.receipt_token,
        account_hash: p.identity.account_hash, profile_hash: p.identity.profile_hash, evidence: 'cancel_button' });
      if (result.status === 'submitted') {
        await chrome.storage.session.remove('autoPending');
        await setStatus('공식 지원 취소 표시를 다시 대조해 제출 완료 기록을 복구했습니다. 재제출하지 않았습니다.');
        return;
      }
    }
  } catch {}
  await setStatus('연결 복구 후 제출 완료 표시를 자동 대조 중입니다. 지원 버튼은 다시 누르지 않습니다.');
}

async function tick() {
  if (busy || Date.now() < nextPoll) return;
  busy = true;
  nextPoll = Date.now() + 5000;
  const c = await config().catch(() => null);
  if (!c || !c.ready) { busy = false; return; }
  let identity = null;
  let permitted = false;
  let permitBoundary = false;
  let keepRecovery = false;
  try {
    const leftover = (await chrome.storage.session.get('autoPending')).autoPending;
    if (leftover) {
      keepRecovery = true;
      await recover(c, leftover);
      return;
    }
    const account = await ui(c, 'inspect');
    if (account.state !== 'account' || !hashShape(account.account_hash)) {
      if (account.reason_code === 'login_required') { await pauseIdentity(c, 'login_required'); return; }
      throw new Error('identity_not_ready');
    }
    if (c.account_hash && account.account_hash !== c.account_hash) { await pauseIdentity(c, 'account_changed'); return; }
    const next = await post(c, '/next', { account_hash: account.account_hash });
    if (!next.job) { await setStatus(queueMessage(next)); return; }
    if (!validJob(next.job)) throw new Error('invalid_job');
    const job = next.job;
    const expected = { job_id: jobId(job.url), title: job.title, account_hash: account.account_hash };
    identity = { attempt_id: job.attempt_id, lease_token: job.lease_token, account_hash: account.account_hash, profile_hash: '0'.repeat(64) };
    if ('application_text' in job) {
      // The extension never fills the intro, so it cannot submit an AI text: hold before opening anything.
      await post(c, '/review', { ...identity, outcome: 'held', reason_code: 'ai_text_unavailable' });
      await setStatus('AI 지원 문구는 이 연결에서 넣을 수 없어 해당 공고를 보류했습니다.');
      return;
    }
    let pending = { job, identity, phase: 'opening', receipt_token: null, checks: 0, created_at: Date.now(),
      next_check_at: 0, tab_id: c.tabId, connection_url: c.url };
    await savePending(c, pending);
    if (!await alive(c)) return;
    try { await bounded(chrome.tabs.update(c.tabId, { url: job.url })); }
    catch (error) { throw new Error(error?.message === 'browser_timeout' ? 'browser_timeout' : 'navigation_failed'); }
    let view = await waitUi(c, expected, ['openable', 'ready', 'already_applied']);
    if (view.account_hash && view.account_hash !== expected.account_hash) view = { state: 'held', reason_code: 'account_changed' };
    if (view.state === 'already_applied') { await post(c, '/review', { ...identity, outcome: 'already_applied', reason_code: 'already_applied' }); return; }
    if (view.state === 'openable') {
      await ui(c, 'open', expected);
      view = await waitUi(c, expected, ['ready', 'already_applied']);
    }
    if (view.state !== 'ready' || view.account_hash !== expected.account_hash || !hashShape(view.profile_hash)) {
      await post(c, '/review', { ...identity, outcome: 'held', reason_code: view.reason_code || 'ui_unrecognized' });
      await setStatus('지원 화면에 추가 확인이 필요해 해당 공고를 보류했습니다.');
      return;
    }
    identity.profile_hash = view.profile_hash;
    const review = await post(c, '/review', { ...identity, outcome: 'ready', reason_code: 'ready', evidence: 'application_form' });
    if (review.status !== 'ready' || !/^[A-Za-z0-9_-]{32,128}$/.test(review.receipt_token)) return;
    // Persist the separate read-only receipt BEFORE requesting any final permit.
    pending = { ...pending, identity, phase: 'permitting', receipt_token: review.receipt_token, next_check_at: Date.now() + 5000 };
    // A lost storage acknowledgement may already have persisted this phase.
    // Never send another review after crossing into permit preparation.
    permitBoundary = true;
    await savePending(c, pending);
    keepRecovery = true;
    // Use request-start time so a slow permit response cannot extend its lifetime.
    const requestedAt = Date.now();
    const permit = await post(c, '/permit', identity);
    permitted = permit.permit === true;
    if (!permitted) keepRecovery = false;
    if (!permitted || permit.expires_in_seconds !== 3 || Date.now() >= requestedAt + 3000 || !await alive(c)) return;
    const clicked = await ui(c, 'submit', { ...expected, profile_hash: view.profile_hash, attempt_id: job.attempt_id, permit_until: requestedAt + 3000 });
    if (clicked.state !== 'clicked') throw new Error('not_clicked');
    await savePending(c, { ...pending, phase: 'clicked' });
    const completed = await waitUi(c, expected, ['already_applied'], 10000);
    if (completed.state === 'already_applied' && completed.account_hash === expected.account_hash) {
      const result = await post(c, '/result', { ...identity, outcome: 'submitted', reason_code: 'submitted', evidence: 'cancel_button' });
      if (result.status !== 'submitted') throw new Error('unconfirmed');
      keepRecovery = false;
      await setStatus('해당 공고의 지원 취소 표시로 제출 완료를 확인했습니다.');
    } else {
      await post(c, '/result', { ...identity, outcome: 'uncertain', reason_code: 'submission_unconfirmed', evidence: null });
      await setStatus('지원 결과 미확인 · 자동으로 다시 누르지 않습니다.');
    }
  } catch (error) {
    const transient = !permitBoundary && !keepRecovery && ['navigation_failed', 'browser_timeout'].includes(error?.message) ? error.message : null;
    if (identity && (!permitBoundary || permitted)) {
      try {
        await post(c, permitted ? '/result' : '/review', { ...identity, outcome: permitted ? 'uncertain' : 'held', reason_code: permitted ? 'submission_unconfirmed' : transient || 'ui_unrecognized', ...(permitted ? { evidence: null } : {}) });
      } catch {}
    }
    await setStatus(keepRecovery ? '연결 응답이 끊겨 완료 표시를 자동 대조합니다. 제출 버튼은 다시 누르지 않습니다.' : transient ? '제출 허가 전 일시 오류를 Worklead에 보고했습니다. 저장된 기록과 조건으로 재시도 가능 여부를 확인합니다.' : '자동 지원 연결·화면을 확인하세요. 불확실한 제출은 재시도하지 않습니다.');
  } finally {
    try {
      if (!keepRecovery) await chrome.storage.session.remove('autoPending');
    } catch {
      // The retained receipt remains read-only recovery data. A failed cleanup
      // must not wedge the worker's single-flight guard until Chrome restarts.
      await setStatus('지원 작업 기록을 정리하지 못했습니다. 남은 기록은 완료 상태만 다시 대조합니다.');
    } finally {
      busy = false;
      if (reconnectNeeded && chrome.storage.local) { reconnectNeeded = false; void reconnect.resume({ force: true }); }
    }
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const popup = !sender.tab && sender.url === chrome.runtime.getURL('popup.html');
  if (message?.type === 'auto-connect' && popup) { configure(message, sender).then(respond, async error => { const message = connectionError(error, 'auto_apply'); await setStatus(message); respond({ok:false,message}); }); return true; }
  if (message?.type === 'auto-disconnect' && popup) { (chrome.storage.local ? reconnect.forget() : clear()).then(() => respond({ ok: true })); return true; }
  if (message?.type === 'auto-resume' && popup) { reconnect.resume({ manual: true }).then(respond); return true; }
  if (message?.type === 'auto-tick' && sender.frameId === 0 && isJobPage(sender.url)) {
    void config().then(c => { if (c?.tabId === sender.tab?.id) return tick(); });
  }
  return false;
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === AUTO_ALARM) void tick(); if (alarm.name === PAIR_ALARM && chrome.storage.local) void reconnect.resume(); });
chrome.tabs.onRemoved.addListener((id, info) => {
  void (async () => {
    if (chrome.storage.local && await reconnect.tabClosed(id, info?.isWindowClosing === true)) return;
    const c = await config(); if (c?.tabId === id) await clear();
  })();
});
chrome.runtime.onStartup?.addListener(() => { if (chrome.storage.local) void reconnect.boot(); });
chrome.runtime.onInstalled?.addListener(() => { if (chrome.storage.local) void reconnect.boot(); });
// MV3 service workers can resume without onStartup (for example an alarm wake).
if (chrome.storage.local) void reconnect.boot();
