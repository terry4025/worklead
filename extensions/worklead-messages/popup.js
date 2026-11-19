import { connectionFrom } from './protocol.js';
import { JOBS_PERMISSION } from './auto-protocol.js';
import { connectionError } from './connection-errors.js';
document.getElementById('extension-version').textContent='연결 확장 '+chrome.runtime.getManifest().version;
const busy={messages:false,auto_apply:false},errors={messages:null,auto_apply:null};
const status=document.getElementById('status'),autoStatus=document.getElementById('auto-status');
const code=document.getElementById('code'),autoCode=document.getElementById('auto-code');
let revision=0;
async function refresh() {
  const version=++revision;
  const a=await chrome.storage.session.get('connectionStatus');
  const b=await chrome.storage.session.get('autoStatus');
  const p=await chrome.storage.local.get('autoPair');
  const m=await chrome.storage.local.get('messagePair');
  if(version!==revision)return;
  if(!busy.messages&&!errors.messages)status.textContent=a.connectionStatus?.message||'메시지 감시를 연결하지 않았습니다.';
  if(!busy.auto_apply&&!errors.auto_apply)autoStatus.textContent=b.autoStatus?.message||'자동 지원을 연결하지 않았습니다.';
  document.getElementById('auto-resume').hidden=!p.autoPair;
  document.getElementById('message-resume').hidden=!m.messagePair;
}
async function connect(purpose) {
  const auto=purpose==='auto_apply',field=auto?autoCode:code,out=auto?autoStatus:status,button=document.getElementById(auto?'auto-connect':'connect');
  errors[purpose]=null;busy[purpose]=true;button.disabled=true;
  try {
    if(auto&&!document.getElementById('auto-consent').checked) {errors[purpose]='저장된 기본 자기소개의 자동 제출 범위를 확인해 주세요.';return;}
    connectionFrom(field.value,purpose);
    if(!await chrome.permissions.request({origins:['http://127.0.0.1/*',JOBS_PERMISSION]}))throw new Error('permission_required');
    const result=await chrome.runtime.sendMessage({type:auto?'auto-connect':'connect',code:field.value,...(auto?{confirm:true}:{capture_visible_chat:document.getElementById('chat-consent').checked})});
    field.value='';
    if(!result?.ok){errors[purpose]=result?.message||connectionError('app_unavailable',purpose);return;}
    if(auto)document.getElementById('auto-consent').checked=false;
  }catch(error){errors[purpose]=connectionError(error,purpose);}
  finally{busy[purpose]=false;button.disabled=false;if(errors[purpose])out.textContent=errors[purpose];else await refresh();}
}
document.getElementById('connect').addEventListener('click',()=>connect('messages'));
document.getElementById('auto-connect').addEventListener('click',()=>connect('auto_apply'));
for(const purpose of ['messages','auto_apply']) {
  const auto=purpose==='auto_apply',out=auto?autoStatus:status,field=auto?autoCode:code;
  document.getElementById(auto?'auto-disconnect':'disconnect').addEventListener('click',async()=>{
    errors[purpose]=null;busy[purpose]=true;
    try{const result=await chrome.runtime.sendMessage({type:auto?'auto-disconnect':'disconnect'});field.value='';if(!result?.ok)errors[purpose]=connectionError('app_unavailable',purpose);}
    catch(error){errors[purpose]=connectionError(error,purpose);}
    finally{busy[purpose]=false;if(errors[purpose])out.textContent=errors[purpose];else await refresh();}
  });
  document.getElementById(auto?'auto-resume':'message-resume').addEventListener('click',async()=>{
    const button=document.getElementById(auto?'auto-resume':'message-resume');button.disabled=true;errors[purpose]=null;busy[purpose]=true;
    try{await chrome.runtime.sendMessage({type:auto?'auto-resume':'message-resume'});}
    catch(error){errors[purpose]=connectionError(error,purpose);}
    finally{busy[purpose]=false;button.disabled=false;if(errors[purpose])out.textContent=errors[purpose];else await refresh();}
  });
}
chrome.storage.onChanged.addListener((_,area)=>{if(area==='session'||area==='local')void refresh();});
void refresh();
