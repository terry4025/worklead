const labels = {
  invalid_code: 'Worklead에서 연결 코드를 다시 복사해 그대로 붙여 넣으세요.',
  wrong_code_purpose: '코드 종류가 다릅니다. 메시지 코드는 위쪽, 자동 지원 코드는 아래쪽에 넣으세요.',
  expired_code: '코드가 만료되었거나 연결이 해제되었습니다. Worklead에서 해당 연결을 준비한 뒤 새 코드를 복사하세요.',
  app_unavailable: '이 PC의 Worklead에 연결하지 못했습니다. 앱이 실행 중인지 확인하세요.',
  auto_apply_stopped: 'Worklead에서 자동 지원이 중지되어 있습니다. 앱에서 다시 시작하세요.',
  message_watch_stopped: 'Worklead에서 메시지 감시가 중지되어 있습니다. 앱에서 현재 크롬으로 연결을 다시 시작하세요.',
  account_changed: '처음 연결한 당근 계정과 달라 중지했습니다. 계정을 확인하세요.',
  login_required: '당근알바에 직접 로그인한 뒤 다시 연결하세요.',
  identity_not_ready: '당근 계정 화면이 아직 준비되지 않았습니다. 페이지 로딩 후 다시 연결하세요.',
  permission_required: '당근 탭과 이 PC의 Worklead 연결 권한을 허용해야 합니다.',
  separate_tab_required: '메시지 감시와 자동 지원은 별도 당근 탭에서 연결하세요.',
  jobs_tab_required: '로그인한 당근알바 탭을 선택한 뒤 확장을 여세요.',
  wrong_tab: '당근알바 탭을 선택한 뒤 확장을 여세요.',
  popup_required: 'Worklead 확장 팝업에서 연결하세요.',
  untrusted_server: 'Worklead 연결 확인이 일치하지 않습니다. 해당 연결의 새 코드를 복사하세요.',
  untrusted_session: 'Worklead 실행 연결을 확인하지 못했습니다. 앱에서 연결을 다시 준비하세요.',
  cancelled: '연결 작업이 취소되었습니다.', busy: '이전 작업을 마친 뒤 다시 연결하세요.',
  pairing_busy: '연결 확인이 많아 잠시 대기 중입니다. 잠시 후 다시 시도하세요.',
  capture_not_enabled: '보이는 채팅 표시는 Worklead와 확장 양쪽에서 허용해야 합니다.',
  browser_timeout: '당근 탭 응답이 늦습니다. 탭이 정상으로 열렸는지 확인하세요.',
};
export function failureCode(status, serverCode) {
  const code = typeof serverCode === 'string' ? serverCode : serverCode?.code;
  if (['auto_apply_stopped','message_watch_stopped','account_changed','login_required','pairing_busy'].includes(code)) return code;
  if (status === 401) return 'expired_code';
  if (status === 404 || status === 405) return 'wrong_code_purpose';
  if (status === 403) return 'permission_required';
  return 'app_unavailable';
}
export function connectionError(error, purpose = 'messages') {
  const code = typeof error === 'string' ? error : error?.message;
  return labels[code] || (purpose === 'messages' ? '메시지 감시 연결을 확인하지 못했습니다. Worklead와 로그인한 당근 탭을 확인하세요.' : '자동 지원 연결을 확인하지 못했습니다. Worklead와 로그인한 별도 당근 탭을 확인하세요.');
}
export async function ping(connection, purpose) {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch(connection.url + '/ping', { method: 'POST', headers: { 'Content-Type':'application/json', Authorization:'Bearer '+connection.token }, body:'{}', credentials:'omit', redirect:'error', cache:'no-store', signal:controller.signal });
    let body; try { body = await response.json(); } catch {}
    if (!response.ok) throw new Error(failureCode(response.status, body?.error));
    if (!body || Object.keys(body).sort().join(',') !== 'purpose,version' || body.purpose !== purpose || body.version !== 1) throw new Error('wrong_code_purpose');
  } catch (error) { if (labels[error?.message]) throw error; throw new Error('app_unavailable'); }
  finally { clearTimeout(timer); }
}
