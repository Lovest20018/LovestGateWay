import {icon} from './icons.js';
import * as urlPrivacy from './url-privacy.js';
import {restoreSession,loginSession,logoutSession} from './auth.js';
import {pendingRecoveryJob} from './recovery-job.js';
const loaders={
  overview:()=>import('./overview.js'),proxies:()=>import('./proxies.js'),rules:()=>import('./rules.js'),
  connections:()=>import('./connections.js'),traffic:()=>import('./traffic.js'),distribution:()=>import('./distribution.js'),
  logs:()=>import('./logs.js'),region:()=>import('./region.js'),diagnostics:()=>import('./diagnostics.js'),
  settings:()=>import('./editor.js'),subscriptions:()=>import('./editor.js'),
};
const monitorMount={connections:'mountConnections',traffic:'mountTraffic',distribution:'mountDistribution',logs:'mountLogs',region:'mountRegion'};
window.ppgwUrlPrivacy=urlPrivacy;
let monitor=null,monitorHost=null,monitorRoute='';
const app=document.getElementById('app'),routes=[['overview','概览'],['proxies','代理'],['rules','规则'],['connections','连接'],['traffic','流量'],['distribution','分布'],['logs','日志'],['region','区域'],['subscriptions','订阅'],['settings','设置'],['diagnostics','诊断']];
const navigationGroups=[['网络',['overview','proxies','rules']],['观测',['connections','traffic','distribution','logs','region']],['管理',['subscriptions','settings','diagnostics']]];
let info=null,gateway=null,editor=null,workspace=null,diagnostics=null,diagnosticHost=null,overview=null,overviewHost=null,proxies=null,proxyHost=null,rules=null,rulesHost=null,route='',timer=null,authenticated=false,runtimePaused=false,routeSequence=0;
let navigationIndex=history.state?.ppgwIndex||0,recoveringHistory=false;
history.replaceState({...history.state,ppgwIndex:navigationIndex},'');
const el=(tag,attrs={},children=[])=>{const n=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==='text')n.textContent=v;else if(k.startsWith('on'))n.addEventListener(k.slice(2),v);else n.setAttribute(k,v);}n.append(...children);return n;};
const button=(text,onclick)=>el('button',{type:'button',class:'btn',text,onclick});
const routeIcons={overview:'gauge',proxies:'globe',rules:'filter',connections:'swap',traffic:'history',distribution:'pie',logs:'terminal',region:'radio',subscriptions:'package',settings:'settings',diagnostics:'activity'};
function expired(){monitor?.expire();if(document.getElementById('reauth-panel'))return;const status=document.getElementById('shell-status');if(status){status.hidden=false;status.replaceChildren(document.createTextNode('登录已失效，任务与未保存的编辑仍保留。 '),button('重新登录',()=>login(true)));}}
const systemTheme=matchMedia('(prefers-color-scheme: dark)');
function selectedTheme(){const saved=localStorage.getItem('ppgw-theme');return ['system','light','dark'].includes(saved)?saved:'system';}
function applyTheme(){const selected=selectedTheme(),resolved=selected==='system'?(systemTheme.matches?'dark':'light'):selected;document.documentElement.dataset.theme=resolved;document.documentElement.style.colorScheme=resolved;for(const key of ['--bg','--bg-elev','--text','--nav-bg','--nav-text','--brand','--brand-deep','--brand-text','--brand-soft','--brand-grad'])document.documentElement.style.removeProperty(key);document.querySelectorAll('#shell-appearance button').forEach(choice=>choice.setAttribute('aria-pressed',String(choice.dataset.themeChoice===selected)));
}
function themeControl(){const choices=el('div',{id:'shell-appearance',class:'shell-appearance',role:'group','aria-label':'外观'});for(const[value,label,name,graphic]of [['system','系统','跟随系统','monitor'],['light','亮色','亮色','sun'],['dark','暗色','暗色','moon']]){const choice=button('',()=>{localStorage.setItem('ppgw-theme',value);applyTheme();});choice.setAttribute('aria-label',name);choice.setAttribute('title',name);choice.setAttribute('aria-pressed',String(selectedTheme()===value));choice.dataset.themeChoice=value;choice.append(icon(graphic),el('span',{text:label}));choices.append(choice);}return choices;}
async function runtime(){try{const response=await fetch('/sniffbox/info',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});if(response.ok){info=await response.json();runtimePaused=false;updateNav();return true;}if(response.status===401){runtimePaused=true;expired();}return false;}catch{return false;}}
function scheduleRuntime(){
  clearTimeout(timer);
  if(!authenticated||runtimePaused||document.hidden)return;
  timer=setTimeout(async()=>{if(route!=='overview')await runtime();scheduleRuntime();},30000);
}
document.addEventListener('visibilitychange',()=>{
  clearTimeout(timer);
  if(authenticated&&!runtimePaused&&!document.hidden){if(route!=='overview')runtime();scheduleRuntime();}
});
function updateNav(){document.querySelectorAll('[data-route]').forEach(a=>{const key=a.dataset.route;a.hidden=['proxies','rules'].includes(key)&&!['yaml','suburl'].includes(info?.mode);a.classList.toggle('active',route===key);if(route===key)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});}
const mobileNavigation=matchMedia('(max-width:760px)');
function syncSidebar(){
  const collapsed=!mobileNavigation.matches&&localStorage.getItem('ppgw-nav-collapsed')==='true';
  app.classList.toggle('shell-sidebar-collapsed',collapsed);
  const toggle=document.getElementById('shell-collapse');
  if(toggle){const label=collapsed?'展开侧边栏':'收起侧边栏';toggle.setAttribute('aria-expanded',String(!collapsed));toggle.setAttribute('aria-label',label);toggle.title=label;}
}
function setNavigation(open,restoreFocus=true){
  const nav=document.getElementById('shell-nav'),menu=document.getElementById('shell-menu');
  if(!nav||!menu)return;
  const wasOpen=nav.classList.contains('open');open=Boolean(open&&mobileNavigation.matches);
  nav.classList.toggle('open',open);menu.setAttribute('aria-expanded',String(open));menu.setAttribute('aria-label',open?'收起导航':'打开导航');
  nav.inert=mobileNavigation.matches&&!open;
  document.getElementById('shell-backdrop').hidden=!open;
  document.querySelector('.shell-main').inert=open;
  document.body.classList.toggle('shell-nav-open',open);
  if(open)nav.querySelector('a')?.focus();else if(wasOpen&&restoreFocus)menu.focus();
}
mobileNavigation.addEventListener('change',()=>{syncSidebar();setNavigation(false,false);});
window.addEventListener('keydown',event=>{
  const nav=document.getElementById('shell-nav');
  if(!mobileNavigation.matches||!nav?.classList.contains('open'))return;
  if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();setNavigation(false);}
  if(event.key==='Tab'){
    const items=[...nav.querySelectorAll('a[href],button,input,select')].filter(node=>!node.disabled&&!node.hidden&&node.getClientRects().length);
    const first=items[0],last=items.at(-1);
    if(event.shiftKey&&(document.activeElement===first||!nav.contains(document.activeElement))){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||!nav.contains(document.activeElement))){event.preventDefault();first?.focus();}
  }
},true);
function currentHash(){const key=location.hash.slice(2).split('?')[0];return key==='ppsub'?'subscriptions':routes.some(r=>r[0]===key)?key:'overview';}
function allowed(hash,type='goto'){const next=hash?new URL(hash,location.href):undefined;return (!gateway||gateway.allowNavigation(next,type))&&(!diagnostics||diagnostics.allowNavigation(next,type));}
function navigate(hash,replace=false){
  if(!allowed(hash))return false;
  if(replace)history.replaceState({...history.state,ppgwIndex:navigationIndex},'',hash);
  else history.pushState({...history.state,ppgwIndex:++navigationIndex},'',hash);
  show(true);scrollTo(0,0);return true;
}
async function show(guarded=false){if(!authenticated)return;const next=currentHash(),migrated=['settings','subscriptions'].includes(next);if(!guarded&&route&&next!==route&&!allowed('#/'+next)){history.replaceState(null,'','#/'+route);return;}
  if(/^#\/ppsub(?:\?|$)/.test(location.hash))history.replaceState({...history.state,ppgwIndex:navigationIndex},'',location.hash.replace('#/ppsub','#/subscriptions'));
  route=next;setNavigation(false,false);
  if(monitor&&next!==monitorRoute){monitor.dispose();monitor=null;monitorHost.remove();monitorHost=null;monitorRoute='';}
  if(diagnostics&&next!=='diagnostics'){diagnostics.dispose();diagnostics=null;diagnosticHost.remove();diagnosticHost=null;}
  if(overview&&next!=='overview'){overview.dispose();overview=null;overviewHost.remove();overviewHost=null;}
  if(proxies&&next!=='proxies'){proxies.dispose();proxies=null;proxyHost.remove();proxyHost=null;}
  if(rules&&next!=='rules'){rules.dispose();rules=null;rulesHost.remove();rulesHost=null;}
  if(!migrated&&workspace){editor?.();editor=null;gateway?.();gateway=null;workspace.remove();workspace=null;}
  const turn=++routeSequence;let loadedRoute;
  const loading=document.getElementById('shell-loading');loading.hidden=false;loading.textContent='正在加载页面…';
  try{loadedRoute=await loaders[next]();}catch{
    if(turn===routeSequence&&authenticated){loading.replaceChildren(document.createTextNode('页面加载失败。 '),button('重试',()=>show(true)));}
    return;
  }
  if(turn!==routeSequence||!authenticated)return;
  loading.hidden=true;
  const {mountEditor,mountOverview,mountProxies,mountRules,mountDiagnostics}=loadedRoute;
  if(migrated){if(!workspace){workspace=el('div',{class:'ppgw-workspace page'});const settings=el('div',{class:'ppgw-gateway page'}),host=el('div',{class:'ppgw-subscription-editor',hidden:''});workspace.append(settings,host);document.getElementById('shell-content').append(workspace);gateway=window.ppgwGateway.mount(settings,{editorHost:host,createEditor:()=>{editor ||=mountEditor(host);},navigate,refreshRuntime:async isCurrent=>{if(isCurrent())await runtime();}});}workspace.hidden=false;gateway.refresh();}
  else if(next==='overview'){if(!overview){overviewHost=el('div');document.getElementById('shell-content').append(overviewHost);overview=mountOverview(overviewHost,{initialInfo:info,onUnauthorized:expired,onInfo:value=>{info=value;updateNav();}});}}
  else if(next==='proxies'){if(!proxies){proxyHost=el('div');document.getElementById('shell-content').append(proxyHost);proxies=mountProxies(proxyHost,{initialInfo:info,onUnauthorized:expired});}}
  else if(next==='rules'){if(!rules){rulesHost=el('div');document.getElementById('shell-content').append(rulesHost);rules=mountRules(rulesHost,{initialInfo:info,onUnauthorized:expired});}}
  else if(next==='diagnostics'){if(!diagnostics){diagnosticHost=el('div');document.getElementById('shell-content').append(diagnosticHost);diagnostics=mountDiagnostics(diagnosticHost,{onUnauthorized:expired,onComplete:runtime});}}
  else if(monitorMount[next]){if(!monitor){monitorRoute=next;monitorHost=el('div');document.getElementById('shell-content').append(monitorHost);monitor=loadedRoute[monitorMount[next]](monitorHost,{initialInfo:info,onUnauthorized:expired});}}
  document.title=(routes.find(r=>r[0]===next)?.[1]||'概览')+' · LovestGateWay';updateNav();
}
function shell(){
  if(pendingRecoveryJob())history.replaceState({...history.state,ppgwIndex:navigationIndex},'','#/diagnostics');
  app.replaceChildren();
  const nav=el('nav',{id:'shell-nav','aria-label':'主导航'});
  const brand=el('a',{href:'#/overview',class:'shell-brand','aria-label':'LovestGateWay 概览',title:'LovestGateWay',onclick:e=>{e.preventDefault();navigate('#/overview');}});
  brand.append(el('img',{src:'/ui/lovestgateway.svg',width:'30',height:'30',alt:''}),el('span',{class:'shell-wordmark'},[document.createTextNode('Lovest'),el('b',{text:'GateWay'})]));
  const collapse=button('',()=>{localStorage.setItem('ppgw-nav-collapsed',String(!app.classList.contains('shell-sidebar-collapsed')));syncSidebar();});
  collapse.id='shell-collapse';collapse.setAttribute('aria-controls','shell-nav-links');collapse.append(icon('panel-left'));
  nav.append(el('div',{class:'shell-nav-head'},[brand,collapse]));
  const links=el('div',{id:'shell-nav-links',class:'shell-nav-links'});
  for(const[label,keys]of navigationGroups){
    const group=el('div',{class:'shell-nav-group',role:'group','aria-label':label},[el('p',{class:'shell-nav-group-label',text:label,'aria-hidden':'true'})]);
    for(const key of keys){
      const name=routes.find(row=>row[0]===key)[1];
      const link=el('a',{href:'#/'+key,'data-route':key,class:'shell-nav-link','aria-label':name,title:name,onclick:e=>{if(e.ctrlKey||e.metaKey||e.shiftKey)return;e.preventDefault();navigate('#/'+key);}});
      link.append(icon(routeIcons[key]),el('span',{class:'shell-nav-label',text:name}));group.append(link);
    }
    links.append(group);
  }
  const logout=button('',async()=>{if(!allowed('#/logout'))return;try{await logoutSession();authenticated=false;location.reload();}catch{const status=document.getElementById('shell-status');status.hidden=false;status.textContent='退出未确认，请重试以撤销服务器会话。';}});
  logout.id='shell-logout';logout.setAttribute('aria-label','退出登录');logout.title='退出登录';logout.append(icon('logout'),el('span',{class:'shell-nav-label',text:'退出登录'}));
  nav.append(links,el('div',{class:'shell-nav-footer'},[themeControl(),logout]));
  const menu=button('',()=>setNavigation(!nav.classList.contains('open')));menu.append(icon('menu'));menu.id='shell-menu';menu.setAttribute('aria-label','打开导航');menu.setAttribute('aria-expanded','false');menu.setAttribute('aria-controls','shell-nav');
  const main=el('main',{id:'shell-content'}),backdrop=button('',()=>setNavigation(false));backdrop.id='shell-backdrop';backdrop.hidden=true;backdrop.setAttribute('aria-label','关闭导航');
  app.append(backdrop,nav,el('div',{class:'shell-main'},[el('header',{class:'shell-bar'},[menu,el('img',{src:'/ui/lovestgateway.svg',width:'26',height:'26',alt:''}),el('strong',{text:'LovestGateWay'})]),el('p',{id:'shell-status',role:'status',hidden:''}),el('p',{id:'shell-loading',role:'status',hidden:''}),main]));
  syncSidebar();setNavigation(false,false);show();scheduleRuntime();
}
function login(reauth=false){if(reauth&&document.getElementById('reauth-panel'))return;let panel;const password=el('input',{type:'password',autocomplete:'current-password',required:'',placeholder:'管理密码','aria-label':'管理密码'}),status=el('p',{role:'status'}),submit=el('button',{class:'btn btn-brand',type:'submit',text:'登录'}),form=el('form',{class:'shell-login glass'},[el('img',{src:'/ui/lovestgateway.svg',width:'54',height:'54',alt:''}),el('h1',{text:'LovestGateWay'}),el('label',{},[el('span',{text:'管理密码'}),password]),submit,status]);form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;status.textContent='正在验证…';try{await loginSession(password.value);password.value='';if(!await runtime())throw Error('登录后读取网关状态失败，请重试。');authenticated=true;if(reauth){panel.remove();document.getElementById('shell-status').hidden=true;await runtime();scheduleRuntime();diagnostics?.refresh();overview?.refresh();proxies?.refresh();rules?.refresh();monitor?.refresh();}else shell();password.value='';}catch(error){status.textContent=error.name==='TimeoutError'?'连接超时，请重试。':error.message;}finally{submit.disabled=false;}};if(reauth){panel=el('section',{id:'reauth-panel','aria-label':'重新登录',class:'shell-reauth'},[form,button('返回编辑',()=>{panel.remove();expired();})]);const region=document.getElementById('shell-status');region.hidden=false;region.replaceChildren(panel);}else app.replaceChildren(form);password.focus();}
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
  event.stopImmediatePropagation();
  show(true);
});
window.addEventListener('storage',event=>{if(event.key?.startsWith('ppgw-'))applyTheme();if(event.key==='ppgw-nav-collapsed'||event.key===null)syncSidebar();if(event.key==='PaoPaoGateWay')runtime();});window.addEventListener('beforeunload',event=>{if((gateway||diagnostics)&&!allowed(null,'leave')){event.preventDefault();event.returnValue='';}});
if(localStorage.getItem('ppgw-theme')==='custom')localStorage.setItem('ppgw-theme','system');
localStorage.removeItem('ppgw-theme-base');localStorage.removeItem('ppgw-custom-colors');
systemTheme.addEventListener('change',applyTheme);
applyTheme();try{if(await restoreSession()&&await runtime()){authenticated=true;shell();}else login();}catch{login();}
