/** 당근 지원 준비. 실제 지원 제출과 저장된 초안 준비를 구분한다. */
export interface ApplicationEntry {
  lead_id: string;
  title: string;
  original_url: string | null;
  recommendation: 'recommended' | 'needs_review' | 'excluded';
  published_at: string | null;
  last_checked_at: string | null;
  candidate: boolean;
  hold_reasons: { code: string; message: string }[];
  draft: { text: string; generated_at: string | null; edited_by_user: boolean } | null;
  can_prepare: boolean;
  can_submit: false;
  mode: 'draft_only';
  submission_supported: false;
  questions: string[];
}

export interface ApplicationList {
  mode: 'draft_only';
  submission_supported: false;
  items: ApplicationEntry[];
  total: number;
}

export interface JobsPort {
  autoApplyStatus(): Promise<AutoApplyStatus>;
  autoApplyStart(body: { confirm: true; min_score: number; daily_limit: number }): Promise<AutoApplyStart>;
  autoApplyStop(): Promise<AutoApplyStatus>;
  applications(limit?: number): Promise<ApplicationList>;
  application(leadId: string): Promise<ApplicationEntry>;
  prepare(leadId: string): Promise<ApplicationEntry>;
  saveDraft(leadId: string, text: string, expectedText: string | null): Promise<ApplicationEntry>;
  messagesStatus(): Promise<MessageWatchStatus>;
  messagesStart(): Promise<MessageWatchStatus>;
  messagesStop(): Promise<MessageWatchStatus>;
  messagesChromeStart(): Promise<ChromeMessageConnection>;
}

export type { JobAutoApplyStatus as AutoApplyStatus, JobAutoApplyStart as AutoApplyStart, JobAutoApplyAttempt as AutoApplyAttempt } from '@contracts/index';
import type { JobAutoApplyStatus as AutoApplyStatus, JobAutoApplyStart as AutoApplyStart } from '@contracts/index';

export interface MessageWatchStatus {
  provider?: 'chrome' | 'edge';
  enabled: boolean;
  state: 'stopped' | 'starting' | 'login_required' | 'watching' | 'surface_unrecognized' | 'error';
  unread_count: number | null;
  last_observed_at: string | null;
  last_increase_at: string | null;
  error_code: string | null;
  message: string;
  poll_interval_seconds: number;
  observation: 'visible_unread_badge';
  captures_message_body: false;
  browser_notifications: 'default' | 'granted' | 'denied' | 'unsupported' | 'unknown';
  browser_open: boolean;
  needs_user_action: boolean;
}

export interface ChromeMessageConnection {
  connection: { url: string; token: string };
  status: MessageWatchStatus;
  extension_directory: string;
}
