import {mountRuntime,navigateRuntime,authenticateRuntime} from './legacy-bridge.js';
import {icon} from './icons.js';
import {mountEditor} from './editor.js';
import {sha256,storedSecret,storeSecret} from './auth.js';
import {mountDiagnostics,pendingRecoveryJob} from './diagnostics.js';
import {mountOverview} from './overview.js';
import {mountProxies} from './proxies.js';
import {mountRules} from './rules.js';
const app=document.getElementById('app'),routes=[['overview','概览'],['proxies','代理'],['rules','规则'],['connections','连接'],['traffic','流量'],['distribution','分布'],['logs','日志'],['region','区域'],['subscriptions','订阅与策略'],['settings','网关设置'],['diagnostics','诊断与恢复']];
let info=null,gateway=null,editor=null,runtimeHost=null,workspace=null,diagnostics=null,diagnosticHost=null,overview=null,overviewHost=null,proxies=null,proxyHost=null,rules=null,rulesHost=null,route='',timer=null,authenticated=false;
let navigationIndex=history.state?.ppgwIndex||0,recoveringHistory=false;
history.replaceState({...history.state,ppgwIndex:navigationIndex},'');
const el=(tag,attrs={},children=[])=>{const n=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==='text')n.textContent=v;else if(k.startsWith('on'))n.addEventListener(k.slice(2),v);else n.setAttribute(k,v);}n.append(...children);return n;};
const button=(text,onclick)=>el('button',{type:'button',class:'btn',text,onclick});
const routeIcons={overview:'gauge',proxies:'globe',rules:'filter',connections:'swap',traffic:'history',distribution:'pie',logs:'terminal',region:'radio',subscriptions:'package',settings:'settings',diagnostics:'activity'};
function expired(){if(document.getElementById('reauth-panel'))return;const status=document.getElementById('shell-status');if(status){status.hidden=false;status.replaceChildren(document.createTextNode('登录已失效，任务与未保存的编辑仍保留。 '),button('重新登录',()=>login(true)));}}
const systemTheme=matchMedia('(prefers-color-scheme: dark)');
function selectedTheme(){const saved=localStorage.getItem('ppgw-theme');return ['system','light','dark'].includes(saved)?saved:'system';}
function applyTheme(){const selected=selectedTheme(),resolved=selected==='system'?(systemTheme.matches?'dark':'light'):selected;document.documentElement.dataset.theme=resolved;document.documentElement.style.colorScheme=resolved;for(const key of ['--bg','--bg-elev','--text','--nav-bg','--nav-text','--brand','--brand-deep','--brand-text','--brand-soft','--brand-grad'])document.documentElement.style.removeProperty(key);document.querySelectorAll('#shell-appearance button').forEach(choice=>choice.setAttribute('aria-pressed',String(choice.dataset.themeChoice===selected)));
}
function themeControl(){const choices=el('div',{id:'shell-appearance',class:'shell-appearance',role:'group','aria-label':'外观'});for(const[value,label,name,graphic]of [['system','系统','跟随系统','monitor'],['light','亮色','亮色','sun'],['dark','暗色','暗色','moon']]){const choice=button('',()=>{localStorage.setItem('ppgw-theme',value);applyTheme();});choice.setAttribute('aria-label',name);choice.setAttribute('title',name);choice.setAttribute('aria-pressed',String(selectedTheme()===value));choice.dataset.themeChoice=value;choice.append(icon(graphic),el('span',{text:label}));choices.append(choice);}return choices;}
async function runtime(){try{const response=await fetch('/sniffbox/info',{headers:{Authorization:'Bearer '+storedSecret()},cache:'no-store',signal:AbortSignal.timeout(10000)});if(response.ok){info=await response.json();updateNav();return true;}if(response.status===401)expired();return false;}catch{return false;}}
function updateNav(){document.querySelectorAll('[data-route]').forEach(a=>{const key=a.dataset.route;a.hidden=['proxies','rules'].includes(key)&&!['yaml','suburl'].includes(info?.mode);a.classList.toggle('active',route===key);if(route===key)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});}
function currentHash(){const key=location.hash.slice(2).split('?')[0];return key==='ppsub'?'subscriptions':routes.some(r=>r[0]===key)?key:'overview';}
function allowed(hash,type='goto'){const next=hash?new URL(hash,location.href):undefined;return (!gateway||gateway.allowNavigation(next,type))&&(!diagnostics||diagnostics.allowNavigation(next,type));}
function navigate(hash,replace=false){
  if(!allowed(hash))return false;
  const runtimeRoute=!['overview','proxies','rules','settings','subscriptions','diagnostics'].includes(hash.slice(2));
  if(runtimeRoute&&navigateRuntime(hash,replace))return true;
  if(replace)history.replaceState({...history.state,ppgwIndex:navigationIndex},'',hash);
  else history.pushState({...history.state,ppgwIndex:++navigationIndex},'',hash);
  show(true);scrollTo(0,0);return true;
}
function show(guarded=false){if(!authenticated)return;const next=currentHash(),migrated=['settings','subscriptions'].includes(next);if(!guarded&&route&&next!==route&&!allowed('#/'+next)){history.replaceState(null,'','#/'+route);return;}
  if(/^#\/ppsub(?:\?|$)/.test(location.hash))history.replaceState({...history.state,ppgwIndex:navigationIndex},'',location.hash.replace('#/ppsub','#/subscriptions'));
  route=next;document.getElementById('shell-nav').classList.remove('open');document.getElementById('shell-menu').setAttribute('aria-expanded','false');
  if(diagnostics&&next!=='diagnostics'){diagnostics.dispose();diagnostics=null;diagnosticHost.remove();diagnosticHost=null;}
  if(overview&&next!=='overview'){overview.dispose();overview=null;overviewHost.remove();overviewHost=null;}
  if(proxies&&next!=='proxies'){proxies.dispose();proxies=null;proxyHost.remove();proxyHost=null;}
  if(rules&&next!=='rules'){rules.dispose();rules=null;rulesHost.remove();rulesHost=null;}
  if(!migrated&&workspace){editor?.();editor=null;gateway?.();gateway=null;workspace.remove();workspace=null;}
  if(migrated){if(!workspace){workspace=el('div',{class:'ppgw-workspace page'});const settings=el('div',{class:'ppgw-gateway page'}),host=el('div',{class:'ppgw-subscription-editor',hidden:''});workspace.append(settings,host);document.getElementById('shell-content').append(workspace);gateway=window.ppgwGateway.mount(settings,{editorHost:host,createEditor:()=>{editor ||=mountEditor(host);},navigate,refreshRuntime:async isCurrent=>{if(isCurrent())await runtime();}});}workspace.hidden=false;if(runtimeHost)runtimeHost.hidden=true;gateway.refresh();}
  else if(next==='overview'){if(runtimeHost)runtimeHost.hidden=true;if(!overview){overviewHost=el('div');document.getElementById('shell-content').append(overviewHost);overview=mountOverview(overviewHost,{initialInfo:info,onUnauthorized:expired,onInfo:value=>{info=value;updateNav();}});}}
  else if(next==='proxies'){if(runtimeHost)runtimeHost.hidden=true;if(!proxies){proxyHost=el('div');document.getElementById('shell-content').append(proxyHost);proxies=mountProxies(proxyHost,{initialInfo:info,onUnauthorized:expired});}}
  else if(next==='rules'){if(runtimeHost)runtimeHost.hidden=true;if(!rules){rulesHost=el('div');document.getElementById('shell-content').append(rulesHost);rules=mountRules(rulesHost,{initialInfo:info,onUnauthorized:expired});}}
  else if(next==='diagnostics'){if(runtimeHost)runtimeHost.hidden=true;if(!diagnostics){diagnosticHost=el('div');document.getElementById('shell-content').append(diagnosticHost);diagnostics=mountDiagnostics(diagnosticHost,{onUnauthorized:expired,onComplete:runtime});}}
  else {if(!runtimeHost){runtimeHost=el('div',{id:'runtime-panel'});document.getElementById('shell-content').append(runtimeHost);mountRuntime(runtimeHost,{onUnauthorized:expired,onHistoryPush:index=>{if(Number.isInteger(index)){navigationIndex=index;return index;}return ++navigationIndex;},onLocation:()=>{show(true);scrollTo(0,0);}}).catch(()=>{runtimeHost.replaceChildren(el('p',{role:'status',text:'运行面板暂时无法加载，请刷新页面重试。'}));});}runtimeHost.hidden=false;}
  document.title=(routes.find(r=>r[0]===next)?.[1]||'概览')+' · LovestGateWay';updateNav();
}
function shell(){if(pendingRecoveryJob())history.replaceState({...history.state,ppgwIndex:navigationIndex},'','#/diagnostics');app.replaceChildren();const nav=el('nav',{id:'shell-nav','aria-label':'主导航'}),brand=el('a',{href:'#/overview',class:'shell-brand',onclick:e=>{e.preventDefault();navigate('#/overview');}});brand.append(el('img',{src:'/ui/lovestgateway.svg',width:'34',height:'34',alt:''}),el('span',{class:'shell-wordmark'},[document.createTextNode('Lovest'),el('b',{text:'GateWay'})]));nav.append(brand);for(const[key,label]of routes){const link=el('a',{href:'#/'+key,'data-route':key,text:label,onclick:e=>{if(e.ctrlKey||e.metaKey||e.shiftKey)return;e.preventDefault();navigate('#/'+key);}});link.prepend(icon(routeIcons[key]));nav.append(link);}nav.append(themeControl(),button('退出登录',()=>{if(!allowed('#/logout'))return;authenticated=false;storeSecret('');location.reload();}));nav.lastElementChild.prepend(icon('logout'));
  const menu=button('',()=>{const open=nav.classList.toggle('open');menu.setAttribute('aria-expanded',String(open));if(open)nav.querySelector('a')?.focus();});menu.append(icon('menu'));menu.id='shell-menu';menu.setAttribute('aria-label','打开导航');menu.setAttribute('aria-expanded','false');menu.setAttribute('aria-controls','shell-nav');const main=el('main',{id:'shell-content'});app.append(nav,el('div',{class:'shell-main'},[el('header',{class:'shell-bar'},[menu,el('img',{src:'/ui/lovestgateway.svg',width:'26',height:'26',alt:''}),el('strong',{text:'LovestGateWay'})]),el('p',{id:'shell-status',role:'status',hidden:''}),main]));show();clearInterval(timer);timer=setInterval(runtime,5000);
}
function login(reauth=false){if(reauth&&document.getElementById('reauth-panel'))return;let panel;const password=el('input',{type:'password',autocomplete:'current-password',required:'',placeholder:'管理密码','aria-label':'管理密码'}),status=el('p',{role:'status'}),submit=el('button',{class:'btn btn-brand',type:'submit',text:'登录'}),form=el('form',{class:'shell-login glass'},[el('img',{src:'/ui/lovestgateway.svg',width:'54',height:'54',alt:''}),el('h1',{text:'LovestGateWay'}),el('label',{},[el('span',{text:'管理密码'}),password]),submit,status]);form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;status.textContent='正在验证…';const secret=sha256(password.value);try{const response=await fetch('/sniffbox/info',{headers:{Authorization:'Bearer '+secret},signal:AbortSignal.timeout(15000)});if(!response.ok)throw Error(response.status===401?'管理密码不正确。':'暂时无法连接网关。');info=await response.json();storeSecret(secret);authenticated=true;if(reauth){authenticateRuntime(runtimeHost,password.value);panel.remove();document.getElementById('shell-status').hidden=true;await runtime();diagnostics?.refresh();overview?.refresh();proxies?.refresh();rules?.refresh();}else shell();password.value='';}catch(error){status.textContent=error.name==='TimeoutError'?'连接超时，请重试。':error.message;}finally{submit.disabled=false;}};if(reauth){panel=el('section',{id:'reauth-panel','aria-label':'重新登录',class:'shell-reauth'},[form,button('返回编辑',()=>{panel.remove();expired();})]);const region=document.getElementById('shell-status');region.hidden=false;region.replaceChildren(panel);}else app.replaceChildren(form);password.focus();}
document.addEventListener('click',event=>{const link=event.target.closest('#runtime-panel a[href]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey)return;const url=new URL(link.href,location.href);if(url.origin!==location.origin||!routes.some(row=>'#/'+row[0]===url.hash))return;event.preventDefault();event.stopImmediatePropagation();navigate(url.hash);},true);
window.addEventListener('hashchange',()=>{if(!recoveringHistory&&currentHash()!==route)show();});
window.addEventListener('popstate',event=>{
  if(recoveringHistory){event.stopImmediatePropagation();recoveringHistory=false;return;}
  const target=event.state?.ppgwIndex;
  if(route&&currentHash()!==route&&!allowed('#/'+currentHash())){
    event.stopImmediatePropagation();
    if(Number.isInteger(target)&&target!==navigationIndex){recoveringHistory=true;history.go(navigationIndex-target);}
    else history.replaceState({...history.state,ppgwIndex:navigationIndex},'','#/'+route);
    return;
  }
  if(Number.isInteger(target))navigationIndex=target;
  if(['overview','proxies','rules','settings','subscriptions','diagnostics'].includes(currentHash()))event.stopImmediatePropagation();
  show(true);
});
window.addEventListener('storage',event=>{if(event.key?.startsWith('ppgw-'))applyTheme();if(event.key==='PaoPaoGateWay')runtime();});window.addEventListener('beforeunload',event=>{if((gateway||diagnostics)&&!allowed(null,'leave')){event.preventDefault();event.returnValue='';}});window.addEventListener('keydown',event=>{if(event.key==='Escape'){document.getElementById('shell-nav')?.classList.remove('open');document.getElementById('shell-menu')?.setAttribute('aria-expanded','false');document.getElementById('shell-menu')?.focus();}});
if(localStorage.getItem('ppgw-theme')==='custom')localStorage.setItem('ppgw-theme','system');
localStorage.removeItem('ppgw-theme-base');localStorage.removeItem('ppgw-custom-colors');
systemTheme.addEventListener('change',applyTheme);
applyTheme();if(storedSecret()&&await runtime()){authenticated=true;shell();}else login();
