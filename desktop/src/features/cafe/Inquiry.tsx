import { useState } from 'react';
import type { Schemas } from '@contracts/index';
import type { CafePort } from '../../domain/cafe';
import { Button } from '../../ui/Button';

export function Inquiry({ item, port, run, busy, openLead, openLink }: { item: Schemas['CafeCommentOut']; port: CafePort; run: (action: () => Promise<unknown>) => void; busy: boolean; openLead?: (id: string) => void; openLink: (url: string) => void }) {
  const [memo, setMemo] = useState(item.memo);
  const [draft, setDraft] = useState<string | null>(null);
  const writtenLabel = item.written_raw
    ? `${item.written_raw} (작성 시각 추정)`
    : item.written_at ? new Date(item.written_at).toLocaleString('ko-KR') : '작성 시각 미확인';
  return <article className="cafe-inquiry"><div className="cafe-heading"><h2>{({ request:'작업 문의', question:'질문', other:'기타' })[item.classification]} · {item.read_at ? '읽음' : '안 읽음'}</h2><span>{item.cafe_name} · {item.is_reply ? '답글' : '댓글'}</span></div><p className="field-hint">{item.author_nickname} · {writtenLabel}</p><p className="cafe-inquiry-body">{item.body}</p><div className="cafe-actions"><Button onClick={() => { openLink(item.post_url); }}>원문 열기</Button><Button disabled={busy} onClick={() => run(() => port.updateComment(item.id, { read: !item.read_at }))}>{item.read_at ? '안 읽음으로 표시' : '읽음 처리'}</Button><Button disabled={busy} onClick={() => run(async () => { const id = item.lead_id ?? await port.commentLead(item.id); openLead?.(id); })}>{item.lead_id ? '연결된 리드 열기' : '리드로 만들기'}</Button><Button disabled={busy} onClick={() => run(async () => { setDraft(await port.commentDraft(item.id)); })}>답글 초안</Button></div><form className="cafe-inquiry-memo" onSubmit={(e) => { e.preventDefault(); run(() => port.updateComment(item.id, { memo })); }}><label htmlFor={`memo-${item.id}`}>문의 메모</label><textarea id={`memo-${item.id}`} rows={3} maxLength={4000} value={memo} onChange={(e) => setMemo(e.target.value)} /><Button type="submit" disabled={busy || memo === item.memo}>메모 저장</Button></form>{draft !== null ? <div className="cafe-draft"><label htmlFor={`draft-${item.id}`}>답글 초안 · 직접 수정 후 원문에서 답글을 작성하세요</label><textarea id={`draft-${item.id}`} rows={7} value={draft} onChange={(e) => setDraft(e.target.value)} /></div> : null}</article>;
}
