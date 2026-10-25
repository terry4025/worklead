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
  let stage = 'home', liveUrl = 'https://jobs.daangn.com/';
  const account = 'a'.repeat(64), profile = 'b'.repeat(64);
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
      if (operation === 'inspect' && !expected) return [{ result: { state: 'account', account_hash: account } }];
      if (operation === 'open') { stage = 'panel'; clicks.push('open'); return [{ result: { state: 'opened' } }]; }
      if (operation === 'submit') { assert.equal(expected.profile_hash, profile); assert.ok(expected.permit_until > Date.now()); stage = 'done'; clicks.push('submit'); return [{ result: { state: 'clicked' } }]; }
      const state = stage === 'detail' ? (outcome === 'already' ? 'already_applied' : 'openable') : stage === 'panel' ? (outcome === 'questions' ? 'held' : 'ready') : 'already_applied';
      return [{ result: { state, account_hash: account, profile_hash: profile, ...(outcome === 'questions' ? { reason_code: 'questions_required' } : {}) } }];
    } },
    alarms: { clear: async () => true, create: async (...args) => alarms.push(args), onAlarm: { addListener() {} } },
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
    if (path === '/review') { result = { status: body.outcome }; if (body.outcome !== 'ready') resolveResult(); }
    if (path === '/permit') {
      if (outcome === 'lost_permit') { resolveResult(); throw new Error('reply_lost'); }
      result = { permit: outcome !== 'denied', expires_in_seconds: outcome !== 'denied' ? 3 : 0 };
      if (outcome === 'denied') resolveResult();
    }
    if (path === '/result') { result = { status: body.outcome }; resolveResult(); }
    return { ok: true, json: async () => result };
  };
  await import('../worklead-messages/auto-worker.js?case=' + outcome + '-' + Math.random());
  const dispatch = (message, sender) => new Promise(resolve => { if (!listeners.some(fn => fn(message, sender, resolve) === true)) resolve(undefined); });
  const popup = { url: 'chrome-extension://fixture/popup.html' };
  return { memory, requests, clicks, navigations, injected, alarms, dispatch, popup, settled, restore() { globalThis.chrome = original.chrome; globalThis.fetch = original.fetch; } };
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
