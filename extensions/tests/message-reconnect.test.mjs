import {test} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {connectionFrom} from '../worklead-messages/protocol.js';
import {pairProof,MESSAGE_PAIR_URL,MESSAGE_PAIR_ALARM,createReconnect} from '../worklead-messages/reconnect.js';
import {connectionError} from '../worklead-messages/connection-errors.js';
const token='d'.repeat(43),account='a'.repeat(64),ephemeral={url:'http://127.0.0.1:44448',token:'m'.repeat(43)};
const code={url:MESSAGE_PAIR_URL,token,purpose:'messages',version:1};
const popup={url:'chrome-extension://fixture/popup.html'};
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let i=0;i<300;i++){if(fn())return;await wait(5);}throw new Error('fixture timed out');}
const store=memory=>({get:async key=>({[key]:memory[key]}),set:async data=>Object.assign(memory,data),remove:async keys=>{for(const k of [].concat(keys))delete memory[k];}});
async function fixture({saved=false,capture=false,ready=false,samplerGate=null}={}) {
  const local=saved?{messagePair:{url:MESSAGE_PAIR_URL,token,account_hash:account,enabled:true,capture_visible_chat:capture}}:{};
  const session=ready?{messagePairTab:{tabId:7},bridge:{...ephemeral,tabId:7,account_hash:account,ready:true,sequence:17,chatSequence:9,captureChat:capture}}:{};
  const requests=[],injected=[],created=[],listeners=[],updated=[],removed=[],startup=[],installed=[],alarms=[],scheduled=[],access=[];
  const alarmState=new Map();
  const tabs=new Map([[7,{id:7,url:'https://jobs.daangn.com/',status:'complete'}]]);
  let visible=account,pingStatus=200,observeStatus=200,observeError='unauthorized',nonce='b'.repeat(64),offline=false,observeGate=null,connection={...ephemeral};
  const original={chrome:globalThis.chrome,fetch:globalThis.fetch,crypto:globalThis.crypto};
  globalThis.crypto ||= webcrypto;
  globalThis.chrome={storage:{local:{...store(local),setAccessLevel:async value=>access.push(value)},session:store(session),onChanged:{addListener(){}}},permissions:{contains:async()=>true},
    runtime:{getURL:x=>'chrome-extension://fixture/'+x,onMessage:{addListener:fn=>listeners.push(fn)},onStartup:{addListener:fn=>startup.push(fn)},onInstalled:{addListener:fn=>installed.push(fn)}},
    tabs:{query:async()=>[tabs.get(7)],get:async id=>{if(!tabs.has(id))throw new Error('closed');return tabs.get(id);},create:async props=>{const tab={id:100+created.length,status:'complete',...props};created.push(tab);tabs.set(tab.id,tab);return tab;},remove:async id=>tabs.delete(id),sendMessage:async()=>{},onRemoved:{addListener:fn=>removed.push(fn)},onUpdated:{addListener:fn=>updated.push(fn)}},
    alarms:{get:async name=>alarmState.get(name),create:async(name,props)=>{scheduled.push([name,props]);alarmState.set(name,props);},clear:async name=>alarmState.delete(name),onAlarm:{addListener:fn=>alarms.push(fn)}},
    scripting:{executeScript:async input=>{injected.push(input);if(input.files){if(input.files.includes('content.js')&&samplerGate)await samplerGate;return [{frameId:0}];}if(input.func?.toString().includes('__workleadMessageInspect'))return [{result:visible?{state:'account',account_hash:visible,sample:{state:'watching',unread_count:0}}:{state:'held',reason_code:'login_required'}}];return [{result:null}];}}
  };
  globalThis.fetch=async(url,init)=>{
    requests.push({url,init,body:JSON.parse(init.body)});if(offline)throw new Error('offline');
    const body=JSON.parse(init.body),path=new URL(url).pathname;
    if(new URL(url).origin===MESSAGE_PAIR_URL) {
      assert.equal(init.headers.Authorization,undefined);assert.ok(!init.body.includes(token));
      if(path==='/hello')return {ok:true,json:async()=>({server_nonce:nonce,proof:await pairProof(token,['server',body.client_nonce,nonce,body.account_hash],'worklead-message-pair-v1')})};
      assert.equal(path,'/resume');assert.equal(body.proof,await pairProof(token,['client',body.client_nonce,nonce,body.account_hash],'worklead-message-pair-v1'));
      return {ok:true,json:async()=>({connection,proof:await pairProof(token,['session',body.client_nonce,nonce,body.account_hash,connection.url,connection.token],'worklead-message-pair-v1')})};
    }
    if(path==='/ping')return {ok:pingStatus===200,status:pingStatus,json:async()=>pingStatus===200?{purpose:'messages',version:1}:{error:'unauthorized'}};
    if(init.headers.Authorization!=='Bearer '+connection.token)return {ok:false,status:401,json:async()=>({error:'unauthorized'})};
    if(path==='/observe'&&observeGate)await observeGate;
    return {ok:observeStatus===200,status:observeStatus,json:async()=>observeStatus===200?{accepted:true}:{error:observeError}};
  };
  await import('../worklead-messages/worker.js?m2='+Math.random());
  const dispatch=(message,sender=popup)=>new Promise(resolve=>{if(!listeners.some(fn=>fn(message,sender,resolve)===true))resolve(undefined);});
  return {local,session,requests,injected,created,listeners,updated,removed,startup,installed,alarms,scheduled,alarmState,access,tabs,dispatch,
    account:v=>visible=v,ping:v=>pingStatus=v,observe:(v,error='unauthorized')=>{observeStatus=v;observeError=error;},offline:()=>offline=true,
    restart:()=>connection={url:'http://127.0.0.1:44449',token:'z'.repeat(43)},
    holdObserve(){let release;observeGate=new Promise(r=>release=r);return release;},
    restore(){globalThis.chrome=original.chrome;globalThis.fetch=original.fetch;if(!original.crypto)delete globalThis.crypto;}}
}

test('purpose tags and legacy fixed ports reject the other channel before HTTP',()=>{
  assert.deepEqual(connectionFrom(JSON.stringify(code),'messages'),{url:MESSAGE_PAIR_URL,token});
  assert.throws(()=>connectionFrom(JSON.stringify(code),'auto_apply'),/wrong_code_purpose/);
  assert.throws(()=>connectionFrom(JSON.stringify({url:'http://127.0.0.1:58347',token}),'messages'),/wrong_code_purpose/);
  assert.throws(()=>connectionFrom(JSON.stringify({...code,version:2}),'messages'),/invalid_code/);
  assert.throws(()=>connectionFrom(JSON.stringify({...code,extra:'bad'}),'messages'),/invalid_code/);
});

test('initial message connection proves server, observes actual current sample, and persists only scoped consent',async()=>{
  const h=await fixture({capture:true});try{
    assert.equal((await h.dispatch({type:'connect',code:JSON.stringify(code),capture_visible_chat:true})).ok,true);
    assert.deepEqual(h.local.messagePair,{url:MESSAGE_PAIR_URL,token,account_hash:account,enabled:true,capture_visible_chat:true});
    assert.ok(h.access.every(x=>x.accessLevel==='TRUSTED_CONTEXTS'));
    assert.equal(h.session.bridge.account_hash,account);assert.equal(h.session.bridge.captureChat,true);
    const observations=h.requests.filter(x=>new URL(x.url).pathname==='/observe');assert.equal(observations.length,1);assert.equal(observations[0].body.account_hash,account);
    assert.ok(h.injected.some(x=>x.files?.includes('chat-content.js')));
    assert.ok(!JSON.stringify(h.local).includes('sample'));assert.ok(!JSON.stringify(h.local).includes('chatSequence'));
  }finally{h.restore();}
});

test('expired direct code fails immediately, preserves an existing remembered grant, and returns a specific reason',async()=>{
  const h=await fixture();try{
    h.local.messagePair={url:MESSAGE_PAIR_URL,token,account_hash:account,enabled:false,capture_visible_chat:false};h.ping(401);
    const result=await h.dispatch({type:'connect',code:JSON.stringify(ephemeral)});
    assert.equal(result.ok,false);assert.match(result.message,/만료/);assert.equal(h.session.bridge,undefined);assert.equal(h.local.messagePair.token,token);
  }finally{h.restore();}
});

test('message connection does not claim success when first real observation fails',async()=>{
  const h=await fixture();try{
    h.observe(401);
    const result=await h.dispatch({type:'connect',code:JSON.stringify(code)});
    assert.equal(result.ok,false);assert.match(result.message,/만료/);assert.equal(h.session.bridge,undefined);assert.equal(h.local.messagePair,undefined);
  }finally{h.restore();}
});

test('HTTP 200 without an accepted observation cannot enable message monitoring',async()=>{
  for(const payload of [{accepted:false},{accepted:false,reconnect_required:true}]) {
    const h=await fixture(),original=globalThis.fetch;
    globalThis.fetch=(url,init)=>new URL(url).pathname==='/observe'?Promise.resolve({ok:true,json:async()=>payload}):original(url,init);
    try{
      const result=await h.dispatch({type:'connect',code:JSON.stringify(code)});
      assert.equal(result.ok,false);assert.match(result.message,/연결|계정/);
      assert.equal(h.session.bridge,undefined);assert.equal(h.local.messagePair,undefined);
      assert.ok(!h.injected.some(x=>x.files?.includes('content.js')));
    }finally{globalThis.fetch=original;h.restore();}
  }
});

test('remembered message startup creates one inactive owned tab and restores only the saved body opt-in',async()=>{
  const h=await fixture({saved:true,capture:false});try{
    await until(()=>h.session.bridge?.ready);assert.equal(h.created.length,1);assert.equal(h.created[0].active,false);assert.equal(h.session.bridge.tabId,h.created[0].id);
    assert.equal(h.session.bridge.captureChat,false);assert.ok(!h.injected.some(x=>x.files?.includes('chat-content.js')));
    for(const fn of h.startup)fn();for(const fn of h.installed)fn();for(const fn of h.alarms)fn({name:MESSAGE_PAIR_ALARM});await wait(20);assert.equal(h.created.length,1);
  }finally{h.restore();}
});

for(const capture of [false,true])test('healthy remembered message boot restores samplers without resetting sequences or capture consent: '+capture,async()=>{
  const h=await fixture({saved:true,ready:true,capture});try{
    await until(()=>h.scheduled.some(x=>x[0]==='worklead-message-sampler'));
    assert.equal(h.created.length,0);assert.equal(h.session.bridge.sequence,17);assert.equal(h.session.bridge.chatSequence,9);
    assert.equal(h.session.bridge.captureChat,capture);
    assert.equal(h.injected.some(x=>x.files?.includes('chat-content.js')),capture);
    assert.deepEqual(h.requests.map(x=>new URL(x.url).pathname),['/ping']);
    await h.dispatch({type:'unread-sample',sample:{state:'watching',unread_count:1}},{tab:{id:7},frameId:0,url:'https://jobs.daangn.com/'});
    const observations=h.requests.filter(x=>new URL(x.url).pathname==='/observe');
    assert.equal(observations.length,1);assert.equal(observations[0].body.sequence,18);
    assert.equal(h.session.bridge.chatSequence,9);assert.equal(h.local.messagePair.capture_visible_chat,capture);
  }finally{h.restore();}
});

test('stop during ready-message sampler restoration cannot resurrect its bridge or capture',async()=>{
  let release;const gate=new Promise(resolve=>release=resolve);
  const h=await fixture({saved:true,ready:true,capture:true,samplerGate:gate});try{
    await until(()=>h.injected.some(x=>x.files?.includes('content.js')));
    const stopped=h.dispatch({type:'disconnect'});
    await until(()=>h.session.bridge===undefined);release();
    assert.equal((await stopped).ok,true);await wait(20);
    assert.equal(h.session.bridge,undefined);assert.equal(h.local.messagePair,undefined);
    assert.ok(!h.injected.some(x=>x.files?.includes('chat-content.js')));
    assert.ok(!h.scheduled.some(x=>x[0]==='worklead-message-sampler'));
    assert.deepEqual(h.requests.map(x=>new URL(x.url).pathname),['/ping']);
  }finally{release();h.restore();}
});

test('pair heartbeats preserve sampler deadlines and do not repeatedly inject or reopen chat',async()=>{
  const h=await fixture({saved:true,ready:true,capture:true});try{
    await until(()=>h.alarmState.has('worklead-message-sampler'));
    const count=h.injected.filter(x=>x.files?.includes('content.js')).length;
    const scheduled=h.scheduled.length;
    for(let i=0;i<3;i++){
      const pings=h.requests.filter(x=>new URL(x.url).pathname==='/ping').length;
      for(const fn of h.alarms)fn({name:MESSAGE_PAIR_ALARM});
      await until(()=>h.requests.filter(x=>new URL(x.url).pathname==='/ping').length>pings);await wait(10);
    }
    assert.equal(h.injected.filter(x=>x.files?.includes('content.js')).length,count);
    assert.equal(h.scheduled.length,scheduled);
    h.alarmState.delete('worklead-message-sampler');
    for(const fn of h.alarms)fn({name:MESSAGE_PAIR_ALARM});
    await until(()=>h.alarmState.has('worklead-message-sampler'));
    assert.equal(h.injected.filter(x=>x.files?.includes('content.js')).length,count+1);
    assert.equal(h.session.bridge.sequence,17);assert.equal(h.session.bridge.chatSequence,9);
  }finally{h.restore();}
});

test('late message sampler-alarm creation is cleared after a concurrent stop',async()=>{
  const h=await fixture(),create=globalThis.chrome.alarms.create;
  let started=false,release;const gate=new Promise(resolve=>release=resolve);
  globalThis.chrome.alarms.create=async(name,props)=>{if(name==='worklead-message-sampler'){started=true;await gate;}return create(name,props);};
  try{
    const connected=h.dispatch({type:'connect',code:JSON.stringify(code),capture_visible_chat:true});
    await until(()=>started);
    const stopped=h.dispatch({type:'disconnect'});await until(()=>h.session.bridge===undefined);
    release();assert.equal((await stopped).ok,true);assert.equal((await connected).ok,false);await wait(20);
    assert.equal(h.alarmState.size,0);assert.equal(h.local.messagePair,undefined);assert.equal(h.session.bridge,undefined);
  }finally{release();h.restore();}
});

test('message page reload reinjects selected tab samplers without a new code',async()=>{
  const h=await fixture();try{
    await h.dispatch({type:'connect',code:JSON.stringify(code),capture_visible_chat:false});
    const count=h.injected.filter(x=>x.files?.includes('content.js')).length;
    for(const fn of h.updated)fn(7,{status:'complete'});
    await until(()=>h.injected.filter(x=>x.files?.includes('content.js')).length>count);
    assert.equal(h.created.length,0);assert.equal(h.session.bridge.tabId,7);
  }finally{h.restore();}
});

test('message account change pauses and clears the capture bridge instead of forwarding the other account',async()=>{
  const h=await fixture();try{
    await h.dispatch({type:'connect',code:JSON.stringify(code),capture_visible_chat:true});h.account('c'.repeat(64));
    await h.dispatch({type:'unread-sample',sample:{state:'watching',unread_count:3}},{tab:{id:7},frameId:0,url:'https://jobs.daangn.com/'});
    assert.equal(h.local.messagePair.enabled,false);assert.equal(h.session.bridge,undefined);
    assert.ok(!h.requests.filter(x=>new URL(x.url).pathname==='/observe').some(x=>x.body.account_hash==='c'.repeat(64)));
  }finally{h.restore();}
});

test('failed replacement handshake keeps an existing approved auto credential',async()=>{
  // Shared controller validation is used by the actual auto-worker configure path.
  const h=await fixture();const previous={url:'http://127.0.0.1:58347',token:'p'.repeat(43),account_hash:account,enabled:true};h.local.autoPair={...previous};
  const controller=createReconnect({readAccount:async()=>({state:'account',account_hash:account}),connected:async()=>{throw new Error('should_not_connect');},clearSession:async()=>{},status:async()=>{},validateConnection:async()=>{throw new Error('expired_code');}});
  const original=globalThis.fetch;globalThis.fetch=async(url,init)=>{const body=JSON.parse(init.body),nonce='b'.repeat(64),pair={url:'http://127.0.0.1:58347',token:'n'.repeat(43)};return {ok:true,json:async()=>new URL(url).pathname==='/hello'?{server_nonce:nonce,proof:await pairProof(pair.token,['server',body.client_nonce,nonce,account])}:{connection:ephemeral,proof:await pairProof(pair.token,['session',body.client_nonce,nonce,account,ephemeral.url,ephemeral.token])}};};
  try{await assert.rejects(controller.initial({url:'http://127.0.0.1:58347',token:'n'.repeat(43)},7),/expired_code/);assert.deepEqual(h.local.autoPair,previous);}
  finally{globalThis.fetch=original;h.restore();}
});

test('app restart replaces an expired message session without another code or tab',async()=>{
  const h=await fixture();try{
    await h.dispatch({type:'connect',code:JSON.stringify(code)});h.restart();
    await h.dispatch({type:'unread-sample',sample:{state:'watching',unread_count:1}},{tab:{id:7},frameId:0,url:'https://jobs.daangn.com/'});
    await until(()=>h.session.bridge?.token==='z'.repeat(43));
    assert.equal(h.created.length,0);assert.equal(h.session.bridge.tabId,7);assert.equal(h.local.messagePair.token,token);
    assert.equal(h.requests.filter(x=>new URL(x.url).pathname==='/resume').length,2);
  }finally{h.restore();}
});

test('explicit stop invalidates an in-flight first observation and cannot resurrect samplers or credentials',async()=>{
  const h=await fixture();const release=h.holdObserve();try{
    const pending=h.dispatch({type:'connect',code:JSON.stringify(code),capture_visible_chat:true});
    await until(()=>h.requests.some(x=>new URL(x.url).pathname==='/observe'));
    assert.equal((await h.dispatch({type:'disconnect'})).ok,true);release();assert.equal((await pending).ok,false);
    assert.equal(h.session.bridge,undefined);assert.equal(h.local.messagePair,undefined);
    assert.ok(!h.injected.some(x=>x.files?.includes('content.js')||x.files?.includes('chat-content.js')));
    assert.match(h.session.connectionStatus.message,/삭제하고 중지/);
  }finally{release();h.restore();}
});

test('message HMAC mismatch stops before resume and does not store a remembered grant',async()=>{
  const h=await fixture(),original=globalThis.fetch;globalThis.fetch=async(url,init)=>new URL(url).pathname==='/hello'?{ok:true,json:async()=>({server_nonce:'b'.repeat(64),proof:'0'.repeat(64)})}:original(url,init);
  try{
    const result=await h.dispatch({type:'connect',code:JSON.stringify(code)});assert.equal(result.ok,false);assert.match(result.message,/확인이 일치/);
    assert.equal(h.session.bridge,undefined);assert.equal(h.local.messagePair,undefined);assert.ok(!h.requests.some(x=>new URL(x.url).pathname==='/resume'));
  }finally{globalThis.fetch=original;h.restore();}
});

test('backend account-loss error pauses the remembered connection and retains the specific reason',async()=>{
  const h=await fixture();try{
    await h.dispatch({type:'connect',code:JSON.stringify(code)});h.observe(409,'account_changed');
    await h.dispatch({type:'unread-sample',sample:{state:'watching',unread_count:1}},{tab:{id:7},frameId:0,url:'https://jobs.daangn.com/'});
    assert.equal(h.session.bridge,undefined);assert.equal(h.local.messagePair.enabled,false);assert.match(h.session.connectionStatus.message,/계정과 달라/);
  }finally{h.restore();}
});
