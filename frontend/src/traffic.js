import {monitorClient} from './monitor-client.js';
import {bytes,date,csv,number,rateWindow} from './monitor-model.js';
import {trafficSnapshot,filterTraffic,aggregateTraffic,sortTraffic,trafficValues,transport,sniff} from './traffic-model.js';
import {el,button,input,select,field,page,table,pager,confirmation,download,metrics,sparkline,streamState,actionMenu} from './monitor-view.js';

export function mountTraffic(host,{initialInfo,onUnauthorized=()=>{}}={}) {
  const ui=page(host,'traffic-page','流量记录'),filters={},rates=rateWindow(),expanded=new Set(),dialog=confirmation(ui.root),supported=initialInfo?.features?.net_rec!==false;
  let rows=[],view='domain',query='',range=Infinity,sort='total',direction=-1,pageNumber=1,pageSize=20,auto=true,busy=false,loaded=false,totals=null,currentRate={down:null,up:null},stored={};
  const columns={ts:true,last_seen:true,client:true,transport:true,domain:true,sniff:true,inbound:true,conns:true,down:true,up:true};
  try{stored=JSON.parse(localStorage.getItem('traffic_cols')||'{}');for(const key of Object.keys(columns))if(typeof stored[key]==='boolean')columns[key]=stored[key];}catch{}
  const client=monitorClient({onUnauthorized,onPause:()=>{rates.reset();currentRate={down:null,up:null};dialog.dispose();render();},onResume:()=>{rates.reset();currentRate={down:null,up:null};}});
  const viewControl=select('流量视图',[['domain','按域名'],['client','按客户端'],['full','完整记录']],value=>{view=value;sort=value==='full'?'ts':'total';direction=-1;pageNumber=1;expanded.clear();list.columns(definitions());render();},view);
  const search=input('搜索流量记录',{onInput:value=>{query=value;pageNumber=1;render();}}),rangeControl=select('流量时间范围',[['300000','近 5 分钟'],['900000','近 15 分钟'],['1800000','近 30 分钟'],['3600000','近 1 小时'],['21600000','近 6 小时'],['43200000','近 12 小时'],['86400000','近 24 小时'],['all','全部']],value=>{range=value==='all'?Infinity:Number(value);pageNumber=1;render();},'all');
  const pause=button('暂停刷新',()=>{auto=!auto;stream.setEnabled(auto);render();}),refresh=button('重新连接',()=>client.refresh()),clear=button('清空记录',()=>{if(!busy&&loaded&&!client.unauthorized)dialog.show('清空流量记录','将清空流量记录和累计统计，确认继续吗？',clearRecords);}),exports=actionMenu('导出流量记录',[['csv-filtered','CSV · 当前筛选'],['csv-all','CSV · 全部'],['json-filtered','JSON · 当前筛选'],['json-all','JSON · 全部']],exportData);clear.classList.add('monitor-danger');
  ui.head.append(viewControl,search,rangeControl,pause,refresh,exports,clear);
  const stats=el('div',undefined,'monitor-metrics'),graphs=el('div',undefined,'monitor-rate-graphs'),card=el('section',undefined,'glass monitor-card'),columnPanel=el('details',undefined,'monitor-columns');columnPanel.append(el('summary','完整记录列设置'));
  const labels={ts:'开始时间',last_seen:'最近活动',client:'客户端',transport:'传输/端口',domain:'域名',sniff:'嗅探',inbound:'入站',conns:'连接数',down:'下载',up:'上传',total:'总流量',clients:'客户端数'};
  for(const key of Object.keys(columns)){const control=el('input');control.type='checkbox';control.checked=columns[key];control.addEventListener('change',()=>{columns[key]=control.checked;try{localStorage.setItem('traffic_cols',JSON.stringify({...stored,...columns}));}catch{}if(view==='full')list.columns(definitions());render();});columnPanel.append(field(labels[key],control));}
  ui.root.append(stats,graphs,columnPanel,card);
  function definitions(){
    const keys=view==='full'?Object.keys(columns).filter(key=>columns[key]):view==='domain'?['domain','down','up','total','clients','conns','last_seen']:['client','down','up','total','conns','last_seen'];
    return [...keys.map(key=>({key,label:labels[key],filter:view==='full'&&['client','domain','transport','sniff','inbound'].includes(key),numeric:['conns','down','up','total','clients'].includes(key),value:row=>['down','up','total'].includes(key)?bytes(trafficValues[key](row)):['ts','last_seen'].includes(key)?date(row[key]):key==='inbound'?row.inbound.join(' / '):key==='sniff'?sniff(row):key==='transport'?transport(row):key==='clients'?row.clients.length:row[key]??'—'})),...(view!=='full'?[{key:'details',label:'详情',sortable:false,value:row=>expanded.has(row.key)?'收起':'展开',action:row=>{if(expanded.has(row.key))expanded.delete(row.key);else expanded.add(row.key);render();},accessible:row=>'展开 '+(row.domain||row.client)}]:[])];
  }
  const list=table({columns:definitions(),filters,onSort:key=>{direction=sort===key?-direction:-1;sort=key;render();},onFilter:(key,value)=>{filters[key]=value;pageNumber=1;render();},key:row=>row.key,expansion:row=>{
    if(!expanded.has(row.key))return null;const detail=el('div',undefined,'monitor-detail-list');
    if(view==='domain'){detail.append(el('strong','客户端'));for(const name of row.clients)detail.append(el('span',name));}
    else {detail.append(el('strong','访问域名'));for(const [name,total]of row.domains.slice(0,100))detail.append(el('span',name+' · '+bytes(total)));if(row.domains.length>100)detail.append(el('small','仅展开前 100 个域名，完整数据可导出。'));}return detail;
  }}),pagination=pager((page,size)=>{pageNumber=page;pageSize=size;render();},{size:20});card.append(list.wrap,pagination.root);
  function filtered(){return filterTraffic(rows,{query,filters,range});}
  function render(){
    if(client.disposed)return;columnPanel.hidden=!supported||view!=='full';pause.textContent=auto?'暂停刷新':'恢复刷新';pause.setAttribute('aria-pressed',String(!auto));clear.disabled=busy||!loaded||client.unauthorized||!supported;pause.disabled=!supported;refresh.disabled=busy||!supported;exports.disabled=!loaded;
    const selected=filtered(),grouped=sortTraffic(aggregateTraffic(selected,view),sort,direction),selection=pagination.update(grouped,pageNumber,pageSize);pageNumber=selection.page;list.render(selection.rows,{sort,direction});
    metrics(stats,[['累计下载',totals?bytes(totals.downloadTotal):'—'],['累计上传',totals?bytes(totals.uploadTotal):'—'],['当前筛选',selected.length+' 条'],['筛选流量',bytes(selected.reduce((sum,row)=>sum+row.down+row.up,0))]]);
    graphs.replaceChildren(...['down','up'].map(key=>{const card=el('div',undefined,'glass monitor-rate-card');card.append(el('span',key==='down'?'下载速率':'上传速率'),el('strong',bytes(currentRate[key])+(currentRate[key]==null?'':'/s')),sparkline(rates.history[key]));return card;}));
  }
  async function clearRecords(){
    if(busy||client.unauthorized||client.disposed)return;busy=true;client.suspend();render();ui.message('正在清空记录…');
    try{const result=await client.request('/sniffbox/traffic/clear',{method:'POST'});if(client.disposed)return;if(result?.cleared!==true)throw Error('清空结果未确认，请重新连接核对。');rows=[];totals=null;currentRate={down:null,up:null};rates.reset();ui.message('流量记录已清空。');}
    catch(error){if(!client.disposed)ui.message('清空未完成：'+(error.name==='AbortError'?'响应未确认，请重新连接核对。':error.message));}
    finally{busy=false;if(!client.disposed){render();client.resume();}}
  }
  function exportData(value){
    const [format,scope]=value.split('-'),source=scope==='all'?rows:filtered(),items=sortTraffic(aggregateTraffic(source,view),sort,direction),stamp=new Date().toISOString().replace(/[-:]/g,'').slice(0,15);
    if(format==='json'){download(JSON.stringify(items.map(({key,...row})=>row),null,2),'traffic-'+stamp+'.json','application/json');return;}
    const keys=view==='full'?Object.keys(columns).filter(key=>columns[key]):view==='domain'?['domain','down','up','total','clients','conns','last_seen']:['client','down','up','total','conns','last_seen'];
    const definition=keys.map(key=>({label:key,value:row=>key==='clients'?row.clients.join(' '):['ts','last_seen'].includes(key)?(row[key]?new Date(row[key]).toISOString():''):key==='sniff'?row.sniff.join(' '):key==='inbound'?row.inbound.join(' '):key==='transport'?transport(row):key==='total'?row.down+row.up:row[key]}));
    if(view==='client')definition.push({label:'top_domains',value:row=>row.domains.map(([name,total])=>name+':'+total).join(' ')});
    if(view==='full'&&columns.sniff)definition.push({label:'head',value:row=>row.head});download(csv(items,definition),'traffic-'+stamp+'.csv','text/csv;charset=utf-8');
  }
  const connectionState=el('p','正在连接…','monitor-stream-state');connectionState.setAttribute('role','status');ui.head.after(connectionState);
  const stream=client.stream('/sniffbox/traffic/sse',raw=>{rows=trafficSnapshot(JSON.parse(raw));loaded=true;ui.message('');render();},{enabled:supported,onState:state=>{ui.root.dataset.stream=state;connectionState.textContent=streamState(state);},onError:error=>ui.message(error.message)});
  client.poll('/sniffbox/traffic/total',value=>{if(typeof value?.downloadTotal!=='number'||typeof value?.uploadTotal!=='number')throw Error('累计流量响应无效。');totals={downloadTotal:number(value.downloadTotal),uploadTotal:number(value.uploadTotal)};currentRate=rates.sample(totals.downloadTotal,totals.uploadTotal);render();},{interval:3000,enabled:supported,onError:error=>ui.message(error.message)});
  if(!supported){ui.message('流量记录未启用，请在网关设置中开启流量采集。');card.hidden=stats.hidden=graphs.hidden=columnPanel.hidden=true;}
  render();
  return {expire(){client.expire();},refresh(){rates.reset();client.refresh();},dispose(){client.dispose();dialog.dispose();host.replaceChildren();}};
}
