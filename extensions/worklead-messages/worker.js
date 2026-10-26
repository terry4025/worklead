import { connectionFrom, isJobPage, validSample, validChatSample } from './protocol.js';
import { connectionError, failureCode, ping } from './connection-errors.js';
import { createReconnect, MESSAGE_PAIR_URL, MESSAGE_PAIR_ALARM } from './reconnect.js';
import { hashShape, JOBS_PERMISSION } from './auto-protocol.js';
import './auto-worker.js';

let work = Promise.resolve(), epoch = 0;
const SAMPLER_ALARM = 'worklead-message-sampler';
const serialized = operation => { const result = work.then(operation); work = result.catch(() => {}); return result; };
const setStatus = message => chrome.storage.session.set({ connectionStatus: { state:'waiting', message, observedAt:Date.now() } });
const config = async () => (await chrome.storage.session.get('bridge')).bridge;
const current = async c => { const live=await config(); return live?.token===c.token && live?.url===c.url && live?.tabId===c.tabId; };
const bounded = async operation => { let timer; try { return await Promise.race([operation,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('browser_timeout')),8000);})]); } finally { clearTimeout(timer); } };
async function identity(tabId) {
  const tab=await chrome.tabs.get(tabId);
  if(!isJobPage(tab.url))throw new Error('jobs_tab_required');
  await bounded(chrome.scripting.executeScript({target:{tabId,allFrames:false},files:['identity.js'],world:'ISOLATED'}));
  const values=await bounded(chrome.scripting.executeScript({target:{tabId,allFrames:false},world:'ISOLATED',func:()=>globalThis.__workleadMessageInspect?.()}));
  const value=values?.length===1?values[0].result:null;
  if(!value)throw new Error('identity_not_ready');
  return value;
}
async function clearSession() {
  epoch++;
  const previous=await config();
  await chrome.storage.session.remove(['bridge']);
  await chrome.alarms.clear(SAMPLER_ALARM);
  if(previous) { try { await chrome.tabs.sendMessage(previous.tabId,{type:'stop-unread-sampler'}); } catch {} }
}
async function post(c,path,body) {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
  try {
    const response=await fetch(c.url+path,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+c.token},body:JSON.stringify(body),credentials:'omit',redirect:'error',cache:'no-store',signal:controller.signal});
    let value;try{value=await response.json();}catch{}
    if(!response.ok)throw new Error(failureCode(response.status,value?.error));
    return value;
  } catch(error) { if(['expired_code','wrong_code_purpose','permission_required','capture_not_enabled','message_watch_stopped','auto_apply_stopped','account_changed','login_required','pairing_busy'].includes(error.message))throw error;throw new Error('app_unavailable'); }
  finally{clearTimeout(timer);}
}
async function inject(c,active=()=>true) {
  if(!active() || !await current(c))throw new Error('cancelled');
  await bounded(chrome.scripting.executeScript({target:{tabId:c.tabId,allFrames:false},files:['content.js'],world:'ISOLATED'}));
  if(!active() || !await current(c))throw new Error('cancelled');
  if(c.captureChat) {
    await bounded(chrome.scripting.executeScript({target:{tabId:c.tabId,allFrames:false},files:['chat-content.js'],world:'ISOLATED'}));
    if(!active() || !await current(c))throw new Error('cancelled');
    await bounded(chrome.scripting.executeScript({target:{tabId:c.tabId,allFrames:false},world:'ISOLATED',func:()=>globalThis.__workleadOpenChatPanel?.()}));
  }
  await chrome.alarms.create(SAMPLER_ALARM,{periodInMinutes:0.5});
}
async function install(connection,tabId,account_hash,active=()=>true,pair={}) {
  const captured=epoch;
  const alive=()=>active() && captured===epoch;
  if(!alive())throw new Error('cancelled');
  if((await chrome.storage.session.get('autoBridge')).autoBridge?.tabId===tabId)throw new Error('separate_tab_required');
  await ping(connection,'messages');
  const view=await identity(tabId);
  if(view.state!=='account'||view.account_hash!==account_hash)throw new Error(view.reason_code==='login_required'?'login_required':'identity_not_ready');
  if(!validSample(view.sample) || view.sample.state!=='watching')throw new Error('identity_not_ready');
  const c={...connection,tabId,sequence:1,chatSequence:0,account_hash,ready:true,captureChat:pair.capture_visible_chat===true};
  const observed=await post(c,'/observe',{...view.sample,account_hash,sequence:1});
  if(observed?.reconnect_required)throw new Error('account_changed');
  if(observed?.accepted!==true)throw new Error('observation_rejected');
  if(!alive())throw new Error('cancelled');
  const previous=await config();
  if(previous)try{await chrome.tabs.sendMessage(previous.tabId,{type:'stop-unread-sampler'});}catch{}
  await chrome.storage.session.set({bridge:c});
  if(!alive()) {if(await current(c))await chrome.storage.session.remove('bridge');throw new Error('cancelled');}
  await inject(c,alive);
  await chrome.storage.session.set({connectionStatus:{state:'connected',message:'Worklead가 현재 로그인·미읽음 상태를 확인했습니다.',observedAt:Date.now()}});
}
const reconnect=createReconnect({readAccount:identity,connected:install,clearSession,status:setStatus,validateConnection:c=>ping(c,'messages'),options:{purpose:'messages'}});
async function configure(message,sender) {
  const captured=++epoch,active=()=>captured===epoch;
  if(sender.tab||sender.url!==chrome.runtime.getURL('popup.html'))throw new Error('popup_required');
  const value=connectionFrom(message.code,'messages');
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  if(!tab||!Number.isInteger(tab.id)||!isJobPage(tab.url))throw new Error('jobs_tab_required');
  if((await chrome.storage.session.get('autoBridge')).autoBridge?.tabId===tab.id)throw new Error('separate_tab_required');
  if(!await chrome.permissions.contains({origins:['http://127.0.0.1/*',JOBS_PERMISSION]}))throw new Error('permission_required');
  if(!active())throw new Error('cancelled');
  if(value.url===MESSAGE_PAIR_URL)return reconnect.initial(value,tab.id,{capture_visible_chat:message.capture_visible_chat===true});
  const view=await identity(tab.id);
  if(view.state!=='account'||!hashShape(view.account_hash))throw new Error(view.reason_code==='login_required'?'login_required':'identity_not_ready');
  if(!active())throw new Error('cancelled');
  await install(value,tab.id,view.account_hash,active,{capture_visible_chat:message.capture_visible_chat===true});
  return {ok:true};
}
async function connectionLost(error,c) {
  if(!await current(c))return;
  if(['account_changed','login_required','message_watch_stopped'].includes(error.message)) {
    if(chrome.storage.local)await reconnect.pause(connectionError(error,'messages'));
    else await clearSession();
    return;
  }
  await chrome.storage.session.set({connectionStatus:{state:'error',message:connectionError(error,'messages'),observedAt:Date.now()}});
  if(chrome.storage.local)void reconnect.resume({force:true});
}
async function pauseIdentity(c,reason) {
  if(!await current(c))return;
  try{await post(c,'/observe',{state:'login_required',unread_count:null,account_hash:null,sequence:c.sequence+1});}catch{}
  if(chrome.storage.local)await reconnect.pause(connectionError(reason,'messages'));
  else await clearSession();
}
async function forward(message,sender) {
  const c=await config();
  if(!c||sender.tab?.id!==c.tabId||sender.frameId!==0||!isJobPage(sender.url)||!validSample(message.sample))return {ok:false};
  try {
    const view=await identity(c.tabId);
    if(view.state!=='account'||!hashShape(view.account_hash)) {
      if(view.reason_code==='login_required')await pauseIdentity(c,'login_required');
      else await setStatus(connectionError('identity_not_ready'));
      return {ok:false};
    }
    if(view.account_hash!==c.account_hash) {await pauseIdentity(c,'account_changed');return {ok:false};}
    const sequence=c.sequence+1;
    if(!await current(c))return {ok:false};
    await chrome.storage.session.set({bridge:{...c,sequence}});
    const result=await post(c,'/observe',{...message.sample,account_hash:view.account_hash,sequence});
    if(!await current(c))return {ok:false};
    if(result?.reconnect_required){await pauseIdentity(c,'account_changed');return {ok:false};}
    await chrome.storage.session.set({connectionStatus:{state:'connected',message:'Worklead에 표시 상태를 전달했습니다.',observedAt:Date.now()}});
    return {ok:true};
  }catch(error){await connectionLost(error,c);return {ok:false};}
}
async function forwardChat(message,sender) {
  const c=await config();
  if(!c?.captureChat||sender.tab?.id!==c.tabId||sender.frameId!==0||!isJobPage(sender.url)||!validChatSample(message.sample))return {ok:false};
  if(message.sample.state==='login_required'||message.sample.account_hash!==c.account_hash){await pauseIdentity(c,message.sample.state==='login_required'?'login_required':'account_changed');return {ok:false};}
  const sequence=c.chatSequence+1;
  await chrome.storage.session.set({bridge:{...c,chatSequence:sequence}});
  try {
    const result=await post(c,'/chat',{...message.sample,sequence});
    if(result?.reconnect_required){await pauseIdentity(c,'account_changed');return {ok:false};}
    return {ok:true};
  }catch(error){
    if(error.message==='permission_required')await setStatus(connectionError('capture_not_enabled'));
    else await connectionLost(error,c);
    return {ok:false};
  }
}
async function reinject() {
  const c=await config();if(!c)return;
  try{await inject(c);}catch(error){await connectionLost(error,c);}
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(!message||typeof message.type!=='string')return false;
  const popup=!sender.tab&&sender.url===chrome.runtime.getURL('popup.html');
  let operation;
  if(message.type==='connect'&&popup)operation=()=>configure(message,sender);
  else if(message.type==='unread-sample')operation=()=>forward(message,sender);
  else if(message.type==='chat-sample')operation=()=>forwardChat(message,sender);
  else if(message.type==='disconnect'&&popup) {
    // Invalidate an in-flight connect before waiting for its browser/HTTP work.
    epoch++;
    (async()=>{if(chrome.storage.local)await reconnect.forget();else await clearSession();await setStatus('메시지 감시 저장 연결을 삭제하고 중지했습니다.');return {ok:true};})().then(respond);
    return true;
  }
  else if(message.type==='message-resume'&&popup)operation=()=>reconnect.resume({manual:true,force:true});
  else return false;
  serialized(operation).then(respond,async error=>{const text=connectionError(error,'messages');if(error.message!=='cancelled')await setStatus(text);respond({ok:false,message:text});});return true;
});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name===SAMPLER_ALARM)void serialized(reinject);if(alarm.name===MESSAGE_PAIR_ALARM&&chrome.storage.local)void reconnect.resume();});
chrome.tabs.onUpdated?.addListener((id,change)=>{if(change.status==='complete')void serialized(async()=>{if((await config())?.tabId===id)await reinject();});});
chrome.tabs.onRemoved.addListener((id,info)=>{void serialized(async()=>{if(chrome.storage.local&&await reconnect.tabClosed(id,info?.isWindowClosing===true))return;if((await config())?.tabId===id)await clearSession();});});
chrome.runtime.onStartup?.addListener(()=>{if(chrome.storage.local)void reconnect.boot();});
chrome.runtime.onInstalled?.addListener(()=>{if(chrome.storage.local)void reconnect.boot();});
if(chrome.storage.local)void reconnect.boot();
