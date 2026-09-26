/**
 * 요소를 지정한 스크롤 컨테이너 안에서만 보이게 한다.
 * (scrollIntoView 는 overflow:hidden 인 바깥 레이아웃까지 밀어 올려 상단 막대를 가릴 수 있다)
 */
export function scrollWithin(el: HTMLElement | null, container: HTMLElement | null, mode: 'nearest' | 'center' = 'nearest', topInset = 0): void {
  if (!el || !container) return;
  const c = container.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  const top = r.top - c.top + container.scrollTop;
  const viewTop = container.scrollTop + topInset;
  const viewBottom = container.scrollTop + container.clientHeight;
  let next = container.scrollTop;
  if (mode === 'center') next = top - (container.clientHeight - r.height) / 2;
  else if (top < viewTop) next = top - topInset;
  else if (top + r.height > viewBottom) next = top + r.height - container.clientHeight;
  if (next !== container.scrollTop) container.scrollTo({ top: Math.max(0, next), behavior: mode === 'center' ? 'smooth' : 'auto' });
}
