import {modeControl} from './mode-control.js';
import {icon} from './icons.js';
import {formatBytes} from './format.js';
import {snapshot,preferences,groups,isGroup,automatic,lockable,rejected,segment,sorts,delay,state,nodeLabel,members,testUrl,testPath,testPlan,usage,informationNode} from './proxy-model.js';

const make=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
const button=(label,callback)=>{const node=make('button',label,'btn');node.type='button';node.addEventListener('click',callback);return node;};
const label=(text,input)=>{const node=make('label');node.append(make('span',text),input);return node;};
const typeLabel=node=>node?.smart?'智能测速':({Selector:'手动选择',URLTest:'测速模式',Fallback:'故障转移',LoadBalance:'负载均衡',Relay:'中继',Direct:'直连',Reject:'拒绝',RejectDrop:'丢弃'})[node?.type]||node?.type||'未知';
const filters=new Map();
const filterFor=name=>{if(!filters.has(name))filters.set(name,{query:'',region:'',status:'',sort:'',url:''});return filters.get(name);};
const time=value=>{const date=new Date(typeof value==='number'?value*1000:value);return Number.isFinite(date.getTime())?date.toLocaleString('zh-CN'):'—';};

export function mountProxies(host,{initialInfo,onUnauthorized=()=>{}}={}){
  let prefs;try{prefs=preferences(JSON.parse(localStorage.getItem('ppgw-proxy-settings')||'{}'));}catch{prefs=preferences({});}
  const save=()=>{try{localStorage.setItem('ppgw-proxy-settings',JSON.stringify(prefs));}catch{}};
  let data=snapshot({}),disposed=false,paused=false,busy=false,testing=false,testController=null,reading=false,resumeRequested=false,timer=null,readController=null,sequence=0,etag='',detailsVersion=0,detailName='',detailGroup='',loaded=false;
  const controllers=new Set(),views=new Map(),providerViews=new Map();
  const page=make('section',undefined,'proxies-page');page.id='proxies-page';
  const heading=make('header',undefined,'proxies-head'),title=make('h1','代理'),summary=make('span','正在读取…','proxies-summary');
  const modes=modeControl(next=>perform('切换模式',async()=>{await request('/configs',{method:'PATCH',body:{mode:next}});data.mode=next;if(next==='global')prefs.gcollapse.GLOBAL=false;save();})),modeButton=modes.root;
  const refreshButton=button('刷新',()=>refresh()),settingsButton=button('页面设置',()=>{settings.hidden=!settings.hidden;settingsButton.setAttribute('aria-expanded',String(!settings.hidden));});
  refreshButton.prepend(icon('history'));settingsButton.prepend(icon('settings'));settingsButton.setAttribute('aria-expanded','false');settingsButton.setAttribute('aria-controls','proxy-preferences');
  heading.append(title,summary,modeButton,refreshButton,settingsButton);
  const status=make('p',undefined,'proxies-status');status.setAttribute('role','status');status.hidden=true;
  const modeNotice=make('p','当前为 GLOBAL 全局模式，仅 GLOBAL 组生效。','proxies-notice');modeNotice.hidden=true;
  const settings=make('section',undefined,'glass proxy-preferences');settings.id='proxy-preferences';settings.hidden=true;settings.setAttribute('aria-label','代理页面设置');
  const input=make('input');input.type='url';input.placeholder='留空使用网关默认 test_node_url';input.value=prefs.testUrl;input.addEventListener('input',()=>{prefs.testUrl=input.value;save();updateUrls();});settings.append(label('测速 URL',input));
  const globalSort=make('select');for(const [value,text]of sorts.filter(([key])=>key!=='scoreAsc')){const option=make('option',text);option.value=value;globalSort.append(option);}globalSort.value=prefs.sort;globalSort.addEventListener('change',()=>{prefs.sort=globalSort.value;save();render();});settings.append(label('组内排序',globalSort));
  for(const [key,text]of [['optimize','优化节点名标签显示'],['hideUnavail','隐藏不可用代理'],['hideInfoNodes','隐藏疑似订阅说明节点（仅影响展示）'],['autoClose','切换后关闭旧连接'],['showUdp','显示 UDP 标记']]){const check=make('input');check.type='checkbox';check.checked=prefs[key];check.dataset.preference=key;check.addEventListener('change',()=>{prefs[key]=check.checked;save();render();});settings.append(label(text,check));}
  const curve=make('input');curve.type='range';curve.min='0';curve.max='100';curve.value=String(prefs.curve);curve.addEventListener('input',()=>{prefs.curve=Number(curve.value);save();render();});settings.append(label('延迟曲线背景（100 为关闭）',curve));
  const toolbar=make('div',undefined,'proxies-actions'),testAll=button('全部测速',()=>{if(testing)testController?.abort();else runTests(()=>testPlan(data,prefs),'全部测速');}),updateAll=button('更新所有订阅',()=>perform('更新所有订阅',updateProviders));
  testAll.dataset.action='test-all';updateAll.dataset.action='update-all';toolbar.append(testAll,updateAll);
  const information=make('p',undefined,'proxies-notice');information.hidden=true;
  const groupHost=make('div',undefined,'proxy-groups'),providerHost=make('section',undefined,'proxy-providers'),usageHost=make('section',undefined,'proxy-usages');
  const empty=make('p','暂无可用的节点组。','glass proxies-empty');empty.hidden=true;
  const dialog=make('dialog',undefined,'proxy-dialog');dialog.setAttribute('aria-label','节点详情');
  const dialogHead=make('header'),dialogTitle=make('h2','节点详情'),close=button('关闭',()=>dialog.close()),dialogBody=make('div',undefined,'proxy-detail-body');dialogHead.append(dialogTitle,close);dialog.append(dialogHead,dialogBody);dialog.addEventListener('close',()=>{detailsVersion++;detailName='';});
  let lockTarget=null;
  const lockMenu=make('div',undefined,'proxy-lock-menu');lockMenu.setAttribute('popover','auto');lockMenu.setAttribute('role','menu');lockMenu.setAttribute('aria-label','节点锁定操作');
  const lockText=make('p'),lockConfirm=button('锁定此节点',()=>{const target=lockTarget;lockMenu.hidePopover();if(target&&data.proxies[target.group])fixed(target.group,target.name);}),lockCancel=button('取消',()=>lockMenu.hidePopover());
  for(const control of [lockConfirm,lockCancel])control.setAttribute('role','menuitem');lockMenu.append(lockText,lockConfirm,lockCancel);
  lockMenu.addEventListener('toggle',event=>{if(event.newState==='closed')lockTarget=null;});
  lockMenu.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();(document.activeElement===lockConfirm?lockCancel:lockConfirm).focus();}});
  function openLock(group,name,anchor){
    if(busy||paused||disposed||!lockable(data.proxies[group]))return;
    const unlocking=data.proxies[group].fixed===name;
    if(typeof lockMenu.showPopover!=='function'){if(confirm((unlocking?'解除锁定：':'锁定节点：')+name+'？'))fixed(group,name);return;}
    lockTarget={group,name};lockText.textContent=group+' · '+name;lockConfirm.textContent=unlocking?'解锁此节点':'锁定此节点';
    const bounds=anchor.getBoundingClientRect();lockMenu.style.left=Math.max(12,Math.min(bounds.left,innerWidth-280))+'px';lockMenu.style.top=Math.max(12,Math.min(bounds.bottom,innerHeight-160))+'px';lockMenu.showPopover();lockConfirm.focus();
  }
  page.append(heading,status,modeNotice,settings,toolbar,information,groupHost,empty,providerHost,usageHost,dialog,lockMenu);host.replaceChildren(page);
  function message(text){if(disposed)return;status.textContent=text;status.hidden=!text;}
  function authorized(){paused=true;testController?.abort();if(!disposed)onUnauthorized();}
  async function request(url,{method='GET',body,signal,tag,allowNotModified=false}={}){
    if(disposed)throw new DOMException('页面已离开。','AbortError');
    const controller=new AbortController();controllers.add(controller);const cancel=()=>controller.abort();signal?.addEventListener('abort',cancel,{once:true});if(signal?.aborted)controller.abort();
    const timeout=setTimeout(cancel,12000);
    try{
      const response=await fetch(url,{method,headers:{...(body!==undefined?{'Content-Type':'application/json'}:{}),...(tag?{'If-None-Match':tag}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{}),signal:controller.signal,cache:'no-store'});
      if(response.status===401){authorized();const error=Error('登录已失效，请重新登录后读取最新状态。');error.unauthorized=true;throw error;}
      if(allowNotModified&&response.status===304)return {unchanged:true};
      if(!response.ok){const error=Error('请求失败（HTTP '+response.status+'）。');error.status=response.status;throw error;}
      if(response.status===204)return null;
      const text=await response.text();return {value:text?JSON.parse(text):null,etag:response.headers.get('ETag')||''};
    }finally{clearTimeout(timeout);signal?.removeEventListener('abort',cancel);controllers.delete(controller);}
  }
  function actionState(){
    for(const control of page.querySelectorAll('[data-action]'))control.disabled=busy||!loaded||paused||control.dataset.unavailable==='true'||testing&&/test/.test(control.dataset.action);
    modes.update(data.mode,busy||!loaded||paused||!['global','rule','direct'].includes(data.mode));
    updateAll.disabled||=!externalProviders().length;refreshButton.disabled=busy||reading;
    testAll.textContent=testing?'停止测速':'全部测速';
    testAll.disabled=testing?false:testAll.disabled||!groups(data,false).length&&!Object.keys(data.providers).length;
  }
  function schedule(ms=5000){clearTimeout(timer);if(!disposed&&!paused&&!document.hidden)timer=setTimeout(()=>refresh(false),ms);}
  async function refresh(explicit=true){
    if(disposed||document.hidden)return;
    if(busy){if(explicit)resumeRequested=true;return;}
    if(!explicit&&(paused||reading))return;
    paused=false;clearTimeout(timer);readController?.abort();const current=++sequence,controller=new AbortController();readController=controller;reading=true;actionState();
    try{
      if(!loaded){const first=await request('/sniffbox/proxies',{signal:controller.signal});if(disposed||current!==sequence)return;accept(first.value);}
      const fresh=await request('/sniffbox/proxies?fresh=1',{signal:controller.signal,tag:etag,allowNotModified:true});
      if(disposed||current!==sequence)return;
      if(!fresh.unchanged){accept(fresh.value);etag=fresh.etag;}
      if(explicit||status.dataset.readError==='true'){message('');delete status.dataset.readError;}
    }catch(error){if(disposed||current!==sequence)return;message(error.name==='AbortError'?'状态读取超时，稍后自动重试。':error.message);status.dataset.readError='true';}
    finally{if(!disposed&&current===sequence){reading=false;actionState();schedule();}}
  }
  function accept(value){
    if(!value||typeof value!=='object'||Array.isArray(value)||!value.proxies||typeof value.proxies!=='object'||Array.isArray(value.proxies))throw Error('代理状态暂不可用，稍后重试。');
    data=snapshot(value);loaded=true;render();
  }
  async function perform(description,work){
    if(disposed||busy||paused||!loaded)return;
    busy=true;clearTimeout(timer);sequence++;readController?.abort();reading=false;actionState();message(description+'中…');
    try{const result=await work();if(disposed)return;message(typeof result==='string'?result:description+'完成。');}
    catch(error){if(disposed)return;message(description+'未完成：'+(error.name==='AbortError'?'响应未确认，请刷新状态后再决定是否重试。':error.message));}
    finally{busy=false;if(!disposed){render();if(resumeRequested){resumeRequested=false;refresh();}else schedule(3200);}}
  }
  async function closeOld(group,node){
    if(!prefs.autoClose)return '';
    let result;try{result=await request('/connections');}catch{return '旧连接未能读取，请重新核对。';}
    if(!Array.isArray(result.value?.connections)&&result.value?.connections!==null)return '旧连接响应无效，请重新核对。';
    const rows=(result.value.connections||[]).filter(connection=>typeof connection.id==='string'&&Array.isArray(connection.chains)&&connection.chains.includes(group)&&!connection.chains.includes(node));
    let index=0,closed=0,stopped=false;
    await Promise.all(Array.from({length:Math.min(8,rows.length)},async()=>{
      while(index<rows.length&&!disposed&&!paused&&!stopped){const connection=rows[index++];try{await request('/connections/'+segment(connection.id),{method:'DELETE'});closed++;}catch(error){if(error.unauthorized||error.status===403)stopped=true;}}
    }));
    return '已关闭 '+closed+' 条旧连接，'+(rows.length-closed)+' 条未关闭。'+(paused||stopped?'后续关闭已停止，请核对访问权限。':'');
  }
  async function updateProviders(){
    let succeeded=0,stopped=false;const failed=[];
    for(const name of externalProviders()){
      if(disposed||paused)break;
      try{await request('/providers/proxies/'+segment(name),{method:'PUT'});succeeded++;}
      catch(error){failed.push(name);if(error.unauthorized||error.status===403){stopped=true;break;}}
    }
    return '订阅更新'+(paused||stopped?'已停止':'完成')+'：成功 '+succeeded+' 个，失败 '+failed.length+' 个'+(failed.length?'（'+failed.join('、')+'）':'')+'。';
  }
  async function select(group,node){
    const target=data.proxies[group];if(target?.type!=='Selector'||target.smart||target.now===node)return;
    await perform('切换节点',async()=>{await request('/proxies/'+segment(group),{method:'PUT',body:{name:node}});if(disposed)return;target.now=node;const result=await closeOld(group,node);return '切换节点完成。'+result;});
  }
  async function fixed(group,node){
    const target=data.proxies[group];if(!lockable(target))return;
    await perform(target.fixed===node?'解除锁定':'锁定节点',async()=>{
      const unlock=target.fixed===node;
      if(target.smart)await request('/sniffbox/smart/fixed',{method:'POST',body:{group,node:unlock?null:node}});
      else await request('/proxies/'+segment(group),{method:unlock?'DELETE':'PUT',...(unlock?{}:{body:{name:node}})});
      if(disposed)return;target.fixed=unlock?'':node;if(!unlock){target.now=node;const result=await closeOld(group,node);return '锁定节点完成。'+result;}
    });
  }
  async function runTests(source,description='节点测速'){
    if(testing||busy||paused||disposed||!loaded)return false;
    let plans;try{plans=typeof source==='function'?source():source;}catch(error){message(description+'未完成：'+error.message);return false;}
    if(!plans.length){message('当前筛选下没有可测速节点。');return false;}
    const controller=new AbortController();testController=controller;testing=true;actionState();
    let index=0,finished=0,failures=0,permission=false;
    message('正在测速 0 / '+plans.length+'。');
    try{
      await Promise.all(Array.from({length:Math.min(plans.length,8)},async()=>{
        while(index<plans.length&&!disposed&&!paused&&!controller.signal.aborted){
          const {name,url}=plans[index++];
          try{
            const result=await request(testPath(data,name,url),{signal:controller.signal}),latency=result.value?.delay;
            if(controller.signal.aborted||disposed)break;
            if(typeof latency!=='number'||!Number.isFinite(latency)||latency<=0)throw Error('测速超时。');
            if(data.proxies[name]){const node=data.proxies[name];node.delay=latency;node.alive=true;node.extra={...node.extra,[url]:latency};node.hist=[...(node.hist||[]).slice(-7),latency];}
          }catch(error){
            if(error.unauthorized||error.status===403){permission=true;controller.abort();break;}
            if(controller.signal.aborted||disposed)break;
            failures++;if(data.proxies[name]){const node=data.proxies[name];node.delay=0;node.alive=false;node.extra={...node.extra,[url]:0};}
          }
          finished++;message('正在测速 '+finished+' / '+plans.length+'，失败 '+failures+'。');
        }
      }));
    }finally{
      testing=false;if(testController===controller)testController=null;
      if(!disposed){render();message(controller.signal.aborted?description+'已完成 '+finished+' / '+plans.length+'，已停止。'+(permission?'权限或登录状态需要核对。':''):failures?description+'未完成：'+finished+' 项已完成，'+failures+' 项测速失败。':description+'完成。');}
    }
    return !controller.signal.aborted;
  }
  async function testGroup(group){
    if(testing||busy||paused||disposed)return;
    try{
      const target=data.proxies[group],filter=filterFor(group),url=testUrl(data,group,prefs,filter.url),names=members(data,group,filter,{...prefs,hideInfoNodes:false}).filter(name=>!rejected(name));
      if(!names.length){message('当前筛选下没有可测速节点。');return;}
      if(automatic(target)&&target.fixed){
        let unlocked=false;
        await perform('解除锁定',async()=>{
          if(target.smart)await request('/sniffbox/smart/fixed',{method:'POST',body:{group,node:null}});
          else await request('/proxies/'+segment(group),{method:'DELETE'});
          if(!disposed){target.fixed='';unlocked=true;}
        });
        if(!unlocked)return;
      }
      await runTests(names.map(name=>({name,url})),'组测速');
    }catch(error){message('组测速未完成：'+error.message);}
  }
  function externalProviders(){return Object.keys(data.providers).filter(name=>name!=='default'&&data.providers[name].vehicleType!=='Compatible');}
  function fieldSelect(text,choices,value,change){const select=make('select');for(const [key,name]of choices){const option=make('option',name);option.value=key;select.append(option);}select.value=value;select.addEventListener('change',()=>change(select.value));return {control:select,node:label(text,select)};}
  function createGroup(name){
    const node=data.proxies[name],filter=filterFor(name),section=make('details',undefined,'glass proxy-group');section.dataset.group=name;
    section.open=Object.hasOwn(prefs.gcollapse,name)?!prefs.gcollapse[name]:node.type==='Selector'&&name!=='GLOBAL'||data.mode==='global'&&name==='GLOBAL';
    const head=make('summary'),nameNode=make('strong',name),kind=make('span',undefined,'proxy-type'),now=make('span',undefined,'proxy-current'),count=make('small');head.append(nameNode,kind,now,count);
    const body=make('div',undefined,'proxy-group-body'),tools=make('div',undefined,'proxy-group-tools'),search=make('input');search.type='search';search.placeholder='搜索节点';search.value=filter.query;search.setAttribute('aria-label',name+' 搜索节点');
    search.addEventListener('input',()=>{filter.query=search.value;renderGroup(view);});tools.append(search);
    const statusSelect=fieldSelect('状态',[['','全部状态'],['online','在线'],['offline','离线'],['unknown','未测速']],filter.status,value=>{filter.status=value;renderGroup(view);});tools.append(statusSelect.node);
    const region=fieldSelect('区域',[['','全部区域']],filter.region,value=>{filter.region=value;renderGroup(view);});tools.append(region.node);
    const order=fieldSelect('排序',[['','跟随页面'],...sorts],prefs.gsort[name]||filter.sort,value=>{filter.sort=value;prefs.gsort[name]=value;save();renderGroup(view);});tools.append(order.node);
    const url=make('input');url.type='url';url.placeholder='使用页面测速 URL';url.value=filter.url;url.addEventListener('input',()=>filter.url=url.value);url.setAttribute('aria-label',name+' 测速 URL');
    const test=button('组测速',()=>testGroup(name));test.dataset.action='group-test';const unlock=button('解除锁定',()=>fixed(name,data.proxies[name].fixed));unlock.dataset.action='unlock';
    const info=make('p',undefined,'proxy-group-info'),grid=make('div',undefined,'proxy-node-grid'),more=button('显示更多',()=>{view.limit+=120;renderGroup(view);});tools.append(label('测速 URL',url),test,unlock);body.append(tools,info,grid,more);section.append(head,body);
    const view={name,section,kind,now,count,body,grid,region:region.control,url,unlock,info,more,limit:120,cards:new Map()};
    section.addEventListener('toggle',()=>{if(disposed)return;prefs.gcollapse[name]=!section.open;save();if(section.open)renderGroup(view);});return view;
  }
  function updateUrls(){for(const view of views.values()){const node=data.proxies[view.name];view.url.readOnly=automatic(node);view.url.value=automatic(node)?node.testUrl||prefs.testUrl||data.testUrl:filterFor(view.name).url;view.url.placeholder=prefs.testUrl||data.testUrl||'使用网关默认 URL';view.url.title=automatic(node)?'该节点组模式自带只读 URL':'';}}
  function cardFor(view,name){
    const row=make('article',undefined,'proxy-node');row.dataset.node=name;const choice=button('',event=>{if(!event.defaultPrevented)select(view.name,name);});choice.className='proxy-node-choice';choice.dataset.action='select';choice.setAttribute('aria-label','选择 '+name);
    const display=make('span',undefined,'proxy-node-name'),latency=make('span',undefined,'proxy-latency'),badges=make('span',undefined,'proxy-node-tags');choice.append(display,latency);
    const footer=make('div',undefined,'proxy-node-footer'),details=button('详情',()=>openDetails(name,view.name)),test=button('测速',()=>runTests(()=>[{name,url:testUrl(data,view.name,prefs,filterFor(view.name).url)}])),lock=button('锁定',()=>fixed(view.name,name));for(const [control,text,graphic]of [[details,'详情','activity'],[test,'测速','gauge']]){control.setAttribute('aria-label',text);control.title=text;control.classList.add('proxy-node-action');control.replaceChildren(icon(graphic),make('span',text));}
    test.dataset.action='node-test';lock.dataset.action='lock';footer.append(badges,details,test,lock);
    const curve=make('div',undefined,'proxy-sparkline');curve.setAttribute('aria-hidden','true');row.append(curve,choice,footer);
    let press=null,suppress=false;const clear=()=>{clearTimeout(press);press=null;};choice.addEventListener('pointerdown',event=>{if(event.button!==0||!lockable(data.proxies[view.name])||busy||paused)return;suppress=false;press=setTimeout(()=>{suppress=true;openLock(view.name,name,choice);},650);});
    for(const type of ['pointerup','pointercancel','pointerleave'])choice.addEventListener(type,clear);
    choice.addEventListener('click',event=>{if(suppress){event.preventDefault();suppress=false;}},true);
    choice.addEventListener('contextmenu',event=>{if(lockable(data.proxies[view.name])){event.preventDefault();clear();openLock(view.name,name,choice);}});
    return {row,choice,display,latency,badges,lock,test,curve,clear};
  }
  function renderGroup(view){
    const {name,section}=view,node=data.proxies[name],filter=filterFor(name);if(!node)return;
    view.kind.textContent=typeLabel(node);view.now.textContent=node.now?'当前：'+node.now:'';view.count.textContent=node.all.length+' 个节点';
    view.unlock.hidden=!node.fixed;view.unlock.title=node.fixed||'';
    view.info.textContent=node.fixed?'已锁定 '+node.fixed+(node.now!==node.fixed?'；当前选择已临时按策略切换。':'；组测速会解除锁定。'):node.type==='Selector'&&!node.smart?'点击节点切换。':lockable(node)?'由核心自动选择；可长按节点或使用锁定按钮。':'由核心决定节点，支持测速和查看详情。';
    const codes=[...new Set(node.all.map(name=>nodeLabel(name).code).filter(Boolean))].sort();const regionValue=view.region.value||filter.region;
    view.region.replaceChildren(...[['','全部区域'],...codes.map(code=>[code,code])].map(([value,text])=>{const option=make('option',text);option.value=value;return option;}));view.region.value=regionValue;
    if(!codes.includes(filter.region)&&filter.region){filter.region='';view.region.value='';}
    if(!section.open)return;
    const matches=members(data,name,filter,prefs),visible=matches.slice(0,view.limit),keep=new Set(visible);view.more.hidden=matches.length<=visible.length;view.more.textContent='显示更多（剩余 '+Math.max(0,matches.length-visible.length)+' 个）';
    for(const [key,card]of view.cards)if(!keep.has(key)){card.clear();card.row.remove();view.cards.delete(key);}
    for(const [index,key]of visible.entries()){let card=view.cards.get(key);if(!card){card=cardFor(view,key);view.cards.set(key,card);}const child=data.proxies[key],info=nodeLabel(key,prefs.optimize),latency=delay(data,key,name),online=state(data,key,name),score=node.scores?.[key]?.score;
      card.display.textContent=[info.flag,info.display].filter(Boolean).join(' ');card.display.title=key;card.latency.textContent=latency===null?'—':latency===0?'超时':latency+' ms';card.row.dataset.state=online;card.row.classList.toggle('selected',node.now===key);card.choice.setAttribute('aria-pressed',String(node.now===key));card.choice.title=key;
      card.choice.disabled=busy||paused||!child||node.type!=='Selector'||node.smart===true;card.choice.dataset.action=node.type==='Selector'&&!node.smart?'select':'inspect';
      // Automatic groups keep the long-press target available without a selection mutation.
      if(lockable(node)){card.choice.disabled=busy||paused;card.choice.setAttribute('aria-label','自动节点 '+key);}
      card.choice.dataset.unavailable=String(!child||node.type!=='Selector'&&!lockable(node));
      card.badges.textContent=[typeLabel(child),info.tag,info.dns?'@'+info.dns:'',prefs.showUdp&&child?.udp?'UDP':'',typeof score==='number'?'打分 '+score:''].filter(Boolean).join(' · ');
      card.lock.hidden=!lockable(node);card.lock.textContent=node.fixed===key?'解锁':'锁定';card.lock.setAttribute('aria-pressed',String(node.fixed===key));card.test.hidden=rejected(key);
      const hist=node.smart?node.scores?.[key]?.samples:child?.hist||child?.history?.map(item=>item.delay);card.curve.replaceChildren();card.curve.hidden=prefs.curve>=100||!Array.isArray(hist)||hist.length<2;
      if(!card.curve.hidden){const samples=hist.slice(-8),valid=samples.filter(value=>typeof value==='number'&&Number.isFinite(value)&&value>0),max=Math.max(1,...valid),svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),line=document.createElementNS(svg.namespaceURI,'polyline');svg.setAttribute('viewBox','0 0 100 40');svg.setAttribute('preserveAspectRatio','none');line.setAttribute('points',samples.map((value,index)=>index/(samples.length-1)*100+','+(typeof value==='number'&&value>0?4+(1-value/max)*32:39)).join(' '));svg.append(line);card.curve.append(svg);card.curve.style.opacity=String((100-prefs.curve)/100);}
      if(view.grid.children[index]!==card.row)view.grid.insertBefore(card.row,view.grid.children[index]||null);
    }
    let noResults=view.body.querySelector('.proxy-no-results');if(!noResults){noResults=make('p','当前筛选下没有节点。','proxy-no-results');view.body.append(noResults);}noResults.hidden=visible.length>0;
  }
  function usageCard(name,raw){
    const value=usage(raw),card=make('article',undefined,'glass proxy-usage');card.append(make('h3',name));
    for(const [key,text]of [['used','已用'],['total','总量'],['left','剩余'],['up','上传'],['down','下载']])card.append(make('span',text+' '+formatBytes(value[key])));
    card.append(make('span',value.expire?'到期 '+time(value.expire):'未提供到期时间'));if(raw.at)card.append(make('small','更新于 '+time(raw.at)));
    if(value.total>0&&value.used!==null){const meter=make('meter');meter.min=0;meter.max=value.total;meter.value=Math.min(value.total,value.used);meter.setAttribute('aria-label',name+' 订阅用量');card.append(meter);}return card;
  }
  function render(){
    if(disposed)return;
    const infoNames=Object.keys(data.proxies).filter(name=>!isGroup(data.proxies[name])&&informationNode(name));information.hidden=!infoNames.length;information.textContent=infoNames.length?'疑似订阅说明节点 '+infoNames.length+' 个：'+infoNames.join('、')+'。按名称识别，仅供参考；可在页面设置中隐藏，不修改配置或测速范围。':'';
    const ordered=groups(data);summary.textContent=ordered.length+' 个节点组';modeNotice.hidden=data.mode!=='global';
    const keep=new Set(ordered);for(const [name,view]of views)if(!keep.has(name)){for(const card of view.cards.values())card.clear();view.section.remove();views.delete(name);}
    for(const [index,name]of ordered.entries()){let view=views.get(name);if(!view){view=createGroup(name);views.set(name,view);}if(groupHost.children[index]!==view.section)groupHost.insertBefore(view.section,groupHost.children[index]||null);if(Object.hasOwn(prefs.gcollapse,name))view.section.open=!prefs.gcollapse[name];renderGroup(view);}
    updateUrls();empty.hidden=!loaded||ordered.length>0;
    const providers=externalProviders();providerHost.hidden=!providers.length;
    for(const [name,view]of providerViews)if(!providers.includes(name)){view.card.remove();providerViews.delete(name);}
    if(providers.length&&!providerHost.querySelector('h2'))providerHost.prepend(make('h2','代理集合'));
    for(const name of providers){const provider=data.providers[name];let view=providerViews.get(name);
      if(!view){const card=make('article',undefined,'glass proxy-provider'),kind=make('span'),count=make('span'),updated=make('small');card.dataset.provider=name;card.append(make('h3',name),kind,count,updated);
        const update=button('更新订阅',()=>perform('更新订阅',async()=>request('/providers/proxies/'+segment(name),{method:'PUT'}))),health=button('批量测速',()=>runTests(()=>{const url=testUrl(data,'',prefs);return data.providers[name].proxies.filter(node=>!rejected(node)).map(node=>({name:node,url}));},'代理集合测速'));update.dataset.action='provider-update';health.dataset.action='provider-test';card.append(update,health);providerHost.append(card);view={card,kind,count,updated};providerViews.set(name,view);}
      view.kind.textContent=provider.vehicleType||'代理集合';view.count.textContent=provider.proxies.length+' 个节点';view.updated.textContent=provider.updatedAt?'更新于 '+time(provider.updatedAt):'暂无更新时间';}
    usageHost.replaceChildren();const subscriptions=[...data.subs,...providers.filter(name=>data.providers[name].subscriptionInfo).map(name=>({name,...data.providers[name].subscriptionInfo}))];usageHost.hidden=!subscriptions.length;
    if(subscriptions.length){usageHost.append(make('h2','订阅用量'));for(const sub of subscriptions)usageHost.append(usageCard(sub.name,sub));}
    actionState();
  }
  async function openDetails(name,group){
    const current=++detailsVersion;detailName=name;detailGroup=group;dialogTitle.textContent=name;dialogBody.replaceChildren(make('p','正在读取节点详情…'));if(!dialog.open)dialog.showModal();
    try{
      const [detail,traffic]=await Promise.all([request('/sniffbox/proxy?name='+encodeURIComponent(name)),request('/sniffbox/nodes').catch(error=>{if(error.unauthorized)throw error;return null;})]);
      if(disposed||current!==detailsVersion)return;
      const node=detail.value||data.proxies[name]||{},rows=[['协议',typeLabel(node)],['UDP',node.udp===true?'支持':node.udp===false?'不支持':'—'],['服务器',typeof node.server==='string'?node.server:'—'],['端口',typeof node.port==='number'?String(node.port):'—'],['状态',({online:'在线',offline:'离线',unknown:'未测速'})[state(data,name,group)]]];
      const stats=Array.isArray(traffic?.value?.nodes)?traffic.value.nodes.find(item=>item.node===name):null;if(stats)rows.push(['节点流量（分布累计）',formatBytes(stats.up)+' ↑ / '+formatBytes(stats.down)+' ↓ / '+formatBytes(stats.total)]);
      const list=make('dl');for(const [key,value]of rows)list.append(make('dt',key),make('dd',value));
      const history=Array.isArray(node.history)?node.history.slice(-8).map(item=>item.delay):data.proxies[name]?.hist||[],historyNode=make('p','延迟历史：'+(history.length?history.map(value=>value>0?value+' ms':'超时').join(' → '):'暂无测速历史'));
      const scores=data.proxies[group]?.scores?.[name],scoreNode=make('p',scores?'智能测速打分：'+(scores.score??'—')+'；最近样本：'+(scores.samples||[]).map(value=>value===null?'失败':value+' ms').join(' → '):'');
      const custom=make('input');custom.type='url';custom.value=testUrl(data,group,prefs,filterFor(group).url);custom.setAttribute('aria-label','节点详情测速 URL');const test=button('测速',async()=>{const completed=await runTests(()=>[{name,url:testUrl(data,'',{...prefs,testUrl:custom.value})}]);if(completed&&dialog.open&&detailName===name)openDetails(name,detailGroup);});test.dataset.action='detail-test';test.hidden=rejected(name);
      dialogBody.replaceChildren(list,historyNode,scoreNode,label('测速 URL',custom),test);actionState();
    }catch(error){if(!disposed&&current===detailsVersion)dialogBody.replaceChildren(make('p',error.message));}
  }
  const visibility=()=>{if(document.hidden){clearTimeout(timer);sequence++;readController?.abort();reading=false;}else if(!disposed&&!paused)refresh(false);};
  const focus=()=>{if(!disposed&&!paused&&!busy)refresh(false);};document.addEventListener('visibilitychange',visibility);window.addEventListener('focus',focus);
  if(initialInfo&&!['yaml','suburl'].includes(initialInfo.mode)){message('当前出站模式不使用 Mihomo 代理组，请在网关设置中选择配置来源。');summary.textContent='当前模式无代理组';toolbar.hidden=true;modeButton.hidden=true;refreshButton.hidden=true;}else if(!document.hidden)refresh();
  return {refresh,dispose(){disposed=true;testController?.abort();sequence++;detailsVersion++;clearTimeout(timer);for(const controller of controllers)controller.abort();for(const view of views.values())for(const card of view.cards.values())card.clear();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('focus',focus);if(dialog.open)dialog.close();if(lockMenu.matches(':popover-open'))lockMenu.hidePopover();host.replaceChildren();}};
}
