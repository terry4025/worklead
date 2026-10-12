import type { AnalysisView } from '../../domain/model';
import { intentLabel } from '../../domain/labels';

export function AiInterpretationPanel({ value }: { value: AnalysisView['aiInterpretation'] }) {
  if (!value) return null;
  return <section className="d-section" aria-label="AI 의도 제안">
    <h3 className="d-h">AI 의도 제안</h3>
    <p>제안 유형: <strong>{intentLabel[value.suggestedIntent]}</strong></p>
    <p className="field-hint">AI 보조 해석입니다. 규칙 기반 보수·적격성·점수를 바꾸지 않으며, 아래 원문 근거를 직접 확인하세요.</p>
    {value.evidenceQuotes.length ? <><h4>원문 근거</h4><ul className="check-list">{value.evidenceQuotes.map((quote, index) => <li key={index}><q>{quote}</q></li>)}</ul></> : <p className="field-hint">유형 제안을 뒷받침할 원문 인용이 없습니다.</p>}
    {value.uncertain.length ? <><h4>AI가 확인하지 못한 점</h4><ul className="check-list">{value.uncertain.map((text, index) => <li key={index}>{text}</li>)}</ul></> : null}
    {value.questions.length ? <><h4>확인 질문</h4><ul className="check-list">{value.questions.map((text, index) => <li key={index}>{text}</li>)}</ul></> : null}
  </section>;
}
