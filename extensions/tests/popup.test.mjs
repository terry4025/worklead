import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {createContext,runInContext} from 'node:vm';
import {connectionFrom} from '../worklead-messages/protocol.js';
import {connectionError} from '../worklead-messages/connection-errors.js';
const {JSDOM}=createRequire(new URL('../../desktop/package.json',import.meta.url))('jsdom');
const html=await readFile(new URL('../worklead-messages/popup.html',import.meta.url),'utf8');
const script=(await readFile(new URL('../worklead-messages/popup.js',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn){for(let i=0;i<100;i++){if(fn())return;await wait(2);}throw new Error('popup timed out');}
const code=purpose=>JSON.stringify({url:'http://127.0.0.1:'+(purpose==='messages'?'58348':'58347'),token:'t'.repeat(43),purpose,version:1});
async function fixture() {
  const dom=new JSDOM(html,{url:'https://extension.invalid/popup.html'}),document=dom.window.document;
  const session={connectionStatus:{message:'이전 연결 상태'},autoStatus:{message:'이전 자동 지원 상태'}},local={},listeners=[],sent=[],permissions=[];
  let allowed=true,reply={ok:true},handler=null;
  const chrome={storage:{session:{get:async key=>({[key]:session[key]})},local:{get:async key=>({[key]:local[key]})},onChanged:{addListener:fn=>listeners.push(fn)}},
    permissions:{request:async value=>{permissions.push(value);return allowed;}},runtime:{getManifest:()=>({version:'0.5.0'}),sendMessage:async value=>{sent.push(value);return handler?handler(value):reply;}}};
  runInContext(script,createContext({document,chrome,connectionFrom,connectionError,JOBS_PERMISSION:'https://jobs.daangn.com/*'}));
  await until(()=>document.getElementById('status').textContent==='이전 연결 상태');
  return {document,session,local,sent,permissions,close:()=>dom.window.close(),reply:value=>reply=value,allowed:value=>allowed=value,handler:value=>handler=value,
    changed:async()=>{for(const fn of listeners)fn({},'session');await wait(5);}};
}

test('popup rejects wrong-purpose code before permission or worker dispatch and keeps the reason through storage refresh',async()=>{
  const h=await fixture();try{
    assert.equal(h.document.getElementById('extension-version').textContent,'연결 확장 0.5.0');
    h.document.getElementById('code').value=code('auto_apply');h.document.getElementById('connect').click();
    await until(()=>!h.document.getElementById('connect').disabled);
    assert.match(h.document.getElementById('status').textContent,/코드 종류/);assert.equal(h.sent.length,0);assert.equal(h.permissions.length,0);
    h.session.connectionStatus={message:'뒤늦은 연결 성공 상태'};await h.changed();assert.match(h.document.getElementById('status').textContent,/코드 종류/);
  }finally{h.close();}
});

test('popup keeps expired worker failure instead of replacing it with a stale successful storage value',async()=>{
  const h=await fixture();try{
    h.reply({ok:false,message:connectionError('expired_code')});h.document.getElementById('code').value=code('messages');h.document.getElementById('connect').click();
    await until(()=>!h.document.getElementById('connect').disabled);assert.match(h.document.getElementById('status').textContent,/만료/);
    assert.equal(h.document.getElementById('code').value,'');await h.changed();assert.match(h.document.getElementById('status').textContent,/만료/);
  }finally{h.close();}
});

test('popup permission denial is actionable and does not dispatch either channel',async()=>{
  const h=await fixture();try{
    h.allowed(false);h.document.getElementById('auto-code').value=code('auto_apply');h.document.getElementById('auto-consent').checked=true;h.document.getElementById('auto-connect').click();
    await until(()=>!h.document.getElementById('auto-connect').disabled);assert.match(h.document.getElementById('auto-status').textContent,/권한/);assert.equal(h.sent.length,0);
  }finally{h.close();}
});

test('popup separates capture and automatic application consent and never displays success while connect is pending',async()=>{
  const h=await fixture();let release;const gate=new Promise(resolve=>release=resolve);try{
    h.handler(()=>gate);h.document.getElementById('code').value=code('messages');h.document.getElementById('chat-consent').checked=true;h.document.getElementById('connect').click();
    await until(()=>h.sent.length===1);assert.equal(h.sent[0].type,'connect');assert.equal(h.sent[0].capture_visible_chat,true);assert.equal(h.sent[0].confirm,undefined);
    h.session.connectionStatus={message:'성공으로 오인할 오래된 상태'};await h.changed();assert.equal(h.document.getElementById('status').textContent,'이전 연결 상태');
    release({ok:false,message:connectionError('app_unavailable')});await until(()=>!h.document.getElementById('connect').disabled);assert.match(h.document.getElementById('status').textContent,/앱이 실행/);
    h.handler(null);h.document.getElementById('auto-code').value=code('auto_apply');h.document.getElementById('auto-consent').checked=true;h.document.getElementById('auto-connect').click();
    await until(()=>h.sent.length===2);assert.equal(h.sent[1].type,'auto-connect');assert.equal(h.sent[1].confirm,true);assert.equal(h.sent[1].capture_visible_chat,undefined);
  }finally{h.close();}
});
