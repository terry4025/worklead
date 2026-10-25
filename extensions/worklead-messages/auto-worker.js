import { connectionFrom, isJobPage } from './protocol.js';
import { AUTO_ALARM, JOBS_PERMISSION, jobId, validJob, hashShape } from './auto-protocol.js';

let busy = false;
let nextPoll = 0;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const setStatus = message => chrome.storage.session.set({ autoStatus: { message, observedAt: Date.now() } });
async function config() { return (await chrome.storage.session.get('autoBridge')).autoBridge; }
async function alive(c) { const current = await config(); return current?.token === c.token && current.tabId === c.tabId; }
async function post(c, path, body) {
  if (!['/next', '/review', '/permit', '/result'].includes(path) || !await alive(c)) throw new Error('stopped');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch(c.url + path, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + c.token }, body: JSON.stringify(body), signal: controller.signal, credentials: 'omit', redirect: 'error', cache: 'no-store' });
    if (!response.ok) throw new Error('not_accepted');
    return await response.json();
  } finally { clearTimeout(timer); }
}
async function ui(c, operation, expected = null) {
  if (!await alive(c)) throw new Error('stopped');
  const tab = await chrome.tabs.get(c.tabId);
  if (!isJobPage(tab.url)) throw new Error('wrong_tab');
  await chrome.scripting.executeScript({ target: { tabId: c.tabId, allFrames: false }, files: ['auto-content.js'], world: 'ISOLATED' });
  const result = await chrome.scripting.executeScript({ target: { tabId: c.tabId, allFrames: false }, world: 'ISOLATED', func: (op, data) => globalThis.__workleadAutoUi(op, data), args: [operation, expected] });
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
    await sleep(400);
  } while (Date.now() < until);
  return result;
}
async function clear() {
  const previous = await config();
  await chrome.storage.session.remove(['autoBridge', 'autoPending']);
  await chrome.alarms.clear(AUTO_ALARM);
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
  const value = connectionFrom(message.code);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !Number.isInteger(tab.id) || !isJobPage(tab.url)) throw new Error('jobs_tab_required');
  if (!await chrome.permissions.contains({ origins: ['http://127.0.0.1/*', JOBS_PERMISSION] })) throw new Error('permission_required');
  // Do not navigate the unread-monitoring tab. Its purpose remains read-only.
  if ((await chrome.storage.session.get('bridge')).bridge?.tabId === tab.id) throw new Error('separate_tab_required');
  await clear();
  await chrome.storage.session.set({ autoBridge: { ...value, tabId: tab.id, ready: false } });
  try {
    const c = await config();
    const result = await ui(c, 'inspect');
    if (result.state !== 'account' || !hashShape(result.account_hash)) throw new Error('login_required');
    await chrome.storage.session.set({ autoBridge: { ...c, ready: true } });
  } catch (error) { await clear(); throw error; }
  // Pairing tests the purpose-specific endpoint before issuing any click.
  await setStatus('자동 지원 전용 탭을 연결했습니다. Worklead의 50점 이상 후보를 기다립니다.');
  await chrome.alarms.create(AUTO_ALARM, { periodInMinutes: 0.5 });
  nextPoll = 0;
  void tick();
  return { ok: true };
}
async function tick() {
  if (busy || Date.now() < nextPoll) return;
  busy = true;
  nextPoll = Date.now() + 5000;
  const c = await config().catch(() => null);
  if (!c || !c.ready) { busy = false; return; }
  let identity = null;
  let permitted = false;
  try {
    const leftover = (await chrome.storage.session.get('autoPending')).autoPending;
    if (leftover) {
      // A suspended/restarted worker never repeats a final click.
      await setStatus('이전 지원 결과를 확인하지 못했습니다. 자동 재제출 없이 Worklead에서 대조합니다.');
      await chrome.storage.session.remove('autoPending');
      return;
    }
    const account = await ui(c, 'inspect');
    if (account.state !== 'account' || !hashShape(account.account_hash)) throw new Error('login_required');
    const next = await post(c, '/next', { account_hash: account.account_hash });
    if (!next.job) { await setStatus('연결 중 · 기준 점수 이상의 새 지원 대상을 기다립니다.'); return; }
    if (!validJob(next.job)) throw new Error('invalid_job');
    const job = next.job;
    const expected = { job_id: jobId(job.url), title: job.title, account_hash: account.account_hash };
    identity = { attempt_id: job.attempt_id, lease_token: job.lease_token, account_hash: account.account_hash, profile_hash: '0'.repeat(64) };
    await chrome.storage.session.set({ autoPending: { attempt_id: job.attempt_id } });
    if (!await alive(c)) return;
    await chrome.tabs.update(c.tabId, { url: job.url });
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
    const review = await post(c, '/review', { ...identity, outcome: 'ready', reason_code: 'ready' });
    if (review.status !== 'ready') return;
    // Use request-start time so a slow permit response cannot extend its lifetime.
    const requestedAt = Date.now();
    const permit = await post(c, '/permit', identity);
    permitted = permit.permit === true;
    if (!permitted || permit.expires_in_seconds !== 3 || Date.now() >= requestedAt + 3000 || !await alive(c)) return;
    const clicked = await ui(c, 'submit', { ...expected, profile_hash: view.profile_hash, attempt_id: job.attempt_id, permit_until: requestedAt + 3000 });
    if (clicked.state !== 'clicked') throw new Error('not_clicked');
    const completed = await waitUi(c, expected, ['already_applied'], 10000);
    if (completed.state === 'already_applied' && completed.account_hash === expected.account_hash) {
      await post(c, '/result', { ...identity, outcome: 'submitted', reason_code: 'submitted', evidence: 'cancel_button' });
      await setStatus('해당 공고의 지원 취소 표시로 제출 완료를 확인했습니다.');
    } else {
      await post(c, '/result', { ...identity, outcome: 'uncertain', reason_code: 'submission_unconfirmed', evidence: null });
      await setStatus('지원 결과 미확인 · 자동으로 다시 누르지 않습니다.');
    }
  } catch {
    if (identity) {
      try {
        await post(c, permitted ? '/result' : '/review', { ...identity, outcome: permitted ? 'uncertain' : 'held', reason_code: permitted ? 'submission_unconfirmed' : 'ui_unrecognized', ...(permitted ? { evidence: null } : {}) });
      } catch {}
    }
    await setStatus('자동 지원 연결·화면을 확인하세요. 불확실한 제출은 재시도하지 않습니다.');
  } finally {
    await chrome.storage.session.remove('autoPending');
    busy = false;
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const popup = !sender.tab && sender.url === chrome.runtime.getURL('popup.html');
  if (message?.type === 'auto-connect' && popup) { configure(message, sender).then(respond, () => respond({ ok: false, message: '로그인된 별도 당근 탭·권한·자동 지원 연결 코드를 확인하세요. 메시지 감시 탭은 함께 쓰지 않습니다.' })); return true; }
  if (message?.type === 'auto-disconnect' && popup) { clear().then(() => respond({ ok: true })); return true; }
  if (message?.type === 'auto-tick' && sender.frameId === 0 && isJobPage(sender.url)) {
    void config().then(c => { if (c?.tabId === sender.tab?.id) return tick(); });
  }
  return false;
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === AUTO_ALARM) void tick(); });
chrome.tabs.onRemoved.addListener(id => { void config().then(c => { if (c?.tabId === id) return clear(); }); });
