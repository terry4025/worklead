import { test } from 'node:test';
import assert from 'node:assert/strict';
import { jobId, validJob } from '../worklead-messages/auto-protocol.js';

const job = { attempt_id: 'auto_test', lead_id: 'lead_test', title: '페이지 제작', url: 'https://jobs.daangn.com/job-posts/페이지-제작-abcdefghijkl', lease_token: 'x'.repeat(43) };
const pair = { url: 'http://127.0.0.1:43500', token: 't'.repeat(43) };
test('only exact public job URLs and shaped commands can navigate the selected tab', () => {
  assert.equal(jobId(job.url), 'abcdefghijkl');
  assert.equal(jobId(job.url + '/apply'), 'abcdefghijkl');
  assert.ok(validJob(job));
  for (const url of ['https://evil.invalid/job-posts/abcdefghijkl', job.url + '?redirect=bad', job.url + '#other', 'https://jobs.daangn.com/me/resume', 'https://user@jobs.daangn.com/job-posts/abcdefghijkl']) assert.equal(jobId(url), null);
  assert.ok(!validJob({ ...job, extra: 'not_allowed' }));
});

async function harness(outcome = 'success') {
  const memory = {};
  const listeners = [];
  const requests = [], clicks = [], navigations = [], injected = [], alarms = [];
  const alarmListeners = [];
  let stage = 'home', liveUrl = 'https://jobs.daangn.com/';
  const account = 'a'.repeat(64), profile = 'b'.repeat(64);
  let visibleAccount = account;
  let resolveResult;
  const settled = new Promise(resolve => { resolveResult = resolve; });
  const mockChrome = {
    runtime: { getURL: x => 'chrome-extension://fixture/' + x, onMessage: { addListener: fn => listeners.push(fn) } },
    storage: { session: {
      async get(key) { return { [key]: memory[key] }; }, async set(values) { Object.assign(memory, values); },
      async remove(keys) { for (const key of [].concat(keys)) delete memory[key]; },
    } },
    permissions: { contains: async () => true },
    tabs: { query: async () => [{ id: 7, url: liveUrl }], get: async () => ({ id: 7, url: liveUrl }), update: async (id, { url }) => { assert.equal(id, 7); navigations.push(url); liveUrl = url; stage = 'detail'; }, onRemoved: { addListener() {} } },
    scripting: { executeScript: async input => {
      assert.equal(input.target.tabId, 7); assert.equal(input.target.allFrames, false); assert.equal(input.world, 'ISOLATED');
      if (input.files) { injected.push(input.files); return [{ frameId: 0 }]; }
      const [operation, expected] = input.args;
      if (outcome === 'stalled_recovery_read' && operation === 'inspect') return new Promise(() => {});
      if (operation === 'inspect' && !expected) return [{ result: { state: 'account', account_hash: visibleAccount } }];
      if (operation === 'open') { stage = 'panel'; clicks.push('open'); return [{ result: { state: 'opened' } }]; }
      if (operation === 'submit') { assert.equal(expected.profile_hash, profile); assert.ok(expected.permit_until > Date.now()); stage = 'done'; clicks.push('submit'); return [{ result: { state: 'clicked' } }]; }
      const state = stage === 'detail' ? (outcome === 'already' ? 'already_applied' : 'openable') : stage === 'panel' ? (outcome === 'questions' ? 'held' : 'ready') : 'already_applied';
      if (outcome === 'recovery_login') return [{ result: { state: 'held', reason_code: 'login_required' } }];
      return [{ result: { state, account_hash: visibleAccount, profile_hash: profile, ...(outcome === 'questions' ? { reason_code: 'questions_required' } : {}) } }];
    } },
    alarms: { clear: async () => true, create: async (...args) => alarms.push(args), onAlarm: { addListener: fn => alarmListeners.push(fn) } },
  };
  const original = { chrome: globalThis.chrome, fetch: globalThis.fetch };
  globalThis.chrome = mockChrome;
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body), path = new URL(url).pathname;
    requests.push({ path, options, body });
    assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
    assert.equal(options.headers.Authorization, 'Bearer ' + pair.token);
    assert.ok(!JSON.stringify(body).includes(pair.token));
    let result;
    if (path === '/next') result = { job: requests.filter(x => x.path === '/next').length === 1 ? job : null };
    if (path === '/review') { result = { status: body.outcome, ...(body.outcome === 'ready' ? { receipt_token: 'r'.repeat(43) } : {}) }; if (body.outcome !== 'ready') resolveResult(); }
    if (path === '/permit') {
      if (outcome === 'lost_permit') { resolveResult(); throw new Error('reply_lost'); }
      result = { permit: outcome !== 'denied', expires_in_seconds: outcome !== 'denied' ? 3 : 0 };
      if (outcome === 'denied') resolveResult();
    }
    if (path === '/result') { result = { status: body.outcome }; resolveResult(); }
    if (path === '/reconcile') { result = { status: 'submitted' }; resolveResult(); }
    if (path === '/pause') { result = { status: 'paused' }; resolveResult(); }
    return { ok: true, json: async () => result };
  };
  await import('../worklead-messages/auto-worker.js?case=' + outcome + '-' + Math.random());
  const dispatch = (message, sender) => new Promise(resolve => { if (!listeners.some(fn => fn(message, sender, resolve) === true)) resolve(undefined); });
  const popup = { url: 'chrome-extension://fixture/popup.html' };
  return { memory, requests, clicks, navigations, injected, alarms, dispatch, popup, settled,
    seedRecovery(phase = 'clicked', patch = {}) {
      memory.autoBridge = { ...pair, tabId: 7, ready: true };
      memory.autoPending = { job, identity: { attempt_id: job.attempt_id, lease_token: job.lease_token, account_hash: account, profile_hash: profile },
        receipt_token: 'r'.repeat(43), phase, checks: 0, created_at: Date.now(), next_check_at: 0, tab_id: 7, connection_url: pair.url, ...patch };
      stage = 'done'; liveUrl = job.url;
    },
    changeAccount() { visibleAccount = 'c'.repeat(64); },
    async wake(timeout = 4500) {
      const previous = memory.autoStatus, checks = memory.autoPending?.checks;
      alarmListeners[0]({ name: 'worklead-auto-apply' });
      await new Promise(resolve => setTimeout(resolve, 30));
      if ((memory.autoPending?.checks ?? 0) > (checks ?? 0)) {
        const until = Date.now() + timeout;
        while (memory.autoStatus === previous && Date.now() < until) await new Promise(resolve => setTimeout(resolve, 10));
        assert.notEqual(memory.autoStatus, previous, 'recovery must finish before fixture teardown');
      }
    },
    async restartWorker() {
      listeners.length = 0; alarmListeners.length = 0;
      await import('../worklead-messages/auto-worker.js?restart=' + Math.random());
    },
    restore() { globalThis.chrome = original.chrome; globalThis.fetch = original.fetch; } };
}
for (const outcome of ['success', 'already', 'denied', 'lost_permit']) test('worker: ' + outcome + ' never duplicates the final click', async () => {
  const h = await harness(outcome);
  try {
    assert.equal(await h.dispatch({ type: 'auto-connect', code: JSON.stringify(pair), confirm: true }, { tab: { id: 7 }, url: job.url }), undefined);
    assert.equal((await h.dispatch({ type: 'auto-connect', code: JSON.stringify(pair), confirm: false }, h.popup)).ok, false);
    assert.equal((await h.dispatch({ type: 'auto-connect', code: JSON.stringify(pair), confirm: true }, h.popup)).ok, true);
    await Promise.race([h.settled, new Promise((_, reject) => setTimeout(() => reject(new Error('flow timed out')), 2000))]);
    // Drain the final storage/status cleanup before releasing this fixture.
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.deepEqual(h.navigations, [job.url]);
    assert.deepEqual(h.clicks, outcome === 'success' ? ['open', 'submit'] : outcome === 'already' ? [] : ['open']);
    if (outcome === 'success') assert.equal(h.requests.find(x => x.path === '/result').body.evidence, 'cancel_button');
    if (outcome === 'already') assert.ok(!h.requests.some(x => x.path === '/permit'));
    if (outcome === 'lost_permit') assert.equal(h.requests.filter(x => x.path === '/permit').length, 1);
    await h.dispatch({ type: 'auto-disconnect' }, h.popup);
    assert.equal(h.memory.autoBridge, undefined);
  } finally { h.restore(); }
});

for (const phase of ['permitting', 'clicked']) test('restarted worker only reconciles existing completion after ' + phase, async () => {
  const h = await harness();
  try {
    h.seedRecovery(phase);
    await h.restartWorker();
    await h.wake();
    assert.deepEqual(h.requests.map(r => r.path), ['/reconcile']);
    assert.deepEqual(h.clicks, []);
    assert.deepEqual(h.navigations, []);
    assert.equal(h.memory.autoPending, undefined);
    const body = h.requests[0].body;
    assert.equal(body.receipt_token, 'r'.repeat(43));
    assert.equal(body.evidence, 'cancel_button');
    assert.equal(body.lease_token, undefined);
  } finally { h.restore(); }
});

test('lost permit reply preserves only the read-only recovery path across worker restart', async () => {
  const h = await harness('lost_permit');
  try {
    await h.dispatch({ type: 'auto-connect', code: JSON.stringify(pair), confirm: true }, h.popup);
    await h.settled;
    await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(h.memory.autoPending.phase, 'permitting');
    assert.equal(h.requests.filter(r => r.path === '/permit').length, 1);
    h.memory.autoPending.next_check_at = 0;
    await h.restartWorker();
    await h.wake();
    assert.deepEqual(h.clicks, ['open']);
    assert.equal(h.requests.filter(r => r.path === '/permit').length, 1);
    // The fixture is still at the intro panel: recovery cannot submit it.
    assert.ok(h.memory.autoPending);
    await h.dispatch({ type: 'auto-disconnect' }, h.popup);
    assert.equal(h.memory.autoPending, undefined);
  } finally { h.restore(); }
});

test('pre-permit interrupted review is held without resuming either click', async () => {
  const h = await harness();
  try {
    h.seedRecovery('opening'); await h.wake();
    assert.deepEqual(h.clicks, []);
    assert.deepEqual(h.requests.map(r => r.path), ['/review']);
    assert.equal(h.requests[0].body.outcome, 'held');
    assert.equal(h.memory.autoPending, undefined);
  } finally { h.restore(); }
});

for (const patch of [{ tab_id: 8 }, { connection_url: 'http://127.0.0.1:43501' }, { checks: 3 }, { created_at: Date.now() - 901000 },
  { job: { ...job, url: 'https://untrusted.invalid/job-posts/abcdefghijkl' } }]) test('invalid or exhausted recovery never navigates, clicks or requests permits: ' + JSON.stringify(patch), async () => {
  const h = await harness();
  try {
    h.seedRecovery('clicked', patch); await h.wake();
    assert.deepEqual(h.requests, []); assert.deepEqual(h.navigations, []); assert.deepEqual(h.clicks, []);
    assert.equal(h.memory.autoPending, undefined);
  } finally { h.restore(); }
});

test('changed account revokes the recovery connection rather than resuming after account switches back', async () => {
  const h = await harness();
  try {
    h.seedRecovery(); h.changeAccount(); await h.wake();
    assert.equal(h.memory.autoPending, undefined);
    assert.equal(h.memory.autoBridge, undefined);
    assert.deepEqual(h.requests.map(r => r.path), ['/pause']); assert.deepEqual(h.clicks, []);
    assert.equal(h.requests[0].body.reason, 'account_changed');
    await h.restartWorker(); await h.wake();
    assert.equal(h.requests.length, 1);
  } finally { h.restore(); }
});

test('stalled browser inspection times out and leaves only bounded read-only recovery', async () => {
  const h = await harness('stalled_recovery_read');
  try {
    h.seedRecovery(); await h.wake(10000);
    assert.equal(h.memory.autoPending.checks, 1);
    assert.deepEqual(h.requests, []); assert.deepEqual(h.clicks, []);
    assert.match(h.memory.autoStatus.message, /자동 대조 중/);
    await h.dispatch({ type: 'auto-disconnect' }, h.popup);
    assert.equal(h.memory.autoPending, undefined);
  } finally { h.restore(); }
});
