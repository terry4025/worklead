import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { webcrypto } from 'node:crypto';
import { createContext, runInContext } from 'node:vm';
import { validChatSample } from '../worklead-messages/protocol.js';

const { JSDOM } = createRequire(new URL('../../desktop/package.json', import.meta.url))('jsdom');
const script = await readFile(new URL('../worklead-messages/chat-content.js',import.meta.url),'utf8');
async function waitFor(fn) { for(let i=0;i<50;i++){if(fn()) return;await new Promise(r=>setTimeout(r,5));}throw new Error('sample missing'); }
function harness() {
  const dom = new JSDOM('<header><button aria-label="합성 프로필 메뉴"><img src="/avatar.png"></button><button aria-label="채팅, 읽지 않은 메시지 1개" id="chat">채팅</button></header><main>공고 본문은 가져오지 않음</main><div role="dialog" aria-label="채팅"><p id="reply">합성 수신 답장입니다</p><textarea>보내지 않은 초안</textarea><input value="개인 입력"><button id="send">전송</button><a href="https://jobs.daangn.com/job-posts/test-123456789abc">공고 제목</a></div>',{url:'https://jobs.daangn.com/'});
  const w = dom.window, sent=[], intervals=[], listeners=[];
  w.HTMLElement.prototype.getClientRects=function(){return [{}];};
  const context=createContext({document:w.document,location:w.location,NodeFilter:w.NodeFilter,getComputedStyle:w.getComputedStyle.bind(w),crypto:webcrypto,TextEncoder,URL,
    MutationObserver:class {observe(){} disconnect(){}},setTimeout,clearTimeout,setInterval:fn=>{intervals.push(fn);return fn;},clearInterval(){},
    chrome:{runtime:{sendMessage:async message=>{sent.push(message);return {ok:true};},onMessage:{addListener:fn=>listeners.push(fn),removeListener(){}}}}});
  runInContext(script,context);
  return {w,context,sent,intervals,listeners};
}
test('selected visible dialog text updates without reload and excludes page body/drafts/controls',async()=>{
  const h=harness();await waitFor(()=>h.sent.length===1);
  let value=h.sent[0].sample;
  assert.equal(value.state,'visible');assert.equal(value.job_id,'123456789abc');assert.ok(validChatSample(value));
  assert.ok(value.text.includes('합성 수신 답장입니다'));
  for(const secret of ['공고 본문은','보내지 않은 초안','개인 입력','전송'])assert.ok(!value.text.includes(secret));
  h.w.document.querySelector('#reply').textContent='새 합성 답장';await h.intervals[0]();
  assert.ok(h.sent.at(-1).sample.text.includes('새 합성 답장'));
  h.w.document.querySelector('header button').remove();await h.intervals[0]();
  assert.equal(h.sent.at(-1).sample.state,'login_required');assert.equal(h.sent.at(-1).sample.text,null);
});
test('opening is limited to the chat list and never a conversation or send action',async()=>{
  const h=harness();await waitFor(()=>h.sent.length===1);
  let opened=0,sends=0;h.w.document.querySelector('#chat').onclick=()=>opened++;h.w.document.querySelector('#send').onclick=()=>sends++;
  h.context.__workleadOpenChatPanel();assert.equal(opened,0);assert.equal(sends,0);
  h.w.document.querySelector('[role="dialog"]').remove();
  h.context.__workleadOpenChatPanel();assert.equal(opened,1);assert.equal(sends,0);
  await h.intervals[0]();assert.equal(h.sent.at(-1).sample.state,'panel_closed');assert.equal(h.sent.at(-1).sample.text,null);
});
test('protocol is bounded and cannot smuggle remote URLs or numeric identity',()=>{
  const value={state:'visible',text:'합성',account_hash:'a'.repeat(64),job_id:null};
  assert.ok(validChatSample(value));
  for(const patch of [{text:'x'.repeat(4001)},{job_id:123456789012},{account_hash:null},{url:'https://evil.example'},{state:'panel_closed'}])assert.ok(!validChatSample({...value,...patch}));
});
