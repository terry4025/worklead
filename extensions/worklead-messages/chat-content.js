// Visible chat only, isolated world. No API, cookies, page state, or sending.
(() => {
  if (location.origin !== 'https://jobs.daangn.com' || globalThis.__workleadChatSampler) return;
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none' && el.getAttribute('aria-hidden') !== 'true';
  const norm = text => (text || '').replace(/\s+/g, ' ').trim();
  const panels = () => [...document.querySelectorAll('[role="dialog"][aria-label="채팅"]')].filter(visible);
  const menu = () => [...document.querySelectorAll('button')].filter(el => visible(el) && /프로필 메뉴$/.test(el.getAttribute('aria-label') || norm(el.textContent)));
  const stamp = () => menu().map(el => (el.getAttribute('aria-label') || norm(el.textContent)) + '\n' + (el.querySelector('img')?.getAttribute('src') || '')).join('\n');
  const hash = async text => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))].map(x => x.toString(16).padStart(2, '0')).join('');
  async function sample() {
    if (menu().length !== 1) return { state: 'login_required', text: null, account_hash: null, job_id: null };
    const before = stamp();
    const account_hash = await hash(before);
    if (before !== stamp()) return { state: 'login_required', text: null, account_hash: null, job_id: null };
    const roots = panels();
    if (roots.length !== 1) return { state: roots.length ? 'surface_unrecognized' : 'panel_closed', text: null, account_hash, job_id: null };
    const root = roots[0];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const lines = [];
    let node;
    while ((node = walker.nextNode()) && lines.length < 1500) {
      const el = node.parentElement;
      if (!visible(el) || el.closest('button,input,textarea,select,[contenteditable],script,style,[aria-hidden="true"]')) continue;
      const text = norm(node.textContent);
      if (text) lines.push(text);
    }
    let text = lines.join('\n');
    if (text.length > 4000) text = text.slice(0, 1900) + '\n… 중간 생략 …\n' + text.slice(-1900);
    const jobs = new Set([...root.querySelectorAll('a[href]')].filter(visible).map(el => {
      try { const u = new URL(el.href); return u.origin === 'https://jobs.daangn.com' && !u.search && !u.hash ? decodeURIComponent(u.pathname).match(/^\/job-posts\/(?:[^/?#]+-)?([A-Za-z0-9]{12})\/?$/)?.[1] : null; } catch { return null; }
    }).filter(Boolean));
    return text ? { state: 'visible', text, account_hash, job_id: jobs.size === 1 ? [...jobs][0] : null } : { state: 'surface_unrecognized', text: null, account_hash, job_id: null };
  }
  let busy = false, stopped = false, debounce;
  const send = async () => {
    if (busy || stopped) return;
    busy = true;
    try { const view = await sample(); if (!stopped) await chrome.runtime.sendMessage({ type: 'chat-sample', sample: view }); } catch {}
    finally { busy = false; }
  };
  // A connection gesture may open the chat LIST once. Never select a room,
  // press a send button, modify inputs, reload, or grant notification access.
  globalThis.__workleadOpenChatPanel = () => {
    if (panels().length) return;
    const badges = [...document.querySelectorAll('button')].filter(el => visible(el) && /^채팅(?:, 읽지 않은 메시지 [0-9]{1,7}개)?$/.test(el.getAttribute('aria-label') || ''));
    if (badges.length === 1) badges[0].click();
  };
  const interval = setInterval(send, 2000);
  const observer = new MutationObserver(() => { clearTimeout(debounce); debounce = setTimeout(send, 150); });
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  const stop = message => {
    if (message?.type !== 'stop-unread-sampler') return;
    stopped = true; clearInterval(interval); clearTimeout(debounce); observer.disconnect();
    delete globalThis.__workleadChatSampler; delete globalThis.__workleadOpenChatPanel;
    chrome.runtime.onMessage.removeListener(stop);
  };
  chrome.runtime.onMessage.addListener(stop);
  globalThis.__workleadChatSampler = true;
  void send();
})();
