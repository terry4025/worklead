import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto, createHmac } from 'node:crypto';
import { createReconnect, pairProof, resumeHandshake, PAIR_URL, PAIR_ALARM } from '../worklead-messages/reconnect.js';

const durable = { url: PAIR_URL, token: 'd'.repeat(43) };
const account = 'a'.repeat(64);
const ephemeral = { url: 'http://127.0.0.1:43501', token: 'e'.repeat(43) };
const remembered = { ...durable, account_hash: account, enabled: true };
const popup = { url: 'chrome-extension://fixture/popup.html' };
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn) { for (let i = 0; i < 200; i++) { if (fn()) return; await pause(5); } throw new Error('fixture timed out'); }

function fixture(options = {}) {
  const local = options.pair === false ? {} : { autoPair: { ...remembered, ...options.pair } };
  const session = options.tab === false ? {} : { autoPairTab: { tabId: 7 } };
  const requests = [], connections = [], statuses = [], created = [], removed = [], access = [], alarms = [];
  const alarmState = new Map();
  const tabs = new Map(options.tab === false ? [] : [[7, { id: 7, url: 'https://jobs.daangn.com/', status: 'complete' }]]);
  let visible = account, fail = false, delayHello = null, delayResume = null, rejectProof = false;
  const store = memory => ({ async get(key) { return { [key]: memory[key] }; }, async set(values) { Object.assign(memory, values); }, async remove(key) { for (const k of [].concat(key)) delete memory[k]; } });
  const chrome = {
    storage: { local: { ...store(local), async setAccessLevel(value) { access.push(value); } }, session: store(session) },
    permissions: { contains: async () => true },
    alarms: { get: async name => alarmState.get(name), create: async (name, props) => { alarms.push([name, props]); alarmState.set(name, props); }, clear: async name => alarmState.delete(name) },
    tabs: { async get(id) { if (!tabs.has(id)) throw new Error('closed'); return tabs.get(id); },
      async create(value) { const tab = { id: 7 + created.length + 1, status: 'complete', ...value }; created.push(value); tabs.set(tab.id, tab); return tab; },
      async remove(id) { removed.push(id); tabs.delete(id); } },
  };
  const original = { chrome: globalThis.chrome, fetch: globalThis.fetch, crypto: globalThis.crypto };
  globalThis.chrome = chrome;
  if (!globalThis.crypto) globalThis.crypto = webcrypto;
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body), path = new URL(url).pathname;
    requests.push({ url, init, body });
    assert.equal(new URL(url).origin, PAIR_URL);
    assert.equal(init.headers.Authorization, undefined);
    assert.ok(!JSON.stringify(body).includes(durable.token));
    assert.equal(init.credentials, 'omit'); assert.equal(init.redirect, 'error'); assert.equal(init.cache, 'no-store');
    if (fail) throw new Error('offline');
    if (path === '/hello') {
      if (delayHello) await delayHello;
      return { ok: true, json: async () => ({ server_nonce: 'b'.repeat(64), proof: rejectProof ? '0'.repeat(64) : await pairProof(durable.token, ['server', body.client_nonce, 'b'.repeat(64), body.account_hash]) }) };
    }
    assert.equal(path, '/resume');
    assert.equal(body.proof, await pairProof(durable.token, ['client', body.client_nonce, body.server_nonce, body.account_hash]));
    if (delayResume) await delayResume;
    return { ok: true, json: async () => ({ connection: ephemeral, proof: rejectProof ? '0'.repeat(64) : await pairProof(durable.token, ['session', body.client_nonce, body.server_nonce, body.account_hash, ephemeral.url, ephemeral.token]) }) };
  };
  const callbacks = {
    readAccount: async () => visible ? { state: 'account', account_hash: visible } : { state: 'held', reason_code: 'login_required' },
    connected: async (c, id, identity, current) => { if (!current()) return; connections.push({ c, id, identity }); session.autoBridge = { ...c, tabId: id, account_hash: identity, ready: true }; },
    clearSession: async () => { delete session.autoBridge; delete session.autoPending; }, status: async s => statuses.push(s),
  };
  const reconnect = createReconnect(callbacks);
  return { local, session, requests, connections, statuses, created, removed, access, alarms, alarmState, chrome, reconnect, callbacks,
    account: value => { visible = value; }, offline: () => { fail = true; }, badProof: () => { rejectProof = true; },
    holdHello() { let release; delayHello = new Promise(r => { release = r; }); return release; },
    holdResume() { let release; delayResume = new Promise(r => { release = r; }); return release; },
    restore() { globalThis.chrome = original.chrome; globalThis.fetch = original.fetch; if (!original.crypto) delete globalThis.crypto; } };
}

test('pair proof uses the exact domain-separated HMAC wire format', async () => {
  const original = globalThis.crypto; if (!original) globalThis.crypto = webcrypto;
  try { assert.equal(await pairProof('x'.repeat(43), ['client', '1', '2', '3']), createHmac('sha256', 'x'.repeat(43)).update('worklead-auto-pair-v1\nclient\n1\n2\n3').digest('hex')); }
  finally { if (!original) delete globalThis.crypto; }
});
test('first consent pairs only after mutual proof and persists no leases or tab/content data', async () => {
  const h = fixture({ pair: false });
  try {
    assert.equal((await h.reconnect.initial(durable, 7)).ok, true);
    assert.deepEqual(h.local, { autoPair: remembered });
    assert.deepEqual(h.connections, [{ c: ephemeral, id: 7, identity: account }]);
    assert.deepEqual(h.requests.map(x => new URL(x.url).pathname), ['/hello', '/resume']);
    assert.deepEqual(h.access, [{ accessLevel: 'TRUSTED_CONTEXTS' }]);
    assert.equal(h.session.autoPairTab.tabId, 7);
    assert.ok(h.alarms.some(x => x[0] === PAIR_ALARM));
  } finally { h.restore(); }
});
test('untrusted local server receives no client proof and cannot save credentials', async () => {
  const h = fixture({ pair: false }); h.badProof();
  try { await assert.rejects(h.reconnect.initial(durable, 7), /untrusted_server/); assert.equal(h.requests.length, 1); assert.deepEqual(h.local, {}); assert.deepEqual(h.connections, []); }
  finally { h.restore(); }
});
test('forged session proof cannot replace the ephemeral connection', async () => {
  const h = fixture(); const release = h.holdResume();
  try {
    const pending = resumeHandshake(durable, account); await until(() => h.requests.length === 2); h.badProof(); release();
    await assert.rejects(pending, /untrusted_session/); assert.deepEqual(h.connections, []);
  } finally { h.restore(); }
});
test('browser restart creates exactly one inactive owned tab, never adopts existing tabs', async () => {
  const h = fixture({ tab: false });
  // An unrelated jobs tab is deliberately not the remembered extension tab.
  h.chrome.tabs.query = async () => { throw new Error('arbitrary tab query is forbidden'); };
  try {
    const results = await Promise.all([h.reconnect.resume(), h.reconnect.resume(), h.reconnect.boot()]);
    assert.equal(results[0].ok, true); assert.equal(h.created.length, 1);
    assert.deepEqual(h.created[0], { url: 'https://jobs.daangn.com/', active: false });
    assert.equal(h.connections.length, 1);
    await h.reconnect.resume(); assert.equal(h.requests.length, 2); assert.equal(h.created.length, 1);
  } finally { h.restore(); }
});
test('app restart can replace an apparently-ready but dead ephemeral connection', async () => {
  const h = fixture(); h.session.autoBridge = { ...ephemeral, ready: true, tabId: 7 };
  try { assert.equal((await h.reconnect.resume({ force: true })).ok, true); assert.equal(h.requests.length, 2); assert.equal(h.connections.length, 1); }
  finally { h.restore(); }
});

test('a ready session is checked and restores work scheduling without rotating its pending receipt', async () => {
  const h = fixture(), resumed = [], checked = [];
  h.session.autoBridge = { ...ephemeral, ready: true, tabId: 7, account_hash: account };
  h.session.autoPending = { phase: 'permitting', receipt_token: 'r'.repeat(43) };
  const pending = h.session.autoPending;
  const controller = createReconnect({ ...h.callbacks,
    validateConnection: async c => checked.push(c),
    resumeConnected: async (...args) => resumed.push(args),
  });
  try {
    await controller.boot();
    assert.equal(checked.length, 1); assert.equal(resumed.length, 1);
    assert.equal(resumed[0][1], 7); assert.equal(resumed[0][2], account);
    assert.equal(h.session.autoPending, pending);
    assert.equal(h.requests.length, 0); assert.equal(h.connections.length, 0);
  } finally { h.restore(); }
});

test('an expired ready session automatically exchanges the remembered grant after its failed ping', async () => {
  const h = fixture(), resumed = [], checked = [];
  h.session.autoBridge = { ...ephemeral, token: 'old'.repeat(15), ready: true, tabId: 7, account_hash: account };
  const controller = createReconnect({ ...h.callbacks,
    validateConnection: async c => { checked.push(c.token); if (c.token !== ephemeral.token) throw new Error('expired_code'); },
    resumeConnected: async c => resumed.push(c),
  });
  try {
    assert.equal((await controller.resume()).ok, true);
    assert.equal(checked.length, 2); assert.equal(resumed.length, 0);
    assert.equal(h.requests.length, 2); assert.equal(h.connections.length, 1);
    assert.equal(h.session.autoBridge.token, ephemeral.token);
  } finally { h.restore(); }
});

for (const changed of ['account', 'tab', 'stopped']) test('ready state cannot bypass ' + changed + ' revalidation', async () => {
  const h = fixture(), checked = [], resumed = [];
  h.session.autoBridge = { ...ephemeral, ready: true, tabId: 7, account_hash: account };
  if (changed === 'account') h.account('c'.repeat(64));
  if (changed === 'tab') h.chrome.tabs.get = async id => ({ id, url: 'https://example.org/', status: 'complete' });
  if (changed === 'stopped') h.local.autoPair.enabled = false;
  const controller = createReconnect({ ...h.callbacks, validateConnection: async c => checked.push(c), resumeConnected: async c => resumed.push(c) });
  try {
    assert.equal((await controller.resume()).ok, false);
    assert.equal(checked.length, 0); assert.equal(resumed.length, 0);
    assert.equal(h.requests.length, 0); assert.equal(h.local.autoPair.enabled, false);
  } finally { h.restore(); }
});

test('disconnect during a ready-session ping prevents later scheduling and handshake', async () => {
  const h = fixture(), resumed = [];
  h.session.autoBridge = { ...ephemeral, ready: true, tabId: 7, account_hash: account };
  let started = false, release;
  const gate = new Promise(resolve => { release = resolve; });
  const controller = createReconnect({ ...h.callbacks,
    validateConnection: async () => { started = true; await gate; },
    resumeConnected: async c => resumed.push(c),
  });
  try {
    const pending = controller.resume(); await until(() => started);
    await controller.forget(); release(); await pending;
    assert.equal(resumed.length, 0); assert.equal(h.requests.length, 0);
    assert.equal(h.session.autoBridge, undefined); assert.equal(h.local.autoPair, undefined);
  } finally { h.restore(); }
});

test('repeated boot preserves the reconnect alarm deadline and stopped grants create no alarms', async () => {
  const h = fixture();
  try {
    await h.reconnect.boot(); await h.reconnect.boot(); await h.reconnect.boot();
    assert.equal(h.alarms.filter(a => a[0] === PAIR_ALARM).length, 1);
    await h.reconnect.pause('stopped');
    const creates = h.alarms.length;
    await h.reconnect.boot();
    assert.equal(h.alarms.length, creates); assert.equal(h.alarmState.size, 0);
  } finally { h.restore(); }
});

test('stop wins over a reconnect alarm creation that completes late', async () => {
  const h = fixture(); let started = false, release;
  const gate = new Promise(resolve => { release = resolve; });
  const create = h.chrome.alarms.create;
  h.chrome.alarms.create = async (...args) => { started = true; await gate; return create(...args); };
  try {
    const boot = h.reconnect.boot(); await until(() => started);
    const stopped = h.reconnect.forget(); await until(() => h.local.autoPair === undefined);
    release(); await stopped; await boot;
    assert.equal(h.alarmState.size, 0); assert.equal(h.requests.length, 0);
    assert.equal(h.connections.length, 0);
  } finally { release(); h.restore(); }
});

test('a loaded page with an unhydrated account menu retries without disabling the remembered grant', async () => {
  const h = fixture({ tab: false }); const now = Date.now;
  let ready = false;
  h.callbacks.readAccount = async () => ready ? { state: 'account', account_hash: account } : { state: 'held', reason_code: 'ui_unrecognized' };
  const pending = createReconnect(h.callbacks);
  try {
    assert.equal((await pending.resume()).ok, false);
    assert.equal(h.local.autoPair.enabled, true);
    assert.equal(h.requests.length, 0); assert.equal(h.created.length, 1);
    ready = true;
    // A later alarm retries the same controller and reuses its owned tab.
    Date.now = () => now() + 31000;
    assert.equal((await pending.resume()).ok, true);
    assert.equal(h.connections.length, 1); assert.equal(h.created.length, 1);
  } finally { Date.now = now; h.restore(); }
});
for (const identity of [null, 'c'.repeat(64)]) test('changed or logged out identity pauses without any broker request: ' + identity, async () => {
  const h = fixture(); h.account(identity);
  try {
    assert.equal((await h.reconnect.resume()).ok, false);
    assert.equal(h.local.autoPair.enabled, false); assert.equal(h.requests.length, 0);
    h.account(account); await h.reconnect.resume(); assert.equal(h.requests.length, 0);
    assert.equal((await h.reconnect.resume({ manual: true })).ok, true); assert.equal(h.local.autoPair.enabled, true);
  } finally { h.restore(); }
});
test('manual tab closure pauses; browser window closure preserves credential for startup', async () => {
  const h = fixture();
  try {
    assert.equal(await h.reconnect.tabClosed(7, false), true); assert.equal(h.local.autoPair.enabled, false);
    await h.reconnect.resume(); assert.equal(h.requests.length, 0);
    await h.reconnect.resume({ manual: true });
    assert.equal(await h.reconnect.tabClosed(7, true), true); assert.equal(h.local.autoPair.enabled, true);
    assert.equal(h.session.autoPairTab, undefined);
    await h.reconnect.resume(); assert.equal(h.created.length, 1);
  } finally { h.restore(); }
});
test('navigation away from dedicated jobs tab pauses rather than commandeering another page', async () => {
  const h = fixture(); h.chrome.tabs.get = async id => ({ id, url: 'https://example.org/', status: 'complete' });
  try { await h.reconnect.resume(); assert.equal(h.local.autoPair.enabled, false); assert.equal(h.requests.length, 0); assert.equal(h.created.length, 0); }
  finally { h.restore(); }
});
test('offline app uses bounded backoff and single flight without opening repeated tabs', async () => {
  const h = fixture({ tab: false }); h.offline();
  try { await Promise.all([h.reconnect.resume(), h.reconnect.resume()]); await h.reconnect.resume(); assert.equal(h.requests.length, 1); assert.equal(h.created.length, 1); assert.equal(h.local.autoPair.enabled, true); }
  finally { h.restore(); }
});
for (const stage of ['hello', 'resume']) test('disconnect during delayed ' + stage + ' wins over late callbacks', async () => {
  const h = fixture(); const release = stage === 'hello' ? h.holdHello() : h.holdResume();
  try {
    const pending = h.reconnect.resume(); await until(() => h.requests.length === (stage === 'hello' ? 1 : 2));
    await h.reconnect.forget(); release(); await pending;
    assert.deepEqual(h.local, {}); assert.equal(h.session.autoBridge, undefined); assert.deepEqual(h.connections, []);
    assert.equal(h.requests.length, stage === 'hello' ? 1 : 2);
    await h.reconnect.resume(); assert.equal(h.connections.length, 0);
  } finally { h.restore(); }
});
test('disconnect during a delayed credential write is serialized after it and deletes the key', async () => {
  const h = fixture({ pair: false });
  const original = h.chrome.storage.local.set; let release, started = false;
  const gate = new Promise(r => { release = r; });
  h.chrome.storage.local.set = async value => { started = true; await gate; return original(value); };
  try {
    const pending = h.reconnect.initial(durable, 7); await until(() => started);
    const stopped = h.reconnect.forget(); release(); await stopped; await assert.rejects(pending, /cancelled/);
    assert.deepEqual(h.local, {}); assert.deepEqual(h.connections, []);
  } finally { h.restore(); }
});

test('real worker wires startup/alarm/401 recovery and popup-only forget without invoking applications', async () => {
  const h = fixture(); const listeners = [], alarmListeners = [], startup = [], installed = [], removal = [];
  const previousFetch = globalThis.fetch;
  h.chrome.runtime = { getURL: name => 'chrome-extension://fixture/' + name, onMessage: { addListener: fn => listeners.push(fn) }, onStartup: { addListener: fn => startup.push(fn) }, onInstalled: { addListener: fn => installed.push(fn) } };
  h.chrome.tabs.query = async () => [{ id: 7, url: 'https://jobs.daangn.com/' }];
  h.chrome.tabs.onRemoved = { addListener: fn => removal.push(fn) };
  h.chrome.alarms.onAlarm = { addListener: fn => alarmListeners.push(fn) };
  const operations = [];
  h.chrome.scripting = { executeScript: async input => { if (input.files) return [{ frameId: 0 }]; if (input.args) { operations.push(input.args[0]); return [{ result: { state: 'account', account_hash: account } }]; } return [{ result: null }]; } };
  let nextCalls = 0;
  globalThis.fetch = async (url, init) => {
    if (new URL(url).pathname === '/ping') return {ok:true,json:async()=>({purpose:'auto_apply',version:1})};
    if (new URL(url).pathname === '/next') { nextCalls++; return { ok: false, status: 401 }; }
    return previousFetch(url, init);
  };
  const dispatch = (message, sender) => new Promise(resolve => { if (!listeners.some(fn => fn(message, sender, resolve) === true)) resolve(undefined); });
  try {
    await import('../worklead-messages/auto-worker.js?durable=' + Math.random());
    await until(() => nextCalls === 1); await until(() => h.requests.length === 4);
    assert.equal(startup.length, 1); assert.equal(installed.length, 1);
    assert.ok(alarmListeners.length); assert.ok(removal.length);
    assert.ok(operations.every(op => op === 'inspect'));
    assert.equal(await dispatch({ type: 'auto-disconnect' }, { tab: { id: 7 }, url: 'https://jobs.daangn.com/' }), undefined);
    assert.equal((await dispatch({ type: 'auto-disconnect' }, popup)).ok, true);
    assert.equal(h.local.autoPair, undefined); assert.equal(h.session.autoBridge, undefined);
    const count = h.requests.length; startup[0](); installed[0](); alarmListeners[0]({ name: PAIR_ALARM }); await pause(20);
    assert.equal(h.requests.length, count);
  } finally { h.restore(); }
});

for (const phase of [null, 'permitting', 'clicked']) test('worker boot restores a healthy ready session and its missing work alarm: ' + phase, async () => {
  const h = fixture(), paths = [], operations = [];
  const savedJob = { attempt_id: 'auto_restart', lead_id: 'lead_restart', title: '페이지 제작',
    url: 'https://jobs.daangn.com/job-posts/페이지-제작-abcdefghijkl', lease_token: 'l'.repeat(43) };
  h.session.autoBridge = { ...ephemeral, ready: true, tabId: 7, account_hash: account };
  if (phase) {
    h.session.autoPending = { job: savedJob,
      identity: { attempt_id: savedJob.attempt_id, lease_token: savedJob.lease_token, account_hash: account, profile_hash: 'b'.repeat(64) },
      phase, receipt_token: 'r'.repeat(43), checks: 0, created_at: Date.now(), next_check_at: 0,
      tab_id: 7, connection_url: ephemeral.url };
    h.chrome.tabs.get = async id => ({ id, url: savedJob.url, status: 'complete' });
  }
  h.chrome.runtime = { getURL: name => 'chrome-extension://fixture/' + name,
    onMessage: { addListener() {} }, onStartup: { addListener() {} }, onInstalled: { addListener() {} } };
  h.chrome.tabs.onRemoved = { addListener() {} };
  h.chrome.alarms.onAlarm = { addListener() {} };
  h.chrome.scripting = { executeScript: async input => {
    if (input.files) return [{ frameId: 0 }];
    const [operation, expected] = input.args; operations.push(operation);
    return [{ result: { state: expected ? 'already_applied' : 'account', account_hash: account } }];
  } };
  globalThis.fetch = async (url, init) => {
    assert.equal(new URL(url).origin, ephemeral.url);
    assert.equal(init.headers.Authorization, 'Bearer ' + ephemeral.token);
    const path = new URL(url).pathname; paths.push(path);
    assert.ok(['/ping', '/next', '/reconcile'].includes(path), 'restoring a session must never grant or submit');
    return { ok: true, json: async () => path === '/ping' ? { purpose: 'auto_apply', version: 1 } : path === '/next' ? { job: null } : { status: 'submitted' } };
  };
  try {
    await import('../worklead-messages/auto-worker.js?ready-restart=' + Math.random());
    await until(() => paths.includes(phase ? '/reconcile' : '/next'));
    await until(() => phase ? h.session.autoPending === undefined : !!h.session.autoStatus);
    assert.deepEqual(paths, ['/ping', phase ? '/reconcile' : '/next']);
    assert.ok(h.alarms.some(a => a[0] === 'worklead-auto-apply'));
    assert.ok(operations.every(op => op === 'inspect'));
    assert.equal(h.session.autoBridge.token, ephemeral.token);
    assert.equal(h.requests.length, 0); assert.equal(h.created.length, 0);
  } finally { h.restore(); }
});

test('late auto work-alarm creation cannot undo stop or start a queued application', async () => {
  const h = fixture(), paths = [], listeners = [];
  let started = false, release;
  const gate = new Promise(resolve => { release = resolve; }), create = h.chrome.alarms.create;
  h.session.autoBridge = { ...ephemeral, ready: true, tabId: 7, account_hash: account };
  h.chrome.alarms.create = async (name, props) => { if (name === 'worklead-auto-apply') { started = true; await gate; } return create(name, props); };
  h.chrome.runtime = { getURL: name => 'chrome-extension://fixture/' + name, onMessage: { addListener: fn => listeners.push(fn) } };
  h.chrome.tabs.onRemoved = { addListener() {} }; h.chrome.alarms.onAlarm = { addListener() {} };
  h.chrome.scripting = { executeScript: async input => input.files ? [{ frameId: 0 }] : [{ result: { state: 'account', account_hash: account } }] };
  globalThis.fetch = async url => { const path = new URL(url).pathname; paths.push(path); assert.equal(path, '/ping'); return { ok: true, json: async () => ({ purpose: 'auto_apply', version: 1 }) }; };
  try {
    await import('../worklead-messages/auto-worker.js?late-work-alarm=' + Math.random());
    await until(() => started);
    const stopped = new Promise(resolve => listeners.some(fn => fn({ type: 'auto-disconnect' }, popup, resolve) === true));
    await until(() => h.session.autoBridge === undefined);
    release(); assert.equal((await stopped).ok, true); await pause(20);
    assert.equal(h.alarmState.size, 0); assert.equal(h.local.autoPair, undefined);
    assert.deepEqual(paths, ['/ping']); assert.equal(h.created.length, 0);
  } finally { release(); h.restore(); }
});
