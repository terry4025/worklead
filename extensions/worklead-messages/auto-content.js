// Isolated world, top frame only. No page scripts, cookies, storage or remote API.
(() => {
  if (location.origin !== 'https://jobs.daangn.com') return;
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden';
  const label = el => norm(el.getAttribute('aria-label') || el.textContent);
  const buttons = root => [...root.querySelectorAll('button, a[role="button"]')].filter(visible);
  const exact = (root, text) => buttons(root).filter(el => label(el) === text && !el.disabled && el.getAttribute('aria-disabled') !== 'true');
  const interactionSnapshot = () => {
    const main = document.querySelector('main');
    const menus = buttons(document).filter(el => /프로필 메뉴$/.test(label(el)));
    return JSON.stringify({
      url: location.href,
      account: menus.map(el => [label(el), el.querySelector('img')?.getAttribute('src') || '']),
      main: main?.innerHTML ?? '',
      values: [...(main?.querySelectorAll('textarea') || [])].map(el => el.value),
      controls: [...(main?.querySelectorAll('button,input,select,textarea,[role="checkbox"],[role="radio"]') || [])].map(el => [visible(el), !!el.disabled, el.getAttribute('aria-disabled')]),
    });
  };
  const digest = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))].map(x => x.toString(16).padStart(2, '0')).join('');
  const account = async () => {
    const menus = buttons(document).filter(el => /프로필 메뉴$/.test(label(el)));
    if (menus.length !== 1) return null;
    return digest(label(menus[0]) + '\n' + (menus[0].querySelector('img')?.getAttribute('src') || ''));
  };
  const id = () => {
    if (location.origin !== 'https://jobs.daangn.com' || location.search || location.hash) return null;
    try { return decodeURIComponent(location.pathname).match(/^\/job-posts\/(?:[^/?#]+-)?([A-Za-z0-9]{12})(?:\/apply)?\/?$/)?.[1] || null; } catch { return null; }
  };
  const panels = () => {
    const roots = new Set();
    for (const heading of document.querySelectorAll('h1,h2,h3,h4,p,span,div')) {
      if (!visible(heading) || norm(heading.textContent) !== '제출할 자기소개를 확인하세요') continue;
      let root = heading.parentElement;
      for (let i = 0; root && i < 7; i++, root = root.parentElement) {
        if (root === document.body) break;
        if ([...root.querySelectorAll('textarea')].filter(visible).length === 1 && exact(root, '지원하기').length === 1) { roots.add(root); break; }
      }
    }
    return [...roots].filter(root => ![...roots].some(other => other !== root && root.contains(other)));
  };
  async function inspect(expected = null) {
    const account_hash = await account();
    if (!account_hash) {
      const signedOut = [...document.querySelectorAll('button,a')].some(el => visible(el) && label(el) === '로그인');
      return { state: 'held', reason_code: signedOut ? 'login_required' : 'ui_unrecognized' };
    }
    if (!expected) return { state: 'account', account_hash };
    if (id() !== expected.job_id) return { state: 'held', reason_code: 'navigation_failed', account_hash };
    const main = document.querySelector('main');
    const title = main?.querySelector('h1');
    if (!main || !title || !visible(title) || norm(title.textContent) !== norm(expected.title)) return { state: 'held', reason_code: 'ui_unrecognized', account_hash };
    if (exact(main, '지원 취소').length === 1) return { state: 'already_applied', account_hash };
    if (buttons(main).some(el => /^(모집\s*마감|마감된 공고|지원 마감)$/.test(label(el)))) return { state: 'held', reason_code: 'closed', account_hash };
    const forms = panels();
    if (forms.length > 1) return { state: 'held', reason_code: 'ui_unrecognized', account_hash };
    if (forms.length === 1) {
      const panel = forms[0];
      const textareas = [...panel.querySelectorAll('textarea')].filter(visible);
      if (textareas.length !== 1 || !norm(textareas[0].value)) return { state: 'held', reason_code: 'ui_unrecognized', account_hash };
      const extra = [...panel.querySelectorAll('input:not([type="hidden"]),select,[contenteditable="true"],[role="checkbox"],[role="radio"]')].filter(visible);
      // Required questions may be siblings of the introduction card, not its children.
      const scope = panel.closest('form,[role="dialog"],dialog') || main;
      const outsideRequired = [...scope.querySelectorAll('input:not([type="hidden"]),select,[contenteditable="true"],[role="checkbox"],[role="radio"],textarea')].filter(el => visible(el) && !panel.contains(el));
      if (extra.length || outsideRequired.length || /추가\s*(질문|답변)|필수\s*(질문|입력)|약관|동의/.test(panel.textContent)) return { state: 'held', reason_code: 'questions_required', account_hash };
      // Only the saved default introduction. Never fill/edit any field.
      const selected = [...panel.querySelectorAll('[aria-selected="true"],[aria-pressed="true"],[data-state="active"]')].filter(visible);
      if (!selected.some(el => norm(el.textContent) === '기본')) return { state: 'held', reason_code: 'profile_changed', account_hash };
      if (!buttons(panel).some(el => label(el) === '기본')) return { state: 'held', reason_code: 'profile_changed', account_hash };
      return { state: 'ready', account_hash, profile_hash: await digest(norm(textareas[0].value)) };
    }
    const first = exact(main, '지원하기');
    return first.length === 1 ? { state: 'openable', account_hash } : { state: 'held', reason_code: 'ui_unrecognized', account_hash };
  }
  async function run(operation, expected) {
    const before = interactionSnapshot();
    const result = await inspect(expected);
    // Hashing awaits must not create a gap between the inspected DOM and the click.
    // This comparison and the final click run synchronously in the same JS turn.
    if (before !== interactionSnapshot()) return { state: 'held', reason_code: 'ui_unrecognized' };
    if (operation === 'inspect') return result;
    if (result.account_hash !== expected.account_hash) return { state: 'held', reason_code: 'account_changed' };
    if (operation === 'open' && result.state === 'openable') {
      exact(document.querySelector('main'), '지원하기')[0].click();
      return { state: 'opened' };
    }
    if (operation === 'submit' && result.state === 'ready' && result.profile_hash === expected.profile_hash && Date.now() <= expected.permit_until) {
      // Marker is private to this isolated document. Worker/backend provide durable at-most-once gates.
      const key = expected.attempt_id;
      const used = globalThis.__workleadAutoClicked || (globalThis.__workleadAutoClicked = new Set());
      if (used.has(key)) return { state: 'held', reason_code: 'submission_unconfirmed' };
      used.add(key);
      exact(panels()[0], '지원하기')[0].click();
      return { state: 'clicked' };
    }
    return { state: 'held', reason_code: result.reason_code || 'ui_unrecognized' };
  }
  globalThis.__workleadAutoUi = run;
  if (!globalThis.__workleadManagedDriver && !globalThis.__workleadAutoTicker) {
    globalThis.__workleadAutoTicker = setInterval(() => chrome.runtime.sendMessage({ type: 'auto-tick' }).catch(() => {}), 2000);
  }
})();
