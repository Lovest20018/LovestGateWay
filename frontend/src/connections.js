import {monitorClient} from './monitor-client.js';
import {bytes,date,duration,number} from './monitor-model.js';
import {connectionTracker,connectionValues,filterConnections} from './connection-model.js';
import {el,button,input,page,table,pager,field,confirmation,metrics,copy} from './monitor-view.js';

export function mountConnections(host,{onUnauthorized=()=>{}}={}) {
  const ui=page(host,'connections-page','连接'),tracker=connectionTracker(),filters={},dialog=confirmation(ui.root);
  let data={active:[],closed:[]},tab='active',query='',sort='active',direction=-1,pageNumber=1,pageSize=100,auto=true,loaded=false,fresh=false,busy=false,stored={};
  const columns={host:true,dl:true,ul:true,dlSpeed:true,ulSpeed:true,chains:true,rule:true,active:false,source:true,inbound:false};
  try{stored=JSON.parse(localStorage.getItem('conns_cols')||'{}');for(const key of Object.keys(columns))if(typeof stored[key]==='boolean')columns[key]=stored[key];}catch{}
  const client=monitorClient({onUnauthorized,onPause:()=>{tracker.resetRates();fresh=false;dialog.dispose();render();},onResume:()=>tracker.resetRates()});
  const tabs=el('div',undefined,'monitor-tabs'),activeTab=button('活动',()=>changeTab('active')),closedTab=button('已关闭',()=>changeTab('closed'));tabs.setAttribute('aria-label','连接视图');tabs.append(activeTab,closedTab);
  const search=input('搜索连接',{onInput:value=>{query=value;pageNumber=1;render();}}),columnPanel=el('details',undefined,'monitor-columns'),columnTitle=el('summary','列设置'),pause=button('暂停刷新',()=>{auto=!auto;reader.setEnabled(auto);tracker.resetRates();render();}),refresh=button('刷新',()=>readOnce()),closeAll=button('关闭全部',()=>{if(!busy&&fresh&&!client.unauthorized)dialog.show('关闭全部连接','将关闭网关当前的全部活动连接，确认继续吗？',()=>perform('/connections'));});
  columnPanel.append(columnTitle);ui.head.append(pause,refresh);ui.toolbar.append(tabs);closeAll.classList.add('monitor-danger');closeAll.title='关闭全部活动连接，不受当前筛选限制';
  const stats=el('div',undefined,'monitor-metrics'),card=el('section',undefined,'glass monitor-card');ui.root.append(stats,card);
  const labels={host:'目标地址',dl:'下载',ul:'上传',dlSpeed:'下载速率',ulSpeed:'上传速率',chains:'代理链',rule:'匹配规则',active:'最近活动',source:'来源',inbound:'入站'};
  for(const [key,label]of Object.entries(labels)){const control=el('input');control.type='checkbox';control.checked=columns[key];control.addEventListener('change',()=>{columns[key]=control.checked;try{localStorage.setItem('conns_cols',JSON.stringify({...stored,...columns}));}catch{}list.columns(definitions());render();});columnPanel.append(field(label,control));}
  const list=table({columns:definitions(),onSort:key=>{direction=sort===key?-direction:-1;sort=key;render();},onFilter:(key,value)=>{filters[key]=value;pageNumber=1;render();},filters,key:row=>row.id+(row.closedAt?':'+row.closedAt:'')});
  const pagination=pager((page,size)=>{pageNumber=page;pageSize=size;render();}),tools=el('div',undefined,'layout-toolbar layout-table-tools');tools.append(search,columnPanel,closeAll);card.append(tools,list.wrap,pagination.root);
  function definitions(){return [...Object.keys(columns).filter(key=>columns[key]).map(key=>({key,label:labels[key],filter:['host','chains','rule','source','inbound'].includes(key),numeric:['dl','ul','dlSpeed','ulSpeed','active'].includes(key),value:row=>key==='dl'||key==='ul'?bytes(connectionValues[key](row)):key.endsWith('Speed')?bytes(connectionValues[key](row))+(connectionValues[key](row)==null?'':'/s'):key==='active'?duration(Date.now()-(row.closedAt??row.lastActive)):connectionValues[key](row)||'—',title:row=>key==='active'?date(row.closedAt??row.lastActive):String(connectionValues[key](row)??'—'),action:['host','chains','rule','source','inbound'].includes(key)?async row=>ui.message(await copy(connectionValues[key](row))?'已复制。':'复制失败，请选择文字复制。'):undefined})),{key:'actions',label:'操作',sortable:false,value:()=>tab==='active'?'关闭':'已关闭',accessible:row=>'关闭连接 '+row.id,action:row=>{if(tab==='active'&&fresh&&!busy)perform('/connections/'+encodeURIComponent(row.id));},disabled:()=>tab!=='active'||busy||!fresh||client.unauthorized}];}
  function changeTab(value){tab=value;pageNumber=1;render();}
  function render(){
    if(client.disposed)return;
    activeTab.textContent='活动（'+data.active.length+'）';closedTab.textContent='已关闭（'+data.closed.length+'）';activeTab.setAttribute('aria-pressed',String(tab==='active'));closedTab.setAttribute('aria-pressed',String(tab==='closed'));
    pause.textContent=auto?'暂停刷新':'恢复刷新';pause.setAttribute('aria-pressed',String(!auto));closeAll.disabled=busy||!fresh||!loaded||client.unauthorized;refresh.disabled=busy;
    const rows=filterConnections(data[tab],{query,filters,sort,direction}),selection=pagination.update(rows,pageNumber,pageSize);pageNumber=selection.page;
    const down=rows.reduce((sum,row)=>sum+row.download,0),up=rows.reduce((sum,row)=>sum+row.upload,0),rates=rows.some(row=>row.dlSpeed!=null)?rows.reduce((sum,row)=>sum+number(row.dlSpeed),0):null;
    metrics(stats,[['当前筛选',rows.length+' 条'],['下载',bytes(down)],['上传',bytes(up)],['下载速率',tab==='active'?bytes(rates)+(rates==null?'':'/s'):'—']]);
    list.render(selection.rows,{sort,direction});
  }
  function readOnce(){reader.setEnabled(true);}
  async function perform(path){
    if(busy||!fresh||client.unauthorized||client.disposed)return;busy=true;client.suspend();render();ui.message('正在关闭连接…');
    try{await client.request(path,{method:'DELETE'});if(!client.disposed)ui.message('关闭请求已完成。');}
    catch(error){if(!client.disposed)ui.message('关闭未完成：'+(error.name==='AbortError'?'响应未确认，请刷新核对。':error.message));}
    finally{busy=false;if(!client.disposed){render();client.resume();if(!client.unauthorized&&!auto)readOnce();}}
  }
  const reader=client.poll('/connections',value=>{data=tracker.update(value);loaded=fresh=true;if(!auto)reader.setEnabled(false);if(ui.status.dataset.readError==='true')ui.message('');render();},{interval:1000,onError:error=>{fresh=false;ui.message(error.message);ui.status.dataset.readError='true';render();}});
  render();
  return {expire(){client.expire();},refresh(){tracker.resetRates();client.refresh();readOnce();},dispose(){client.dispose();dialog.dispose();host.replaceChildren();}};
}
