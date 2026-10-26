import { connectionFrom, isJobPage } from './protocol.js';
import { connectionError, failureCode } from './connection-errors.js';
import { hashShape, JOBS_PERMISSION } from './auto-protocol.js';

export const PAIR_URL = 'http://127.0.0.1:58347';
export const PAIR_ALARM = 'worklead-auto-reconnect';
export const MESSAGE_PAIR_URL = 'http://127.0.0.1:58348';
export const MESSAGE_PAIR_ALARM = 'worklead-message-reconnect';
const PREFIX = 'worklead-auto-pair-v1';
const enc = new TextEncoder();
const hex = value => [...new Uint8Array(value)].map(x => x.toString(16).padStart(2, '0')).join('');
const nonce = () => hex(crypto.getRandomValues(new Uint8Array(32)));
const keys = (value, expected) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === expected;
export const isPairCode = value => value?.url === PAIR_URL;
const legacyValidPair = p => keys(p, 'account_hash,enabled,token,url') && isPairCode(p) && hashShape(p.account_hash) &&
  typeof p.enabled === 'boolean' && /^[A-Za-z0-9_-]{32,128}$/.test(p.token);

export async function pairProof(token, parts, prefix = PREFIX) {
  const key = await crypto.subtle.importKey('raw', enc.encode(token), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, enc.encode([prefix, ...parts].join('\n'))));
}
function sameProof(a, b) {
  if (!hashShape(a) || !hashShape(b)) return false;
  let different = 0;
  for (let i = 0; i < 64; i++) different |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return different === 0;
}
async function exchange(path, body, pairUrl = PAIR_URL) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2500);
  try {
    // The durable key is never transmitted, including in Authorization headers.
    const response = await fetch(pairUrl + path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), credentials: 'omit', redirect: 'error', cache: 'no-store', signal: controller.signal });
    if (!response.ok) { let payload; try { payload = await response.json(); } catch {} throw new Error(failureCode(response.status, payload?.error)); }
    return await response.json();
  } finally { clearTimeout(timer); }
}
export async function resumeHandshake(pair, account_hash, current = () => true, options = {}) {
  const pairUrl = options.pairUrl || PAIR_URL, prefix = options.prefix || PREFIX;
  const sign = parts => pairProof(pair.token, parts, prefix);
  if (pair?.url !== pairUrl || !hashShape(account_hash)) throw new Error('invalid_pair');
  const client_nonce = nonce();
  const hello = await exchange('/hello', { client_nonce, account_hash }, pairUrl);
  if (!current()) throw new Error('cancelled');
  if (!keys(hello, 'proof,server_nonce') || !hashShape(hello.server_nonce) ||
      !sameProof(hello.proof, await sign(['server', client_nonce, hello.server_nonce, account_hash]))) throw new Error('untrusted_server');
  if (!current()) throw new Error('cancelled');
  const proof = await sign(['client', client_nonce, hello.server_nonce, account_hash]);
  if (!current()) throw new Error('cancelled');
  const result = await exchange('/resume', { client_nonce, server_nonce: hello.server_nonce, account_hash, proof }, pairUrl);
  if (!current()) throw new Error('cancelled');
  if (!keys(result, 'connection,proof')) throw new Error('untrusted_session');
  const connection = connectionFrom(JSON.stringify(result.connection));
  if (connection.url === pairUrl || connection.token === pair.token ||
      !sameProof(result.proof, await sign(['session', client_nonce, hello.server_nonce, account_hash, connection.url, connection.token]))) throw new Error('untrusted_session');
  if (!current()) throw new Error('cancelled');
  return connection;
}

export function createReconnect({ readAccount, connected, clearSession, status, validateConnection = async () => {}, options = {} }) {
  const message = options.purpose === 'messages';
  const pairUrl = message ? MESSAGE_PAIR_URL : PAIR_URL;
  const prefix = message ? 'worklead-message-pair-v1' : PREFIX;
  const storageKey = message ? 'messagePair' : 'autoPair';
  const tabKey = message ? 'messagePairTab' : 'autoPairTab';
  const bridgeKey = message ? 'bridge' : 'autoBridge';
  const alarm = message ? MESSAGE_PAIR_ALARM : PAIR_ALARM;
  const label = message ? '메시지 감시' : '자동 지원';
  const exchangePair = (pair, account, current) => resumeHandshake(pair, account, current, { pairUrl, prefix });
  const validPair = p => {
    if (!message) return legacyValidPair(p);
    return keys(p, 'account_hash,capture_visible_chat,enabled,token,url') && p.url === pairUrl && hashShape(p.account_hash) &&
      typeof p.enabled === 'boolean' && typeof p.capture_visible_chat === 'boolean' && /^[A-Za-z0-9_-]{32,128}$/.test(p.token);
  };
  let epoch = 0, running = null, failures = 0, retryAt = 0, writes = Promise.resolve();
  let secured = null;
  const secure = () => secured ||= chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
  const mutate = (operation, captured = null) => {
    const result = writes.then(async () => { await secure(); if (captured === null || captured === epoch) await operation(); });
    writes = result.catch(() => {}); return result;
  };
  const remember = (pair, captured) => mutate(() => chrome.storage.local.set({ [storageKey]: pair }), captured);
  const saved = async () => { await secure(); const value = (await chrome.storage.local.get(storageKey))[storageKey]; return validPair(value) ? value : null; };
  const current = captured => captured === epoch;
  async function disarm({ forget = false, message = null } = {}) {
    epoch++; retryAt = 0; failures = 0;
    // Serialized credential writes make a disconnect win over a late handshake.
    await mutate(async () => {
      if (forget) await chrome.storage.local.remove(storageKey);
      else { const pair = await saved(); if (pair) await chrome.storage.local.set({ [storageKey]: { ...pair, enabled: false } }); }
    });
    await clearSession();
    await chrome.alarms.clear(alarm);
    if (message) await status(message);
  }
  async function initial(pair, tabId, extra = {}) {
    const captured = ++epoch, active = () => current(captured);
    const previous = await saved();
    const account = await readAccount(tabId);
    if (!active()) throw new Error('cancelled');
    if (account?.state !== 'account' || !hashShape(account.account_hash)) throw new Error(account?.reason_code === 'login_required' ? 'login_required' : 'identity_not_ready');
    const connection = await exchangePair(pair, account.account_hash, active);
    if (!active()) throw new Error('cancelled');
    await validateConnection(connection);
    if (!active()) throw new Error('cancelled');
    const remembered = { ...pair, account_hash: account.account_hash, enabled: true, ...(message ? { capture_visible_chat: extra.capture_visible_chat === true } : {}) };
    try {
      await remember(remembered, captured);
      if (!active()) throw new Error('cancelled');
      await chrome.storage.session.set({ [tabKey]: { tabId } });
      if (!active()) throw new Error('cancelled');
      await connected(connection, tabId, account.account_hash, active, remembered);
      if (!active()) throw new Error('cancelled');
    } catch (error) {
      if (active()) await mutate(() => previous ? chrome.storage.local.set({ [storageKey]: previous }) : chrome.storage.local.remove(storageKey), captured);
      throw error;
    }
    await chrome.alarms.create(alarm, { periodInMinutes: 0.5 });
    retryAt = 0; failures = 0;
    await status(label + ' 연결을 기억했습니다. Worklead·크롬 재시작 후 같은 계정으로 자동 연결합니다.');
    return { ok: true };
  }
  async function run({ manual = false, force = false } = {}) {
    if (running) {
      // The first /next can reject while the handshake callback is still settling.
      // Queue its forced refresh after the current flight instead of swallowing it.
      if (force) { const captured = epoch; return running.then(() => current(captured) ? run({ force: true }) : { ok: false }); }
      return running;
    }
    if (!manual && Date.now() < retryAt) return { ok: false };
    const captured = epoch, active = () => current(captured);
    running = (async () => {
      const pair = await saved();
      if (!pair || (!pair.enabled && !manual) || !active()) return { ok: false };
      if (!await chrome.permissions.contains({ origins: ['http://127.0.0.1/*', JOBS_PERMISSION] })) throw new Error('permission_required');
      const session = await chrome.storage.session.get(bridgeKey);
      if (session[bridgeKey]?.ready && !force && !manual) return { ok: true };
      let savedTab = (await chrome.storage.session.get(tabKey))[tabKey];
      let tab = null;
      if (savedTab && Number.isInteger(savedTab.tabId)) {
        try { tab = await chrome.tabs.get(savedTab.tabId); } catch {}
      }
      if (!active()) return { ok: false };
      if (tab && !isJobPage(tab.url)) {
        await disarm({ message: `${label} 전용 탭이 다른 사이트로 이동해 일시 중지했습니다. 당근 탭으로 돌아온 뒤 저장한 연결 재개를 누르세요.` });
        return { ok: false };
      }
      if (!tab) {
        // Session IDs disappear on browser exit. Never adopt an arbitrary jobs tab.
        tab = await chrome.tabs.create({ url: 'https://jobs.daangn.com/', active: false });
        if (!active()) { try { await chrome.tabs.remove(tab.id); } catch {} return { ok: false }; }
        if (!Number.isInteger(tab.id)) throw new Error('tab_unavailable');
        savedTab = { tabId: tab.id };
        await chrome.storage.session.set({ [tabKey]: savedTab });
      }
      let account;
      // A newly created page may still be loading. Retry reads only, never clicks.
      for (let i = 0; i < 6 && active(); i++) {
        try { account = await readAccount(tab.id); } catch {}
        if (account?.state === 'account' && hashShape(account.account_hash)) break;
        const latest = await chrome.tabs.get(tab.id);
        if (latest.status === 'complete') break;
        await new Promise(resolve => setTimeout(resolve, 400));
      }
      if (!active()) return { ok: false };
      // DOM load completion does not mean the site's account menu has hydrated.
      // Keep the remembered grant and retry later; never infer logout from a blank page.
      if (account?.state !== 'account' && account?.reason_code !== 'login_required') throw new Error('identity_not_ready');
      if (account?.state !== 'account' || account.account_hash !== pair.account_hash) {
        await disarm({ message: account?.state === 'account' ? '당근 계정이 달라 자동 연결을 중지했습니다. 처음 연결한 계정으로 로그인한 뒤 저장한 연결 재개를 누르세요.' : '당근 로그인을 확인하지 못해 자동 연결을 중지했습니다. 전용 탭에 로그인한 뒤 저장한 연결 재개를 누르세요.' });
        return { ok: false };
      }
      const connection = await exchangePair(pair, account.account_hash, active);
      if (!active()) return { ok: false };
      await validateConnection(connection);
      if (!active()) return { ok: false };
      if (manual) await remember({ ...pair, enabled: true }, captured);
      if (!active()) return { ok: false };
      await connected(connection, tab.id, account.account_hash, active, pair);
      if (!active()) return { ok: false };
      await chrome.alarms.create(alarm, { periodInMinutes: 0.5 });
      failures = 0; retryAt = 0;
      await status('저장한 계정으로 ' + label + '를 다시 연결했습니다.');
      return { ok: true };
    })().catch(async error => {
      if (active()) {
        failures = Math.min(failures + 1, 4);
        retryAt = Date.now() + Math.min(30000 * 2 ** (failures - 1), 240000);
        await chrome.alarms.create(alarm, { periodInMinutes: 0.5 });
        await status(connectionError(error, message ? 'messages' : 'auto_apply') + ' 저장한 연결로 다시 시도합니다.');
      }
      return { ok: false };
    }).finally(() => { running = null; });
    return running;
  }
  async function tabClosed(id, isWindowClosing) {
    const savedTab = (await chrome.storage.session.get(tabKey))[tabKey];
    if (savedTab?.tabId !== id) return false;
    if (isWindowClosing) {
      epoch++; retryAt = 0;
      await clearSession();
      await chrome.storage.session.remove(tabKey);
    } else await disarm({ message: `${label} 전용 탭을 닫아 일시 중지했습니다. 저장한 연결 재개를 누르면 새 전용 탭에서 이어갑니다.` });
    return true;
  }
  return { initial, resume: run, forget: () => disarm({ forget: true }), pause: message => disarm({ message }), tabClosed,
    async boot() { try { await secure(); await chrome.alarms.create(alarm, { periodInMinutes: 0.5 }); await run(); } catch {} } };
}
