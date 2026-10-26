import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createContext, runInContext } from 'node:vm';
import { connectionFrom, isJobPage, validSample } from '../worklead-messages/protocol.js';

const code = { url: 'http://127.0.0.1:43000', token: 'a'.repeat(43) };
test('connection permits exact loopback and a scoped code; rejects arbitrary destinations', () => {
  assert.deepEqual(connectionFrom(JSON.stringify(code)), code);
  for (const url of ['http://localhost:43000', 'http://127.0.0.1:80', 'https://127.0.0.1:43000', 'http://127.0.0.1:43000/api', 'http://127.0.0.1:43000/?token=secret', 'http://user@127.0.0.1:43000', 'http://127.0.0.1:43000/#secret', 'https://example.com', 'garbage']) {
    assert.throws(() => connectionFrom(JSON.stringify({ ...code, url })));
  }
  assert.throws(() => connectionFrom(JSON.stringify({ ...code, extra: 'not-allowed' })));
  assert.throws(() => connectionFrom(JSON.stringify({ ...code, token: 'short' })));
});

test('sample has only state and bounded integer; bodies, user IDs and ambiguous counts rejected', () => {
  assert.ok(validSample({ state: 'watching', unread_count: 0 }));
  assert.ok(validSample({ state: 'login_required', unread_count: null }));
  for (const value of [{ state: 'watching', unread_count: true }, { state: 'watching', unread_count: '1' }, { state: 'watching', unread_count: -1 }, { state: 'watching', unread_count: 1, body: 'private' }, { state: 'watching', unread_count: 1.5 }, { state: 'login_required', unread_count: 0 }]) assert.ok(!validSample(value));
  assert.ok(isJobPage('https://jobs.daangn.com/'));
  for (const url of ['https://jobs.daangn.com.example.org/', 'https://accounts.daangn.com/', 'http://jobs.daangn.com/']) assert.ok(!isJobPage(url));
});

test('content sampler reads labels only, never clicks, and cleans up before reinjection', async () => {
  const content = await readFile(new URL('../worklead-messages/content.js', import.meta.url), 'utf8');
  let buttons = [];
  const sent = [], listeners = new Set(), intervals = new Set();
  const button = (label, text = '') => ({ getClientRects: () => [1], getAttribute: () => label, textContent: text });
  const context = createContext({ location: { origin: 'https://jobs.daangn.com' }, document: { querySelectorAll(selector) { assert.equal(selector, 'button'); return buttons; } }, getComputedStyle: () => ({visibility:'visible',display:'block'}), chrome: { runtime: {sendMessage(message) { sent.push(message); return Promise.resolve(); },onMessage:{addListener:fn=>listeners.add(fn),removeListener:fn=>listeners.delete(fn)}} }, setInterval(fn) { intervals.add(fn); return fn; }, clearInterval(fn) { intervals.delete(fn); } });
  buttons = [button('채팅')]; runInContext(content, context);
  assert.equal(sent[0].sample.unread_count, 0);
  runInContext(content, context); assert.equal(intervals.size, 1); assert.equal(sent.length, 1);
  buttons = [button('채팅, 읽지 않은 메시지 4개')]; [...intervals][0](); assert.equal(sent.at(-1).sample.unread_count, 4);
  buttons = [button('채팅'), button('채팅')]; [...intervals][0](); assert.equal(sent.at(-1).sample.state, 'surface_unrecognized');
  buttons = [button(null, '로그인')]; [...intervals][0](); assert.equal(sent.at(-1).sample.state, 'login_required');
  [...listeners][0]({type:'stop-unread-sampler'}); assert.equal(intervals.size, 0); assert.equal(listeners.size, 0);
  buttons = [button('채팅')]; runInContext(content,context); assert.equal(intervals.size,1);
});

test('worker binds one selected top-level jobs tab; never forwards content-supplied URLs or secrets', async () => {
  const memory = {};
  let listeners = [], fetches = [], injected = [];
  globalThis.chrome = {
    runtime: {getURL:name=>'chrome-extension://test/'+name,onMessage:{addListener:fn=>{listeners.push(fn);}}},
    storage:{session:{async get(name) {return {[name]:memory[name]};},async set(values) {Object.assign(memory,values);},async remove(keys) {for(const key of keys)delete memory[key];}}},
    permissions:{contains:async()=>true},
    tabs:{query:async()=>[{id:7,url:'https://jobs.daangn.com/'}],get:async()=>({id:7,url:'https://jobs.daangn.com/'}),sendMessage:async()=>{},onRemoved:{addListener(){}}},
    scripting:{executeScript:async value=>{injected.push(value);return value.files?[{frameId:0}]:[{result:{state:'account',account_hash:'b'.repeat(64),sample:{state:'watching',unread_count:0}}}];}},
    alarms:{onAlarm:{addListener(){}},clear:async()=>true,create:async()=>{}},
  };
  const originalFetch=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{fetches.push({url,options});return {ok:true,json:async()=>new URL(url).pathname==='/ping'?{purpose:'messages',version:1}:{accepted:true}};};
  await import('../worklead-messages/worker.js');
  const dispatch=(message,sender)=>new Promise(resolve=>{if(!listeners.some(listener=>listener(message,sender,resolve)===true))resolve(undefined);});
  try {
    const popup={url:'chrome-extension://test/popup.html'};
    assert.ok((await dispatch({type:'connect',code:JSON.stringify(code)},popup)).ok);
    assert.deepEqual(injected[0].target,{tabId:7,allFrames:false});
    assert.deepEqual(fetches.map(x=>new URL(x.url).pathname),['/ping','/observe']);
    const message={type:'unread-sample',sample:{state:'watching',unread_count:2}};
    for(const sender of [{tab:{id:8},frameId:0,url:'https://jobs.daangn.com/'},{tab:{id:7},frameId:1,url:'https://jobs.daangn.com/'},{tab:{id:7},frameId:0,url:'https://example.org/'}]) assert.equal((await dispatch(message,sender)).ok,false);
    const sender={tab:{id:7},frameId:0,url:'https://jobs.daangn.com/'};
    assert.equal((await dispatch({...message,sample:{...message.sample,url:'https://evil.invalid'}},sender)).ok,false);
    assert.equal(fetches.length,2);
    await dispatch(message,sender); await dispatch(message,sender);
    assert.equal(fetches.length,4); const observations=fetches.filter(x=>x.url.endsWith('/observe')); assert.equal(observations[0].url,code.url+'/observe');
    assert.equal(JSON.parse(observations[2].options.body).sequence,3);
    assert.equal(observations[0].options.credentials,'omit');
    assert.deepEqual(Object.keys(JSON.parse(observations[0].options.body)).sort(),['account_hash','sequence','state','unread_count']);
    assert.ok(!JSON.stringify(JSON.parse(observations[0].options.body)).includes(code.token));
    await dispatch({type:'disconnect'},popup); assert.equal(memory.bridge,undefined);
  } finally {globalThis.fetch=originalFetch;delete globalThis.chrome;}
});
