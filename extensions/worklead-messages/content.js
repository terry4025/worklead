(() => {
  // Isolated content world, selected tab only. Read DOM; never page JS/storage.
  if (globalThis.__workleadUnreadSampler) return;
  const visible = (element) => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden' && getComputedStyle(element).display !== 'none';
  const badge = /^채팅(?:, 읽지 않은 메시지 ([0-9]{1,7})개)?$/;
  function sample() {
    if (location.origin !== 'https://jobs.daangn.com') return { state: 'surface_unrecognized', unread_count: null };
    const buttons = [...document.querySelectorAll('button')].filter(visible);
    if (buttons.some(button => (button.getAttribute('aria-label') || button.textContent.trim()) === '로그인')) return { state: 'login_required', unread_count: null };
    const candidates = buttons.filter(button => badge.test(button.getAttribute('aria-label') || ''));
    if (candidates.length !== 1) return { state: 'surface_unrecognized', unread_count: null };
    const match = badge.exec(candidates[0].getAttribute('aria-label'));
    return { state: 'watching', unread_count: Number(match[1] || 0) };
  }
  const send = () => chrome.runtime.sendMessage({ type: 'unread-sample', sample: sample() }).catch(() => {});
  // No reload, click, private API call, notification grant or chat opening.
  send();
  const interval = setInterval(send, 2000);
  globalThis.__workleadUnreadSampler = true;
  function stop(message) {
    if (message?.type === 'stop-unread-sampler') {
      clearInterval(interval);
      delete globalThis.__workleadUnreadSampler;
      chrome.runtime.onMessage.removeListener(stop);
    }
  }
  chrome.runtime.onMessage.addListener(stop);
})();
