import { connectionFrom } from './protocol.js';
const code = document.getElementById('code');
const status = document.getElementById('status');
const connect = document.getElementById('connect');

async function refresh() {
  const { connectionStatus } = await chrome.storage.session.get('connectionStatus');
  status.textContent = connectionStatus?.message || '연결하지 않았습니다.';
}

connect.addEventListener('click', async () => {
  try {
    connectionFrom(code.value);
    // Permission grant is a user gesture in Chrome, never granted by Worklead.
    if (!await chrome.permissions.request({ origins: ['http://127.0.0.1/*'] })) {
      status.textContent = '연결 권한을 허용하지 않아 감시를 시작하지 않았습니다.';
      return;
    }
    connect.disabled = true;
    const result = await chrome.runtime.sendMessage({ type: 'connect', code: code.value });
    code.value = '';  // Do not leave the local key in the popup DOM.
    if (!result?.ok) throw new Error(result?.message || '연결하지 못했습니다.');
    await refresh();
  } catch (error) {
    status.textContent = error.message;
  } finally { connect.disabled = false; }
});

document.getElementById('disconnect').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ type: 'disconnect' });
  code.value = '';
  await refresh();
});
chrome.storage.onChanged.addListener((_, area) => { if (area === 'session') void refresh(); });
void refresh();
