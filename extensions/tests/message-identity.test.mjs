import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {createContext, runInContext} from 'node:vm';
import {webcrypto} from 'node:crypto';
const {JSDOM}=createRequire(new URL('../../desktop/package.json',import.meta.url))('jsdom');
const source=await readFile(new URL('../worklead-messages/identity.js',import.meta.url),'utf8');

function fixture(html, crypto=webcrypto) {
  const dom=new JSDOM(html,{url:'https://jobs.daangn.com/'}),w=dom.window;
  w.HTMLElement.prototype.getClientRects=function(){return this.hidden?[]:[{}];};
  let clicks=0;w.document.addEventListener('click',()=>clicks++);
  const context=createContext({document:w.document,location:w.location,getComputedStyle:w.getComputedStyle.bind(w),crypto,TextEncoder,Uint8Array});
  runInContext(source,context);
  return {inspect:()=>context.__workleadMessageInspect(),document:w.document,clicks:()=>clicks,close:()=>w.close()};
}
const profile='<button aria-label="시험 계정 프로필 메뉴"><img src="https://example.invalid/avatar.png"></button>';

test('message identity reads visible account and unread labels without clicks or background actions',async()=>{
  const h=fixture(profile+'<button aria-label="채팅, 읽지 않은 메시지 3개"></button><button>지원하기</button>');
  try{
    const value=await h.inspect();
    assert.equal(value.state,'account');assert.equal(value.sample.state,'watching');assert.equal(value.sample.unread_count,3);
    const expected=Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode('시험 계정 프로필 메뉴\nhttps://example.invalid/avatar.png'))).toString('hex');
    assert.equal(value.account_hash,expected);assert.equal((await h.inspect()).account_hash,expected);assert.equal(h.clicks(),0);
  }finally{h.close();}
});

test('message identity distinguishes loading, explicit login and an unrecognized unread surface',async()=>{
  const h=fixture('');try{
    assert.equal((await h.inspect()).reason_code,'identity_not_ready');
    h.document.body.innerHTML='<button>로그인</button>';
    assert.equal((await h.inspect()).reason_code,'login_required');
    h.document.body.innerHTML=profile;
    const value=await h.inspect();assert.equal(value.state,'account');assert.equal(value.sample.state,'surface_unrecognized');assert.equal(value.sample.unread_count,null);
  }finally{h.close();}
});

test('ambiguous visible identities are rejected while hidden duplicates do not change the account',async()=>{
  const h=fixture(profile+profile+'<button aria-label="채팅"></button>');try{
    assert.equal((await h.inspect()).reason_code,'identity_not_ready');
    h.document.querySelectorAll('button')[1].hidden=true;
    assert.equal((await h.inspect()).sample.unread_count,0);
  }finally{h.close();}
});

test('account changes during asynchronous hashing cannot produce an accepted mixed observation',async()=>{
  let h;
  h=fixture(profile+'<button aria-label="채팅"></button>',{subtle:{digest:async(...args)=>{
    h.document.querySelector('button').setAttribute('aria-label','다른 계정 프로필 메뉴');
    return webcrypto.subtle.digest(...args);
  }}});
  try{assert.equal((await h.inspect()).reason_code,'identity_not_ready');assert.equal(h.clicks(),0);}finally{h.close();}
});
