export function connectionFrom(text) {
  if (typeof text !== 'string' || text.length > 1024) throw new Error('연결 코드 형식을 확인하세요.');
  let value;
  try { value = JSON.parse(text); } catch { throw new Error('Worklead에서 복사한 연결 코드를 붙여 넣으세요.'); }
  if (!value || Object.keys(value).sort().join(',') !== 'token,url' || typeof value.url !== 'string' || typeof value.token !== 'string') throw new Error('연결 코드 형식을 확인하세요.');
  let url;
  try { url = new URL(value.url); } catch { throw new Error('이 PC의 Worklead 연결 코드만 사용할 수 있습니다.'); }
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || Number(url.port) < 1024 || Number(url.port) > 65535 || url.username || url.password || url.pathname !== '/' || url.search || url.hash || !/^[A-Za-z0-9_-]{32,128}$/.test(value.token)) throw new Error('이 PC의 Worklead 연결 코드만 사용할 수 있습니다.');
  return { url: url.origin, token: value.token };
}

export function isJobPage(url) {
  try { return new URL(url).origin === 'https://jobs.daangn.com'; } catch { return false; }
}

export function validSample(value) {
  return value && Object.keys(value).sort().join(',') === 'state,unread_count' &&
    ['watching', 'login_required', 'surface_unrecognized'].includes(value.state) &&
    (value.state === 'watching' ? Number.isSafeInteger(value.unread_count) && value.unread_count >= 0 && value.unread_count <= 9999999 : value.unread_count === null);
}

export function validChatSample(v) {
  return v && Object.keys(v).sort().join(',') === 'account_hash,job_id,state,text' &&
    ['visible','panel_closed','surface_unrecognized','login_required'].includes(v.state) &&
    (v.state === 'visible' ? typeof v.text === 'string' && v.text.length > 0 && v.text.length <= 4000 : v.text === null) &&
    (v.state === 'login_required' ? v.account_hash === null : typeof v.account_hash === 'string' && /^[a-f0-9]{64}$/.test(v.account_hash)) &&
    (v.job_id === null || (v.state === 'visible' && typeof v.job_id === 'string' && /^[A-Za-z0-9]{12}$/.test(v.job_id)));
}
