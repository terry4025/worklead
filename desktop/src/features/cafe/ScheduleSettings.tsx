import { useState } from 'react';
import type { CafeSnapshot } from '../../domain/cafe';
import type { Schemas } from '@contracts/index';
import { Button } from '../../ui/Button';

const fields = [
  ['daily_post_limit', '하루 게시 한도', 1, 50],
  ['min_post_minutes', '게시 최소 간격 (분)', 30, 10080],
  ['collect_interval_minutes', '글 수집 주기 (분)', 30, 10080],
  ['comment_interval_minutes', '댓글 확인 주기 (분)', 30, 10080],
  ['comment_monitor_days', '게시 후 댓글 감시 기간 (일)', 1, 60],
] as const;
type Values = Pick<CafeSnapshot['settings'], typeof fields[number][0]>;

export function ScheduleSettings({ settings, busy, save }: { settings: CafeSnapshot['settings']; busy: boolean; save: (patch: Schemas['CafeSettingsPatch']) => void }) {
  const [values, setValues] = useState<Values>(() => Object.fromEntries(fields.map(([id]) => [id, settings[id]])) as Values);
  return <form className="cafe-settings cafe-schedule" onSubmit={(e) => { e.preventDefault(); save(values); }}>
    <h2>게시 한도와 확인 주기</h2>
    <p className="field-hint">지원 경로 확보 전에는 예약을 실행하지 않습니다. 이 값은 이후 사용할 설정으로 저장됩니다.</p>
    {fields.map(([id, label, min, max]) => <label key={id}>{label}<input type="number" required min={min} max={max} value={values[id]} onChange={(e) => setValues({ ...values, [id]: Number(e.target.value) })} /></label>)}
    <Button type="submit" variant="primary" disabled={busy}>주기·게시 한도 저장</Button>
  </form>;
}
