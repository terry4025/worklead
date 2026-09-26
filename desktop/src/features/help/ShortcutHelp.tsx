import { Dialog } from '../../ui/Dialog';

const KEYS: [string, string][] = [
  ['↑ ↓ / J K', '리드 이동'],
  ['Enter', '상세로 이동 (좁은 창에서는 열기)'],
  ['Esc', '상세 닫기 · 입력 벗어나기 · 창 닫기'],
  ['O', '원문 열기 (시스템 브라우저)'],
  ['S', '관심 표시 / 해제'],
  ['X', '제외 / 해제'],
  ['U', '방금 제외한 리드 되돌리기'],
  ['R', '모집 상태 재확인'],
  ['M', '메모 입력'],
  ['/', '검색'],
  ['1 – 5', '검토 · 관심 · 진행 중 · 제외 · 전체'],
  ['F5', '목록 새로고침'],
  ['C', '수집 제어'],
  [',', '설정'],
  ['?', '이 도움말'],
];

export function ShortcutHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="단축키" width="min(460px, 94vw)">
      <table className="keys">
        <tbody>
          {KEYS.map(([k, d]) => (
            <tr key={k}>
              <th scope="row">
                <kbd>{k}</kbd>
              </th>
              <td>{d}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="field-hint">입력칸에 있을 때는 단축키가 동작하지 않습니다.</p>
    </Dialog>
  );
}
