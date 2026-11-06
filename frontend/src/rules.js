import {storedSecret} from './auth.js';
import {icon} from './icons.js';
import {snapshot,isGroup,segment,nodeLabel,delay} from './proxy-model.js';
import {rulesSnapshot,providersSnapshot,rulePage,proxyChain,matchLog,testAddress,geoResult} from './rule-model.js';

const make=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
const button=(text,callback)=>{const node=make('button',text,'btn');node.type='button';node.addEventListener('click',callback);return node;};
const field=(name,input)=>{const node=make('label');node.append(make('span',name),input);return node;};
const date=value=>{if(!value)return '—';const parsed=new Date(typeof value==='number'?(value>1e12?value:value*1000):value);return Number.isFinite(parsed.getTime())?parsed.toLocaleString('zh-CN'):'—';};
const groupType=node=>node?.smart?'智能测速':({Selector:'手动选择',URLTest:'测速模式',Fallback:'故障转移',LoadBalance:'负载均衡',Relay:'中继'})[node?.type]||node?.type||'未知';

export function mountRules(host,{initialInfo,onUnauthorized=()=>{}}={}){
  const supported=!initialInfo||['yaml','suburl'].includes(initialInfo.mode);
  let rows=[],providers=[],proxies={},mode='',geo=[],loaded=false,rulesCurrent=false,proxiesCurrent=false,disposed=false,paused=false,busy=false,reading=false,resumeRequested=false,query='',pageNumber=1,located=null,testResult=null,drawerPath=[],drawerLimit=120;
  let pageSize='auto';try{const saved=sessionStorage.getItem('rules_pageSize');if(['auto','20','50','100','all'].includes(saved))pageSize=saved;}catch{}
  let timer=null,sequence=0,readController=null;const controllers=new Set(),rowViews=new Map(),providerViews=new Map();
  const page=make('section',undefined,'rules-page');page.id='rules-page';
  const header=make('header',undefined,'rules-head'),title=make('h1','规则'),count=make('span','正在读取…','rules-count'),search=make('input');search.type='search';search.placeholder='搜索规则、目标或匹配内容';search.setAttribute('aria-label','搜索规则');
  search.addEventListener('input',()=>{query=search.value;pageNumber=1;renderRows();renderProviders();});
  const geoVersion=make('small',undefined,'rules-geo-version'),geoButton=button('手动更新 GEO 数据',()=>{if(!busy&&!paused){confirmText.textContent='数据库每 24 小时会自动尝试更新。手动下载并重新加载 GEO 数据会引起连接中断，确认立即更新吗？';confirmDialog.showModal();}}),modeButton=button('切换全局模式',()=>perform('切换模式',async()=>{const next=mode==='global'?'rule':'global';await request('/configs',{method:'PATCH',body:{mode:next}});mode=next;})),refreshButton=button('刷新',()=>refresh());
  geoButton.dataset.action='geo';modeButton.dataset.action='mode';refreshButton.prepend(icon('history'));modeButton.prepend(icon('globe'));header.append(title,count,search,geoVersion,geoButton,modeButton,refreshButton);
  const status=make('p',undefined,'rules-status');status.setAttribute('role','status');status.hidden=true;const notice=make('p','当前为 GLOBAL 全局模式，规则未生效。','rules-notice');notice.hidden=true;
  const testSection=make('section',undefined,'glass rule-test'),testTitle=make('h2','规则测试'),testForm=make('form'),testInput=make('input');testInput.type='text';testInput.autocomplete='off';testInput.spellcheck=false;testInput.placeholder='输入网址，例如 https://example.com';testInput.setAttribute('aria-label','规则测试网址');
  const testButton=make('button','测试','btn');testButton.type='submit';testButton.dataset.action='test';const clearInput=button('清空',()=>{testInput.value='';testInput.focus();});testForm.append(testInput,clearInput,testButton);
  const testOutput=make('div',undefined,'rule-test-output');testSection.append(testTitle,testForm,testOutput);testForm.addEventListener('submit',event=>{event.preventDefault();runTest();});
  const providerSection=make('details',undefined,'glass rule-providers'),providerHeading=make('summary','规则订阅'),providerActions=make('div',undefined,'rules-provider-actions'),updateAll=button('全部更新',()=>perform('全部更新',async()=>{for(const provider of providers){if(disposed||paused)break;await request('/providers/rules/'+segment(provider.name),{method:'PUT'});}},true)),providerList=make('div',undefined,'rule-provider-list');updateAll.dataset.action='update-all';providerActions.append(updateAll);providerSection.append(providerHeading,providerActions,providerList);
  const tableCard=make('section',undefined,'glass rules-table-card'),tip=make('p','把命中频率高的规则排在更前可以提高匹配效率。','rules-order-tip'),tableWrap=make('div',undefined,'rules-table-wrap');tableWrap.tabIndex=0;tableWrap.setAttribute('aria-label','规则列表，可横向滚动');
  const table=make('table'),thead=make('thead'),headRow=make('tr');for(const name of ['启用','目标代理','类型','匹配内容','数量','命中','未命中','最近命中','最近未命中','优先级']){const th=make('th',name);th.scope='col';headRow.append(th);}thead.append(headRow);const tbody=make('tbody');table.append(thead,tbody);tableWrap.append(table);
  const pagination=make('div',undefined,'rules-pagination'),size=make('select');size.setAttribute('aria-label','每页规则数');for(const value of ['auto','20','50','100','all']){const option=make('option',({auto:'自动',all:'全部'})[value]||value);option.value=value;size.append(option);}size.value=pageSize;size.addEventListener('change',()=>{pageSize=size.value;pageNumber=1;try{sessionStorage.setItem('rules_pageSize',pageSize);}catch{}renderRows();});
  const previous=button('上一页',()=>{pageNumber--;renderRows();}),next=button('下一页',()=>{pageNumber++;renderRows();}),pageCount=make('span');pagination.append(field('每页',size),previous,pageCount,next);tableCard.append(tip,tableWrap,pagination);
  const confirmDialog=make('dialog',undefined,'rules-dialog rules-confirm');confirmDialog.setAttribute('aria-label','确认更新 GEO 数据');const confirmTitle=make('h2','更新 GEO 数据'),confirmText=make('p'),cancel=button('取消',()=>confirmDialog.close()),confirm=button('确认更新',()=>{if(!confirmDialog.open)return;confirmDialog.close();perform('GEO 更新',async()=>{const result=geoResult(await request('/upgrade/geo',{method:'POST',timeout:120000}));geo=result.files;renderGeo();if(!result.complete)throw Error(result.message);return result.message;},true);});confirm.dataset.action='confirm-geo';confirmDialog.append(confirmTitle,confirmText,cancel,confirm);
  const drawer=make('dialog',undefined,'rules-dialog rule-group-dialog');drawer.setAttribute('aria-label','目标节点组');const drawerHead=make('header'),back=button('返回上层',()=>{drawerPath.pop();drawerLimit=120;renderDrawer();}),drawerTitle=make('h2'),close=button('关闭',()=>drawer.close());drawerHead.append(back,drawerTitle,close);const drawerHint=make('p',undefined,'rule-group-hint'),drawerList=make('div',undefined,'rule-group-list'),more=button('显示更多',()=>{drawerLimit+=120;renderDrawer();});drawer.append(drawerHead,drawerHint,drawerList,more);drawer.addEventListener('close',()=>{drawerPath=[];});
  page.append(header,status,notice,testSection,providerSection,tableCard,confirmDialog,drawer);host.replaceChildren(page);

  function message(text,readError=false){if(disposed)return;status.textContent=text;status.hidden=!text;status.dataset.readError=String(readError);}
  function actionState(){
    for(const control of page.querySelectorAll('[data-action]'))control.disabled=busy||reading||paused||!loaded||control.dataset.unavailable==='true';
    modeButton.disabled||=!['global','rule','direct'].includes(mode);updateAll.disabled||=!providers.length;refreshButton.disabled=busy||reading;
    for(const view of rowViews.values())view.toggle.disabled=busy||paused||reading||!rulesCurrent||!mode||mode==='global'||!view.row.canToggle;
  }
  async function request(url,{method='GET',body,signal,timeout=15000}={}){
    if(disposed)throw new DOMException('页面已离开。','AbortError');
    const active=new AbortController();controllers.add(active);const abort=()=>active.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();const deadline=setTimeout(abort,timeout);
    try{const response=await fetch(url,{method,headers:{Authorization:'Bearer '+storedSecret(),...(body!==undefined?{'Content-Type':'application/json'}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{}),cache:'no-store',signal:active.signal});
      if(response.status===401){paused=true;if(!disposed)onUnauthorized();const error=Error('登录已失效，请重新登录后读取最新状态。');error.unauthorized=true;throw error;}
      if(!response.ok)throw Error(response.status===429?'当前已有测试正在运行，请稍后手动重试。':'请求失败（HTTP '+response.status+'）。');
      if(response.status===204)return null;const text=await response.text();return text?JSON.parse(text):null;
    }finally{clearTimeout(deadline);signal?.removeEventListener('abort',abort);controllers.delete(active);}
  }
  function schedule(){clearTimeout(timer);if(!disposed&&!paused&&!document.hidden)timer=setTimeout(()=>refresh(false),15000);}
  async function refresh(explicit=true){
    if(disposed||document.hidden||!supported)return;if(busy){if(explicit)resumeRequested=true;return;}if(!explicit&&(paused||reading))return;
    clearTimeout(timer);paused=false;readController?.abort();const current=++sequence,active=new AbortController();readController=active;reading=true;actionState();
    try{const [rulesResult,providerResult,configResult,proxyResult,geoState]=await Promise.allSettled([
      request('/rules',{signal:active.signal}),request('/providers/rules',{signal:active.signal}),request('/configs',{signal:active.signal}),request('/sniffbox/proxies?fresh=1',{signal:active.signal}),request('/sniffbox/geo',{signal:active.signal})]);
      if(disposed||current!==sequence)return;
      const errors=[];
      rulesCurrent=false;if(rulesResult.status==='fulfilled'){rows=rulesSnapshot(rulesResult.value);loaded=true;rulesCurrent=true;}else errors.push('规则：'+rulesResult.reason.message);
      if(providerResult.status==='fulfilled'){try{providers=providersSnapshot(providerResult.value);}catch(error){errors.push(error.message);}}else errors.push('规则订阅：'+providerResult.reason.message);
      if(configResult.status==='fulfilled'&&typeof configResult.value?.mode==='string')mode=configResult.value.mode.toLowerCase();else{mode='';errors.push('运行模式未确认。');}
      proxiesCurrent=false;if(proxyResult.status==='fulfilled'&&proxyResult.value?.proxies&&typeof proxyResult.value.proxies==='object'){proxies=snapshot(proxyResult.value).proxies;proxiesCurrent=true;}else errors.push('目标节点状态未确认。');
      if(geoState.status==='fulfilled'&&Array.isArray(geoState.value?.files))geo=geoState.value.files;else errors.push('GEO 状态未确认。');
      render();if(errors.length)message(errors.join(' '),true);else if(explicit||status.dataset.readError==='true')message('');
    }catch(error){if(!disposed&&current===sequence)message(error.name==='AbortError'?'状态读取超时，请稍后重试。':error.message,true);}
    finally{if(!disposed&&current===sequence){reading=false;actionState();schedule();}}
  }
  async function perform(description,work,reload=false){
    if(disposed||busy||paused||!loaded)return;busy=true;sequence++;readController?.abort();reading=false;clearTimeout(timer);actionState();message(description+'中…');
    try{const detail=await work();if(!disposed)message(description+'完成。'+(typeof detail==='string'?' '+detail:''));}
    catch(error){if(!disposed)message(description+'未完成：'+(error.name==='AbortError'?'响应未确认，请刷新状态后再决定是否重试。':error.message));}
    finally{busy=false;if(!disposed){render();if(resumeRequested){resumeRequested=false;refresh();}else if(reload)refresh(false);else schedule();}}
  }
  function renderGeo(){const version=geo.find(file=>typeof file?.version==='string'&&file.version)?.version;geoVersion.textContent=version?'GEO '+version.slice(0,10):'';}
  function createRow(row){
    const tr=make('tr');const cells=Array.from({length:10},()=>make('td'));tr.append(...cells);const toggle=make('input');toggle.type='checkbox';toggle.setAttribute('aria-label','启用规则 '+row.index);cells[0].append(toggle);
    const target=button('',()=>{if(isGroup(proxies[view.row.proxy]))openGroup(view.row.proxy);});target.className='rule-target';const chain=make('span',undefined,'rule-chain');cells[1].append(target,chain);
    const view={tr,cells,toggle,target,chain,row};toggle.addEventListener('change',()=>{const current=view.row,disabled=!toggle.checked;toggle.checked=current.extra.disabled!==true;if(!current.canToggle||busy||paused||reading||!rulesCurrent||!mode||mode==='global')return;perform(disabled?'临时停用规则':'临时启用规则',async()=>{await request('/rules/disable',{method:'PATCH',body:{[String(current.index)]:disabled}});if(disposed)return;current.extra.disabled=disabled;return '下次配置重载后恢复。';});});return view;
  }
  function renderRows(){
    const selection=rulePage(rows,{query,located,page:pageNumber,pageSize}),matched=matchLog(testResult?.logs,rows)?.row;pageNumber=selection.page;const keep=new Set();
    for(const [position,row]of selection.rows.entries()){
      const key=String(row.position);keep.add(key);let view=rowViews.get(key);if(!view){view=createRow(row);rowViews.set(key,view);}view.row=row;view.tr.dataset.index=String(row.index);view.toggle.setAttribute('aria-label','启用规则 '+row.index);view.toggle.checked=row.extra.disabled!==true;view.toggle.hidden=!row.canToggle;view.tr.classList.toggle('off',row.extra.disabled===true);view.tr.classList.toggle('matched',matched===row);
      view.target.textContent=row.proxy||'—';view.target.title=row.proxy;view.target.disabled=!proxiesCurrent||!isGroup(proxies[row.proxy]);const chain=proxyChain(proxies,row.proxy),end=chain.at(-1),label=end?nodeLabel(end,optimize()):null;view.chain.textContent=label?'→ '+[label.flag,label.display].filter(Boolean).join(' '):'';view.chain.title=chain.join(' → ');
      for(const [index,value]of [[2,row.type||'—'],[3,row.payload||'—'],[4,typeof row.size==='number'&&row.size>=0?String(row.size):'—'],[5,row.extra.hitCount??'—'],[6,row.extra.missCount??'—'],[7,date(row.extra.hitAt)],[8,date(row.extra.missAt)],[9,String(row.index)]])view.cells[index].textContent=typeof value==='string'||typeof value==='number'?value:'—';
      if(tbody.children[position]!==view.tr)tbody.insertBefore(view.tr,tbody.children[position]||null);
    }
    for(const [key,view]of rowViews)if(!keep.has(key)){view.tr.remove();rowViews.delete(key);}let empty=tbody.querySelector('.rules-empty');if(!selection.rows.length){if(!empty){empty=make('tr',undefined,'rules-empty');const cell=make('td');cell.colSpan=10;empty.append(cell);tbody.append(empty);}empty.firstChild.replaceChildren(document.createTextNode(!loaded?'正在读取规则…':!rows.length?'当前配置没有规则。':'没有匹配的规则。'));if(loaded&&!rows.length){const link=make('a','前往订阅与策略');link.href='#/subscriptions';empty.firstChild.append(' ',link);}}else empty?.remove();
    pageCount.textContent=selection.page+' / '+selection.pages+' · '+selection.total+' 条';previous.disabled=selection.page<=1;next.disabled=selection.page>=selection.pages;tip.hidden=rows.length<=3;tableCard.classList.toggle('global',mode==='global');actionState();
  }
  function renderProviders(){
    providerSection.hidden=!providers.length;providerHeading.textContent='规则订阅（'+providers.length+'）';const term=query.trim().toLocaleLowerCase(),visible=providers.filter(provider=>!term||provider.name.toLocaleLowerCase().includes(term)),keep=new Set();
    for(const provider of visible){keep.add(provider.name);let view=providerViews.get(provider.name);if(!view){const card=make('article',undefined,'rule-provider'),name=make('strong',provider.name),details=make('span'),updated=make('small'),update=button('更新',()=>perform('规则订阅更新',async()=>{await request('/providers/rules/'+segment(provider.name),{method:'PUT'});},true));update.dataset.action='provider-update';card.dataset.provider=provider.name;card.append(name,details,updated,update);providerList.append(card);view={card,details,updated};providerViews.set(provider.name,view);}view.details.textContent=[provider.vehicleType,provider.behavior,typeof provider.ruleCount==='number'?provider.ruleCount+' 条':'数量未知'].filter(Boolean).join(' / ');view.updated.textContent='更新于 '+date(provider.updatedAt);}
    for(const [name,view]of providerViews)if(!keep.has(name)){view.card.remove();providerViews.delete(name);}
  }
  function optimize(){try{return JSON.parse(localStorage.getItem('ppgw-proxy-settings')||'{}').optimize!==false;}catch{return true;}}
  function openGroup(name){drawerPath=[name];drawerLimit=120;renderDrawer();if(!drawer.open)drawer.showModal();}
  function renderDrawer(){
    const name=drawerPath.at(-1),group=proxies[name];if(!name||!isGroup(group)){if(drawer.open)drawer.close();return;}
    drawerTitle.textContent=name;back.hidden=drawerPath.length<=1;drawerHint.textContent=drawerPath.join(' → ')+' · '+groupType(group)+' · '+group.all.length+' 个节点。'+(group.type==='Selector'&&!group.smart?'点击节点即可切换。':'由核心自动选择；锁定操作请使用代理页。');drawerList.replaceChildren();
    for(const member of group.all.slice(0,drawerLimit)){const child=proxies[member],row=make('div',undefined,'rule-group-row'),info=nodeLabel(member,optimize()),choose=button([info.flag,info.display].filter(Boolean).join(' '),()=>perform('切换目标节点',async()=>{const live=proxies[name];if(!proxiesCurrent||!isGroup(live)||live.type!=='Selector'||live.smart||!live.all.includes(member)||live.now===member)return;await request('/proxies/'+segment(name),{method:'PUT',body:{name:member}});if(!disposed)live.now=member;}));choose.dataset.action='group-select';choose.dataset.unavailable=String(!proxiesCurrent||group.type!=='Selector'||group.smart===true||group.now===member);choose.setAttribute('aria-pressed',String(group.now===member));choose.title=member;row.dataset.node=member;const latency=delay({proxies},member,name);row.append(choose,make('span',isGroup(child)?groupType(child):latency===null?'—':latency===0?'超时':latency+' ms'));
      if(isGroup(child)){const drill=button('查看子组',()=>{if(drawerPath.includes(member)){message('节点组存在循环引用，已停止继续展开。');return;}drawerPath.push(member);drawerLimit=120;renderDrawer();});drill.setAttribute('aria-label','查看子组 '+member);row.append(drill);}drawerList.append(row);
    }
    more.hidden=drawerLimit>=group.all.length;more.textContent='显示更多（剩余 '+Math.max(0,group.all.length-drawerLimit)+' 个）';actionState();
  }
  async function runTest(){
    if(busy||paused||!loaded)return;let url;try{url=testAddress(testInput.value);}catch(error){message(error.message);return;}
    testResult=null;located=null;renderTest();await perform('规则测试',async()=>{const result=await request('/sniffbox/ruletest',{method:'POST',body:{url},timeout:30000});if(disposed)return;if(result?.ok!==true)throw Error(typeof result?.error==='string'?result.error:'测试结果未确认。');testResult={url:typeof result.url==='string'?result.url:url,probe:result.probe,logs:Array.isArray(result.logs)?result.logs:[],logsError:typeof result.logsError==='string'?result.logsError:''};renderTest();});
  }
  function renderTest(){
    testOutput.replaceChildren();if(!testResult)return;const {probe,logs}=testResult;testOutput.append(make('p','测试网址：'+testResult.url));const summary=probe&&typeof probe==='object'?'请求结果：'+(typeof probe.status==='number'?'HTTP '+probe.status:typeof probe.error==='string'?probe.error:'未确认')+(typeof probe.ms==='number'?' · '+probe.ms+' ms':''):'请求结果未确认。';testOutput.append(make('p',summary));
    const match=matchLog(logs,rows);if(match){const line=make('p',undefined,'rule-test-match');line.append(document.createTextNode(match.type?'日志匹配：'+match.type+(match.payload?'('+match.payload+')':'')+' → ':match.none?'日志报告未匹配规则 → ':'日志使用目标 → '));const target=button(match.chain,()=>{if(isGroup(proxies[match.chain]))openGroup(match.chain);});target.disabled=!isGroup(proxies[match.chain]);line.append(target);const chain=proxyChain(proxies,match.chain);if(chain.length)line.append(make('span',' → '+chain.join(' → ')));testOutput.append(line);if(match.row){const locate=button(located===null?'定位规则 #'+match.row.index:'取消定位',()=>{located=located===null?match.row.index:null;pageNumber=1;renderTest();renderRows();});testOutput.append(locate);}}
    else testOutput.append(make('p','未取得可识别的匹配日志，不能据此确定命中规则。'));
    const details=make('details'),heading=make('summary','相关日志（'+logs.length+'）'),log=make('pre');log.textContent=logs.slice(0,2000).map(item=>(typeof item?.type==='string'?'['+item.type+'] ':'')+(typeof item?.payload==='string'?item.payload:'')).join('\n')||testResult.logsError||'暂无日志。';details.append(heading,log);testOutput.append(details);
  }
  function render(){if(disposed)return;count.textContent=rows.length+' 条规则';notice.hidden=mode!=='global';modeButton.textContent=mode==='global'?'切换规则模式':'切换全局模式';modeButton.prepend(icon('globe'));renderGeo();renderRows();renderProviders();if(drawer.open)renderDrawer();actionState();}
  const visibility=()=>{if(document.hidden){clearTimeout(timer);sequence++;readController?.abort();reading=false;}else if(!disposed&&!paused)refresh(false);},focus=()=>{if(!disposed&&!paused&&!busy)refresh(false);};document.addEventListener('visibilitychange',visibility);window.addEventListener('focus',focus);
  if(!supported){message('当前出站模式不使用 Mihomo 规则，请在网关设置中选择配置来源。');count.textContent='当前模式无规则';testSection.hidden=tableCard.hidden=true;geoButton.hidden=modeButton.hidden=refreshButton.hidden=true;}else if(!document.hidden)refresh();
  return {refresh,dispose(){disposed=true;sequence++;clearTimeout(timer);for(const controller of controllers)controller.abort();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('focus',focus);for(const dialog of [confirmDialog,drawer])if(dialog.open)dialog.close();host.replaceChildren();}};
}
