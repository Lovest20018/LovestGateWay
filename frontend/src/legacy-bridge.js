// Mount the upstream public entry point in the shared document.
let started=false,runtimeHost=null;
export async function mountRuntime(host,{onUnauthorized,onLocation,onHistoryPush}) {
  if(started)return;
  started=true;runtimeHost=host;
  const push=history.pushState.bind(history),replace=history.replaceState.bind(history);
  history.pushState=(state,title,url)=>{push({...state,ppgwIndex:onHistoryPush(state?.ppgwIndex)},title,url);onLocation();};
  history.replaceState=(state,title,url)=>{replace({...state,ppgwIndex:state?.ppgwIndex??history.state?.ppgwIndex??0},title,url);};
  // Preserve saved profiles and preferences when upstream writes its secret.
  const setItem=Storage.prototype.setItem,removeItem=Storage.prototype.removeItem;
  Storage.prototype.setItem=function(key,value){if(this===localStorage&&key==='PaoPaoGateWay'){try{const previous=JSON.parse(this.getItem(key)||'{}'),next=JSON.parse(value);if(previous&&next&&typeof previous==='object'&&typeof next==='object'&&!Array.isArray(previous)&&!Array.isArray(next)){const merged={...previous,...next};if(Array.isArray(next.clashAPIConfigs)&&Array.isArray(previous.clashAPIConfigs)){merged.clashAPIConfigs=next.clashAPIConfigs.map((row,index)=>({...previous.clashAPIConfigs[index],...row}));const authOnly=Object.keys(next).length===1&&next.clashAPIConfigs.length===1&&next.clashAPIConfigs[0]&&Object.keys(next.clashAPIConfigs[0]).length===1&&Object.hasOwn(next.clashAPIConfigs[0],'secret');if(authOnly)merged.clashAPIConfigs.push(...previous.clashAPIConfigs.slice(1));}value=JSON.stringify(merged);}}catch{}}return setItem.call(this,key,value);};
  Storage.prototype.removeItem=function(key){if(this===localStorage&&key==='PaoPaoGateWay'){try{const saved=JSON.parse(this.getItem(key)||'null');if(saved&&typeof saved==='object'&&!Array.isArray(saved)){if('secret'in saved)saved.secret='';if(Array.isArray(saved.clashAPIConfigs))saved.clashAPIConfigs=saved.clashAPIConfigs.map(row=>({...row,secret:''}));return setItem.call(this,key,JSON.stringify(saved));}}catch{}}return removeItem.call(this,key);};
  let hadLogin=false;
  new MutationObserver(()=>{const login=host.querySelector('input[autocomplete="current-password"]');if(login){login.closest('.wrap')?.setAttribute('hidden','');if(!hadLogin)onUnauthorized();}hadLogin=!!login;}).observe(host,{childList:true,subtree:true});
  globalThis.__sveltekit_1higjzk={base:'/ui',assets:'/ui'};
  const runtime=await import('/ui/@@runtime-bundle@@');
  runtime.start(host);
}

// Reauthenticate through the public form, retaining the source editor draft.
export function authenticateRuntime(host,password){
  const input=host?.querySelector('input[autocomplete="current-password"]');
  if(!input)return;
  input.value=password;
  input.dispatchEvent(new Event('input',{bubbles:true}));
  input.closest('form')?.requestSubmit();
}

// Let the upstream router handle its public links. Its route state and browser
// history stay valid; source routes retain the same history keys while hidden.
export function navigateRuntime(hash,replace=false){
  if(!runtimeHost?.querySelector('main'))return false;
  const link=document.createElement('a');link.href=hash;link.hidden=true;
  if(replace)link.setAttribute('data-sveltekit-replacestate','true');
  document.body.append(link);link.click();link.remove();return true;
}
