import { connectionFrom } from './protocol.js';
import { JOBS_PERMISSION } from './auto-protocol.js';
const code = document.getElementById('code');
const status = document.getElementById('status');
const connect = document.getElementById('connect');

async function refresh() {
  const { connectionStatus } = await chrome.storage.session.get('connectionStatus');
  status.textContent = connectionStatus?.message || '연결하지 않았습니다.';
  const { autoStatus } = await chrome.storage.session.get('autoStatus');
  document.getElementById('auto-status').textContent = autoStatus?.message || '자동 지원을 연결하지 않았습니다.';
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

const autoCode = document.getElementById('auto-code');
const autoConsent = document.getElementById('auto-consent');
const autoConnect = document.getElementById('auto-connect');
const autoStatus = document.getElementById('auto-status');
autoConnect.addEventListener('click', async () => {
  try {
    if (!autoConsent.checked) { autoStatus.textContent = '저장된 기본 자기소개의 자동 제출 범위를 확인해 주세요.'; return; }
    connectionFrom(autoCode.value);
    if (!await chrome.permissions.request({ origins: ['http://127.0.0.1/*', JOBS_PERMISSION] })) { autoStatus.textContent = '당근 탭·이 PC 연결 권한을 허용하지 않아 시작하지 않았습니다.'; return; }
    autoConnect.disabled = true;
    const result = await chrome.runtime.sendMessage({ type: 'auto-connect', code: autoCode.value, confirm: true });
    autoCode.value = '';
    autoConsent.checked = false;
    if (!result?.ok) throw new Error(result?.message || '자동 지원을 연결하지 못했습니다.');
    await refresh();
  } catch { autoStatus.textContent = '별도 당근 탭·권한·자동 지원 연결 코드를 확인하세요.'; }
  finally { autoConnect.disabled = false; }
});
document.getElementById('auto-disconnect').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ type: 'auto-disconnect' });
  autoCode.value = ''; autoConsent.checked = false;
  await refresh();
});
