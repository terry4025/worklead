import { connectionFrom, isJobPage, validSample, validChatSample } from './protocol.js';
import './auto-worker.js';

// session storage is available to trusted extension contexts only by default.
// Never put this scoped local key in page messages, localStorage or sync storage.
let work = Promise.resolve();
const serialized = operation => { const result = work.then(operation); work = result.catch(() => {}); return result; };

async function disconnect() {
  const { bridge } = await chrome.storage.session.get('bridge');
  await chrome.storage.session.remove(['bridge', 'connectionStatus']);
  if (bridge) {
    try { await chrome.tabs.sendMessage(bridge.tabId, { type: 'stop-unread-sampler' }); } catch {}
  }
}

async function configure(message, sender) {
  if (sender.tab || sender.url !== chrome.runtime.getURL('popup.html')) throw new Error('확장 메뉴에서 연결하세요.');
  const config = connectionFrom(message.code);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !Number.isInteger(tab.id) || !isJobPage(tab.url)) throw new Error('당근알바 탭을 선택한 뒤 연결하세요.');
  if ((await chrome.storage.session.get('autoBridge')).autoBridge?.tabId === tab.id) throw new Error('자동 지원과 메시지 감시는 별도 당근 탭을 사용하세요.');
  if (!await chrome.permissions.contains({ origins: ['http://127.0.0.1/*'] })) throw new Error('이 PC의 Worklead 연결 권한을 허용하세요.');
  await disconnect();
  await chrome.storage.session.set({ bridge: { ...config, tabId: tab.id, sequence: 0, chatSequence: 0, captureChat: message.capture_visible_chat === true }, connectionStatus: { state: 'waiting', message: '선택한 당근 탭의 표시를 기다리고 있습니다.' } });
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: false }, files: ['content.js'], world: 'ISOLATED' });
    if (message.capture_visible_chat === true) {
      await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: false }, files: ['chat-content.js'], world: 'ISOLATED' });
      await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: false }, world: 'ISOLATED', func: () => globalThis.__workleadOpenChatPanel?.() });
    }
  } catch {
    await disconnect();
    throw new Error('선택한 당근 탭을 읽지 못했습니다. 탭에서 확장을 다시 실행하세요.');
  }
  return { ok: true };
}

async function forward(message, sender) {
  const { bridge } = await chrome.storage.session.get('bridge');
  if (!bridge || sender.tab?.id !== bridge.tabId || sender.frameId !== 0 || !isJobPage(sender.url) || !validSample(message.sample)) return { ok: false };
  // Reserve sequence before HTTP; if a response is lost, never replay that sample.
  const sequence = bridge.sequence + 1;
  await chrome.storage.session.set({ bridge: { ...bridge, sequence } });
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch(bridge.url + '/observe', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + bridge.token },
      body: JSON.stringify({ ...message.sample, sequence }), credentials: 'omit', redirect: 'error', cache: 'no-store', signal: controller.signal,
    });
    if (!response.ok) throw new Error('not_accepted');
    await chrome.storage.session.set({ connectionStatus: { state: 'connected', message: 'Worklead에 표시 상태를 전달했습니다.', observedAt: Date.now() } });
    return { ok: true };
  } catch {
    await chrome.storage.session.set({ connectionStatus: { state: 'error', message: 'Worklead 연결을 확인하세요. 앱 재시작 후에는 새 연결 코드가 필요합니다.' } });
    return { ok: false };
  } finally { clearTimeout(timeout); }
}

async function forwardChat(message, sender) {
  const { bridge } = await chrome.storage.session.get('bridge');
  if (!bridge?.captureChat || sender.tab?.id !== bridge.tabId || sender.frameId !== 0 || !isJobPage(sender.url) || !validChatSample(message.sample)) return { ok: false };
  const sequence = bridge.chatSequence + 1;
  await chrome.storage.session.set({ bridge: { ...bridge, chatSequence: sequence } });
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch(bridge.url + '/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + bridge.token },
      body: JSON.stringify({ ...message.sample, sequence }), credentials: 'omit', redirect: 'error', cache: 'no-store', signal: controller.signal,
    });
    if (!response.ok) throw new Error('not_accepted');
    const result = await response.json();
    if (result.reconnect_required) { await disconnect(); return { ok: false }; }
    return { ok: true };
  } catch { return { ok: false }; }
  finally { clearTimeout(timeout); }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!message || typeof message.type !== 'string') return false;
  let operation;
  if (message.type === 'connect') operation = () => configure(message, sender);
  else if (message.type === 'unread-sample') operation = () => forward(message, sender);
  else if (message.type === 'chat-sample') operation = () => forwardChat(message, sender);
  else if (message.type === 'disconnect' && !sender.tab && sender.url === chrome.runtime.getURL('popup.html')) operation = async () => { await disconnect(); return { ok: true }; };
  else return false;
  serialized(operation).then(respond, () => respond({ ok: false, message: '연결을 완료하지 못했습니다. 당근 탭·권한·Worklead 연결 코드를 확인하세요.' }));
  return true;
});

chrome.tabs.onRemoved.addListener(tabId => serialized(async () => {
  const { bridge } = await chrome.storage.session.get('bridge');
  if (bridge?.tabId === tabId) await disconnect();
}));
