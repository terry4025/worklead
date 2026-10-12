import { useState } from 'react';
import type { Schemas } from '@contracts/index';
import { Button } from '../../ui/Button';

export function TemplateEditor({ template, busy, save }: { template: Schemas['CafeTemplate']; busy: boolean; save: (body: Schemas['CafeTemplatePatch']) => void }) {
  const [title, setTitle] = useState(template.title);
  const [body, setBody] = useState(template.body);
  const dirty = title !== template.title || body !== template.body;
  return <section className="cafe-template"><form onSubmit={(e) => { e.preventDefault(); save({ title, body }); }}><h2>게시 템플릿 · 버전 {template.version}</h2><label htmlFor="cafe-template-title">제목</label><input id="cafe-template-title" maxLength={200} required value={title} onChange={(e) => setTitle(e.target.value)} /><label htmlFor="cafe-template-body">본문</label><textarea id="cafe-template-body" rows={16} maxLength={20000} required value={body} onChange={(e) => setBody(e.target.value)} /><div><Button variant="primary" type="submit" busy={busy} disabled={!dirty}>템플릿 저장</Button> <span role="status" className="field-hint">{dirty ? '저장하지 않은 변경' : '현재 저장된 템플릿'}</span></div></form><div className="cafe-template-preview"><h2>게시 미리보기</h2><h3>{title}</h3><p>{body}</p></div></section>;
}
