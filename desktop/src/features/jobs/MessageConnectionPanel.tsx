import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, Copy, Link, Square } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { usePortCtx } from '../../app/PortContext';
import { formatDateTime } from '../../domain/format';
import type { MessageWatchStatus } from '../../domain/jobs';
import { Button } from '../../ui/Button';
import { errorMessage } from '../../ui/States';
import { StatusMark } from '../../ui/StatusMark';
import { copyText } from '../../platform/external';

const key = ['jobs', 'messages', 'status'] as const;
const labels: Record<MessageWatchStatus['state'], string> = {
  stopped: '연결하지 않음', starting: '연결 중', login_required: '직접 로그인 필요',
  watching: '미읽음 표시 감시 중', surface_unrecognized: '메시지 표시 확인 필요', error: '연결 오류',
};

export function MessageConnectionPanel({ onSettings, now }: { onSettings: () => void; now: Date }) {
  const { port, mode } = usePortCtx();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: key, queryFn: () => port.jobs.messagesStatus(), refetchInterval: 2000 });
  // 연결 비밀은 DOM, URL, React Query, 로컬 저장소에 남기지 않는다.
  const connectionCode = useRef<string | null>(null);
  const [codeReady, setCodeReady] = useState(false);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [extensionDirectory, setExtensionDirectory] = useState<string | null>(null);
  const [folderCopied, setFolderCopied] = useState(false);
  const [captureChat, setCaptureChat] = useState(true);
  const [remember, setRemember] = useState(true);
  const mounted = useRef(false);
  const clearCode = () => { connectionCode.current = null; setCodeReady(false); setCopyState('idle'); setExtensionDirectory(null); setFolderCopied(false); };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; connectionCode.current = null; }; }, []);
  const chrome = useMutation({
    mutationFn: async () => {
      clearCode();
      await qc.cancelQueries({ queryKey: key, exact: true });
      qc.removeQueries({ queryKey: ['jobs', 'messages', 'inbox'] });
      const result = await port.jobs.messagesChromeStart(captureChat, remember);
      if (mounted.current) {
        connectionCode.current = result.connection ? JSON.stringify(result.connection) : null;
        setCodeReady(result.connection !== null);
        setExtensionDirectory(result.extension_directory);
      }
      // 연결 상태만 mutation 캐시에 반환한다. 비밀을 응답 데이터로 캐시하지 않는다.
      return result.status;
    },
    onSuccess: async (status) => { await qc.cancelQueries({ queryKey: key, exact: true }); qc.setQueryData(key, status); },
  });
  const action = useMutation({
    mutationFn: async (start: boolean) => { clearCode(); await qc.cancelQueries({ queryKey: key, exact: true }); return start ? port.jobs.messagesStart() : port.jobs.messagesStop(); },
    onSuccess: async (status) => { await qc.cancelQueries({ queryKey: key, exact: true }); qc.setQueryData(key, status); qc.removeQueries({ queryKey: ['jobs', 'messages', 'inbox'] }); },
  });
  const forget = useMutation({
    mutationFn: async () => { clearCode(); await qc.cancelQueries({ queryKey: key, exact: true }); return port.jobs.messagesChromeForget(); },
    onSuccess: async status => { await qc.cancelQueries({ queryKey: key, exact: true }); qc.setQueryData(key, status); qc.removeQueries({ queryKey: ['jobs', 'messages', 'inbox'] }); },
  });
  const status = q.data;
  useEffect(() => { if (status?.enabled && status.provider === 'chrome') { setRemember(Boolean(status.remembers_connection)); setCaptureChat(status.captures_message_body); } }, [status?.enabled, status?.remembers_connection, status?.captures_message_body, status?.provider]);
  useEffect(() => {
    if (status && (!status.enabled || status.provider === 'edge')) clearCode();
  }, [status?.enabled, status?.provider]);
  const watching = status?.state === 'watching' && !q.isError;
  return <section className="message-connection" aria-labelledby="message-heading">
    <h2 id="message-heading"><Bell size={17} aria-hidden="true" /> 당근 새 메시지</h2>
    <StatusMark tone={q.isError || action.isError || chrome.isError || forget.isError ? 'bad' : watching ? 'good' : 'warn'}>{q.isError ? '연결 상태 확인 실패' : status?.state === 'starting' && status.remembers_connection && status.paired ? '저장한 메시지 연결 복구 중' : status ? labels[status.state] : '연결 상태 확인 중'}</StatusMark>
    {status?.provider ? <p className="field-hint">연결 방식: {status.provider === 'chrome' ? '현재 크롬 탭 · 확장 연결' : '전용 Edge'}</p> : null}
    <p>{status?.message ?? '메시지 연결 여부를 아직 확인하지 못했습니다.'}</p>
    {status ? <dl className="message-status"><div><dt>미읽음 표시</dt><dd>{status.unread_count === null ? '미확인' : `${status.unread_count}개`}{!watching && status.last_observed_at ? ' · 마지막 관찰' : ''}</dd></div><div><dt>마지막 관찰</dt><dd>{status.last_observed_at ? formatDateTime(status.last_observed_at, now) : '관찰 기록 없음'}</dd></div><div><dt>최근 증가</dt><dd>{status.last_increase_at ? formatDateTime(status.last_increase_at, now) : '증가 관찰 기록 없음'}</dd></div></dl> : null}
    <div className="draft-actions">
      <Button size="sm" variant="primary" icon={<Link size={14} />} busy={chrome.isPending} disabled={!status || action.isPending || forget.isPending || mode === 'demo' || (status.enabled && status.provider !== 'chrome')} onClick={() => { action.reset(); forget.reset(); chrome.mutate(); }}>현재 크롬으로 연결</Button>
      <Button size="sm" icon={<Link size={14} />} busy={action.isPending} disabled={!status || chrome.isPending || forget.isPending || (status.enabled && (status.provider === 'chrome' || !['error', 'stopped'].includes(status.state)))} onClick={() => { chrome.reset(); action.mutate(true); }}>연결하고 감시 시작</Button>
      {status?.enabled ? <Button size="sm" icon={<Square size={13} />} disabled={action.isPending || chrome.isPending || forget.isPending} onClick={() => { chrome.reset(); forget.reset(); action.mutate(false); }}>감시 중지</Button> : null}
      {status?.remembers_connection ? <Button size="sm" busy={forget.isPending} disabled={chrome.isPending || action.isPending} onClick={() => { chrome.reset(); action.reset(); forget.mutate(); }}>저장한 메시지 연결 해제</Button> : null}
    </div>
    <label className="field-hint"><input type="checkbox" checked={remember} disabled={chrome.isPending || status?.enabled || forget.isPending} onChange={e => setRemember(e.target.checked)} /> 메시지 감시도 다음 실행부터 자동 연결</label>
    <p className="field-hint">최초 한 번 확장 0.6.1의 메시지 표시 감시에 연결하면 앱·크롬 재시작 뒤 같은 계정으로 복구합니다. 자동 지원 연결과는 별도이며, 감시 중지는 다음 실행에도 유지합니다.</p>
    <label className="field-hint"><input type="checkbox" checked={captureChat} disabled={chrome.isPending || status?.enabled} onChange={e => setCaptureChat(e.target.checked)} /> 보이는 채팅 내용도 Worklead에서 보기</label>
    <p className="field-hint">켜고 크롬으로 연결하면 선택한 탭에 보이는 채팅 내용을 이 PC에서 일시 표시합니다. 확장 팝업에서도 같은 항목을 선택하세요. 앱 종료·연결 중지·로그인 변경 시 내용을 지우며 파일·DB·AI로 보내지 않습니다.</p>
    {codeReady ? <div className="chrome-connection-code">
      <p className="field-hint">최초 한 번 당근 탭의 확장 팝업에서 메시지 표시 감시에 이 코드를 붙여 넣으세요. 자동 지원 입력란에는 넣지 마세요. 실제 미읽음 관찰이 확인돼야 연결 완료입니다.</p>
      <Button size="sm" icon={<Copy size={14} />} onClick={async () => { if (!connectionCode.current) return; setCopyState(await copyText(connectionCode.current) ? 'copied' : 'failed'); }}>메시지 연결코드 복사</Button>
      <p className={copyState === 'failed' ? 'field-error' : 'field-hint'} role="status">{copyState === 'copied' ? '연결코드를 복사했습니다. Worklead 확장 팝업에 붙여 넣으세요.' : copyState === 'failed' ? '연결코드를 복사하지 못했습니다. 다시 시도하세요.' : '코드는 화면에 표시하지 않습니다. 확장 연결에만 사용하세요.'}</p>
    </div> : null}
    <details className="chrome-connection-guide"><summary>크롬 확장 설치·연결 방법</summary>
      <ol><li>크롬 주소창에서 <code>chrome://extensions</code>를 열고 개발자 모드를 켭니다.</li><li>‘압축해제된 확장 프로그램을 로드합니다’에서 아래 폴더를 선택하고 설치 내용을 확인하세요.</li><li>로그인한 당근 알바 탭에서 Worklead 확장 팝업을 열고 복사한 연결코드를 붙여 넣습니다.</li></ol>
      {extensionDirectory ? <><p className="field-hint chrome-extension-path">{extensionDirectory}</p><Button size="sm" icon={<Copy size={14} />} onClick={async () => setFolderCopied(await copyText(extensionDirectory))}>확장 폴더 경로 복사</Button>{folderCopied ? <p className="field-hint" role="status">확장 폴더 경로를 복사했습니다.</p> : null}</> : <p className="field-hint">‘현재 크롬으로 연결’을 누르면 이 PC에 준비한 확장 폴더 경로를 표시합니다.</p>}
      <p className="field-hint">자동 연결을 기억하면 재시작 뒤 코드를 다시 입력하지 않습니다. 기억을 끈 임시 연결은 재시작 뒤 새 코드가 필요합니다. 연결 해제·계정 변경 시에는 다시 확인해야 합니다. 확장은 직접 설치·확인해 주세요.</p>
    </details>
    {chrome.isError ? <p className="field-error" role="alert">{errorMessage(chrome.error)}</p> : null}
    {q.isError || action.isError || forget.isError ? <div role="alert"><p className="field-error">{errorMessage(forget.error ?? action.error ?? q.error)}</p><Button size="sm" disabled={action.isPending} onClick={() => void q.refetch()}>연결 상태 다시 확인</Button></div> : null}
    <p className="field-hint">{mode === 'demo' ? '데모에서는 실제 메시지를 읽지 않습니다. ' : ''}{status?.provider === 'chrome' ? '크롬 확장은 직접 선택한 당근 탭만 관찰합니다. ' : '‘연결하고 감시 시작’은 전용 Edge 창을 엽니다. 그 창에서 직접 로그인하세요. '}채팅 내용은 영구 저장하지 않습니다.</p>
    <p className="field-hint">화면의 미읽음 수를 {status?.poll_interval_seconds ?? 2}초마다 확인하고 증가하면 알립니다. 처음 읽은 수는 기준값입니다. 수가 그대로인 새 메시지는 감지하지 못할 수 있으며, Worklead나 연결한 브라우저를 종료하면 감시가 중단됩니다.</p>
    <p className="field-hint">크롬 배경 탭·절전에서는 관찰과 알림이 지연되거나 일부 메시지를 놓칠 수 있습니다. 연결 공백 후 수가 늘면 재개 확인 알림을 보냅니다. Worklead를 종료하거나 연결한 탭을 닫으면 감시가 끊깁니다.</p>
    {status?.browser_notifications === 'denied' ? <p className="field-error">Edge 알림이 차단되어 있습니다. 브라우저에서 직접 알림 권한을 확인하세요.</p> : null}
    <Button size="sm" variant="ghost" icon={<Bell size={14} />} onClick={onSettings}>Windows 알림·조용한 시간 설정</Button>
    <p className="field-hint">Windows 알림 사용 여부와 방해 금지 설정에 따라 알림이 보이지 않을 수 있습니다. 실제 새 메시지 수신은 연결 후 확인해야 합니다.</p>
  </section>;
}
