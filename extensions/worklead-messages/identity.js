// Top-frame visible account and unread labels only. No application/UI mutations.
(() => {
  if (location.origin !== 'https://jobs.daangn.com') return;
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden';
  const norm = text => (text || '').replace(/\s+/g,' ').trim();
  const label = el => el.getAttribute('aria-label') || norm(el.textContent);
  const buttons = () => [...document.querySelectorAll('button,a[role="button"]')].filter(visible);
  const stamp = () => buttons().filter(el=>/프로필 메뉴$/.test(label(el))).map(el=>label(el)+'\n'+(el.querySelector('img')?.getAttribute('src') || ''));
  globalThis.__workleadMessageInspect = async () => {
    const before = stamp();
    if (before.length !== 1) return {state:'held',reason_code:buttons().some(el=>label(el)==='로그인')?'login_required':'identity_not_ready'};
    const account_hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(before[0])))].map(x=>x.toString(16).padStart(2,'0')).join('');
    const after=stamp(); if(after.length!==1 || after[0]!==before[0])return {state:'held',reason_code:'identity_not_ready'};
    const badges=buttons().map(label).filter(text=>/^채팅(?:, 읽지 않은 메시지 [0-9]{1,7}개)?$/.test(text));
    const sample=badges.length===1?{state:'watching',unread_count:Number(badges[0].match(/[0-9]+/)?.[0]||0)}:{state:'surface_unrecognized',unread_count:null};
    return {state:'account',account_hash,sample};
  };
})();
