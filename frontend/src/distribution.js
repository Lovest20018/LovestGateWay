import {monitorClient} from './monitor-client.js';
import {bytes,date,duration,rateWindow,sortRows} from './monitor-model.js';
import {trafficSnapshot,distributions,nodeSnapshot} from './traffic-model.js';
import {nodeLabel} from './proxy-model.js';
import {el,button,input,select,page,table,pager,confirmation,metrics,sparkline,streamState} from './monitor-view.js';

export function mountDistribution(host,{initialInfo,onUnauthorized=()=>{}}={}) {
  const ui=page(host,'distribution-page','流量分布'),supported=initialInfo?.features?.net_rec!==false,viaClash=['yaml','suburl'].includes(initialInfo?.mode),dialog=confirmation(ui.root),rates=rateWindow();
  let records=[],nodes=[],since=0,query='',sort='total',direction=-1,auto=true,busy=false,loaded=false,nodePage=1,nodeSize=10,currentRate={total:null};
  let optimize=true;try{optimize=JSON.parse(localStorage.getItem('ppgw-proxy-settings')||'{}').optimize!==false;}catch{}
  const client=monitorClient({onUnauthorized,onPause:()=>{dialog.dispose();rates.reset();currentRate={total:null};render();},onResume:()=>{rates.reset();currentRate={total:null};}});
  const refresh=button('刷新分布',()=>client.refresh()),pause=button('暂停刷新',()=>{auto=!auto;nodeReader.setEnabled(auto&&supported&&viaClash);stream.setEnabled(auto&&supported);rates.reset();currentRate={total:null};render();});ui.head.append(refresh,pause);
  const nodeCard=el('section',undefined,'glass monitor-card'),nodeHead=el('div',undefined,'monitor-head'),stats=el('div',undefined,'monitor-metrics'),graph=el('div',undefined,'monitor-rate-card');
  const search=input('搜索节点',{onInput:value=>{query=value;nodePage=1;render();}}),clear=button('清空节点统计',()=>dialog.show('清空节点统计','将重置节点累计流量，确认继续吗？',clearNodes));nodeHead.append(el('h2','节点流量'),search,clear);
  const nodeTable=table({columns:[{key:'node',label:'节点',value:row=>{const label=nodeLabel(row.node,optimize);return [label.flag,label.display||row.node].filter(Boolean).join(' ');},title:row=>row.node},...['down','up','total'].map(key=>({key,label:{down:'下载',up:'上传',total:'总流量'}[key],numeric:true,value:row=>bytes(row[key])}))],key:row=>row.node,onSort:key=>{direction=key===sort?-direction:-1;sort=key;render();}}),nodePager=pager((page,size)=>{nodePage=page;nodeSize=size;render();},{size:10});
  nodeCard.append(nodeHead,stats,graph,nodeTable.wrap,nodePager.root);ui.root.append(nodeCard);
  const ranks=[];
  for(const [kind,title]of [['client','客户端分布'],['service','服务与域名分布']]){
    const section=el('section',undefined,'monitor-ranking-section'),grid=el('div',undefined,'monitor-ranking-grid');section.append(el('h2',title),grid);ui.root.append(section);
    for(const [metric,label]of [['total','流量排行'],['duration','持续时间排行'],['conns','连接数排行']]){
      const card=el('section',undefined,'glass monitor-card'),head=el('div',undefined,'monitor-head'),body=el('ol',undefined,'monitor-ranking'),state={kind,metric,page:1,size:10,body};head.append(el('h3',label));
      if(metric==='total')head.append(select(title+'流量指标',[['total','总流量'],['down','下载'],['up','上传']],value=>{state.metric=value;state.page=1;render();},'total'));
      state.pager=pager((page,size)=>{state.page=page;state.size=size;render();},{size:10});card.append(head,body,state.pager.root);grid.append(card);ranks.push(state);
    }
  }
  function render(){
    if(client.disposed)return;nodeCard.hidden=!viaClash||!supported;pause.textContent=auto?'暂停刷新':'恢复刷新';pause.disabled=!supported;pause.setAttribute('aria-pressed',String(!auto));refresh.disabled=busy||!supported;clear.disabled=busy||!loaded||client.unauthorized;
    const selection=nodePager.update(sortRows(nodes.filter(row=>row.node.toLowerCase().includes(query.toLowerCase())),row=>row[sort],direction),nodePage,nodeSize);nodePage=selection.page;nodeTable.render(selection.rows,{sort,direction});
    metrics(stats,[['统计起点',date(since)],['节点数量',String(nodes.length)],['累计流量',bytes(nodes.reduce((sum,row)=>sum+row.total,0))]]);graph.replaceChildren(el('span','节点合计速率'),el('strong',bytes(currentRate.total)+(currentRate.total==null?'':'/s')),sparkline(rates.history.total));
    const grouped={client:distributions(records,'client'),service:distributions(records,'service')};
    for(const state of ranks){state.body.closest('.monitor-ranking-section').hidden=!supported;const ordered=sortRows(grouped[state.kind],row=>row[state.metric],-1),selection=state.pager.update(ordered,state.page,state.size),max=Math.max(1,...ordered.map(row=>row[state.metric]));state.page=selection.page;
      state.body.replaceChildren(...selection.rows.map((row,index)=>{const item=el('li'),bar=el('span',undefined,'monitor-bar');bar.style.width=(row[state.metric]/max*100)+'%';item.append(el('span',String((selection.page-1)*state.size+index+1)),el('strong',row.label||'未知'),el('span',state.metric==='duration'?duration(row.duration):state.metric==='conns'?String(row.conns):bytes(row[state.metric])),bar);return item;}));if(!selection.rows.length)state.body.append(el('li','暂无采集数据。'));
    }
  }
  async function clearNodes(){if(busy||client.unauthorized||client.disposed)return;busy=true;client.suspend();render();try{const value=await client.request('/sniffbox/nodes/clear',{method:'POST'});if(client.disposed)return;if(value?.cleared!==true)throw Error('清空结果未确认，请刷新核对。');nodes=[];loaded=false;rates.reset();currentRate={total:null};ui.message('节点统计已清空。');}catch(error){if(!client.disposed)ui.message('清空未完成：'+error.message);}finally{busy=false;if(!client.disposed){render();client.resume();}}}
  const nodeReader=client.poll('/sniffbox/nodes',value=>{const next=nodeSnapshot(value);nodes=next.nodes;since=next.since;loaded=true;currentRate=rates.sample(nodes.reduce((sum,row)=>sum+row.down,0),nodes.reduce((sum,row)=>sum+row.up,0),Date.now(),String(since));render();},{enabled:supported&&viaClash,interval:3000,onError:error=>ui.message(error.message)});
  const connectionState=el('p','正在连接…','monitor-stream-state');connectionState.setAttribute('role','status');ui.head.after(connectionState);
  const stream=client.stream('/sniffbox/traffic/sse',raw=>{records=trafficSnapshot(JSON.parse(raw));render();},{enabled:supported,onState:state=>{ui.root.dataset.stream=state;connectionState.textContent=streamState(state);},onError:error=>ui.message(error.message)});
  if(!supported)ui.message('流量记录未启用，请在网关设置中开启流量采集。');render();
  return {expire(){client.expire();},refresh(){client.refresh();},dispose(){client.dispose();dialog.dispose();host.replaceChildren();}};
}
