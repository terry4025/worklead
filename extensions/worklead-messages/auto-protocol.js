export const AUTO_ALARM = 'worklead-auto-apply';
export const JOBS_PERMISSION = 'https://jobs.daangn.com/*';
export const hashShape = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
export function jobId(raw) {
  try {
    const url = new URL(raw);
    if (url.origin !== 'https://jobs.daangn.com' || url.username || url.password || url.search || url.hash) return null;
    return decodeURIComponent(url.pathname).match(/^\/job-posts\/(?:[^/?#]+-)?([A-Za-z0-9]{12})(?:\/apply)?\/?$/)?.[1] ?? null;
  } catch { return null; }
}
export function validJob(job) {
  return job && Object.keys(job).sort().join(',') === 'attempt_id,lead_id,lease_token,title,url' &&
    typeof job.attempt_id === 'string' && job.attempt_id.length <= 40 && typeof job.lead_id === 'string' && job.lead_id.length <= 80 &&
    typeof job.title === 'string' && job.title.length > 0 && job.title.length <= 2000 && jobId(job.url) !== null &&
    /^[A-Za-z0-9_-]{32,128}$/.test(job.lease_token);
}
