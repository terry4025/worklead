import type { Schemas } from '@contracts/index';

export type CafeSnapshot = Schemas['CafeSnapshot'];
export type CafeEntry = Schemas['CafeEntry'];
export type CafeJob = Schemas['CafeJob'];
export interface CafePort {
  comments(): Promise<Schemas['CafeCommentList']>;
  updateComment(id: string, body: Schemas['CafeCommentPatch']): Promise<Schemas['CafeCommentOut']>;
  commentLead(id: string): Promise<string>;
  commentDraft(id: string): Promise<string>;
  template(): Promise<Schemas['CafeTemplate']>;
  updateTemplate(body: Schemas['CafeTemplatePatch']): Promise<Schemas['CafeTemplate']>;
  attempts(): Promise<Schemas['CafeAttempt'][]>;
  post(id: string): Promise<CafeJob>;
  repost(id: string): Promise<CafeJob>;
  status(): Promise<CafeSnapshot>;
  consent(version: string): Promise<unknown>;
  settings(patch: Schemas['CafeSettingsPatch']): Promise<Schemas['CafeSettings']>;
  list(): Promise<CafeEntry[]>;
  add(url: string): Promise<CafeEntry>;
  state(id: string, status: CafeEntry['status']): Promise<CafeEntry>;
  board(id: string, approved: boolean): Promise<Schemas['CafeBoardOut']>;
  jobs(): Promise<CafeJob[]>;
  action(kind: CafeJob['kind']): Promise<CafeJob>;
}
