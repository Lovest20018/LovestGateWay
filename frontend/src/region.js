import {monitorClient} from './monitor-client.js';
import {categories,regionTests} from './region-catalog.js';
import {runRegionTest,probePool} from './region-engine.js';
import {el,button,input,page,copy,download} from './monitor-view.js';

const categoryNames={basic:'基础信息',ai:'人工智能',global:'全球服务',na:'北美',eu:'欧洲',hk:'香港',tw:'台湾',jp:'日本'},stateNames={yes:'可用',no:'不可用',partial:'部分可用',info:'信息',fail:'失败',waiting:'待检测',running:'检测中'};
export function mountRegion(host,{onUnauthorized=()=>{}}={}) {
  const ui=page(host,'region-page','区域检测'),results=new Map(),tiles=new Map(),groups=[],detail=el('dialog',undefined,'monitor-dialog'),detailTitle=el('h2'),detailText=el('pre'),progress=el('p',undefined,'monitor-progress');
  let controller=null,runId=0,busy=false,done=0,total=0,masked=true,onlyUnlocked=false,query='',disposed=false;
  const client=monitorClient({onUnauthorized,onPause:reason=>{if(reason!=='disposed'&&busy){stop(reason==='unauthorized'?'登录已失效，检测已停止；重新登录后请手动开始。':'页面已隐藏，检测已停止。');}}});
  const runAll=button('检测全部',()=>start(regionTests)),stopButton=button('停止检测',()=>stop('检测已停止。')),mask=button('显示 IP',()=>{masked=!masked;render();}),unlocked=button('仅看可用',()=>{onlyUnlocked=!onlyUnlocked;render();}),search=input('搜索检测项目',{onInput:value=>{query=value;render();}}),copyReport=button('复制报告',async()=>ui.message(await copy(report())?'报告已复制。':'复制失败，请导出报告。')),exportReport=button('导出报告',()=>download(report(),'region-report-'+new Date().toISOString().replace(/:/g,'-')+'.txt'));
  ui.head.append(runAll,stopButton,mask,unlocked,search,copyReport,exportReport);ui.root.append(el('p','检测使用当前网关出口。服务规则和网络状况可能影响结果，可展开详情核对。','monitor-hint'),progress);
  detail.setAttribute('aria-label','检测详情');detail.append(detailTitle,detailText,button('关闭',()=>detail.close()));ui.root.append(detail);
  for(const category of categories){const section=el('section',undefined,'monitor-region-section'),head=el('div',undefined,'monitor-head'),grid=el('div',undefined,'monitor-region-grid'),run=button('检测此组',()=>start(regionTests.filter(test=>test.cat===category)));head.append(el('h2',categoryNames[category]),run);section.append(head,grid);ui.root.append(section);groups.push({category,section,run});
    for(const test of regionTests.filter(test=>test.cat===category)){const tile=el('article',undefined,'glass monitor-region-tile'),name=el('h3',test.name),state=el('span',undefined,'monitor-region-state'),label=el('p',undefined,'monitor-region-label'),region=el('small'),actions=el('div',undefined,'monitor-actions'),run=button('检测',()=>start([test])),details=button('详情',()=>showDetail(test));run.setAttribute('aria-label','检测 '+test.name);details.setAttribute('aria-label',test.name+'详情');actions.append(run,details);let copyIP;
      if(['ip','udp-ip'].includes(test.id)){copyIP=button('复制 IP',async()=>{const value=results.get(test.id);ui.message(await copy(value?.ip||(/^[\da-f:.]+$/i.test(value?.label||'')?value.label:''))?'IP 已复制。':'尚无可复制的 IP。');});actions.append(copyIP);}
      tile.dataset.test=test.id;tile.append(name,state,label,region,actions);grid.append(tile);tiles.set(test.id,{test,tile,state,label,region,run,details,copyIP});
    }
  }
  function stop(message){runId++;controller?.abort();controller=null;busy=false;for(const [id,value]of results)if(value.state==='running')results.set(id,{...value,state:'waiting',label:'已停止，尚未得出结果'});if(!disposed){ui.message(message);render();}}
  function showDetail(test){const value=results.get(test.id);detailTitle.textContent=test.name;detailText.textContent=value?JSON.stringify(value,null,2):'尚未检测。';detail.showModal();}
  function report(){return ['LovestGateWay 区域检测报告',new Date().toISOString(),...regionTests.map(test=>{const value=results.get(test.id);return test.name+'\t'+(stateNames[value?.state]||'待检测')+'\t'+(value?.label||'')+'\t'+(value?.region||'')+(value?.tooltip?'\t'+value.tooltip:'');})].join('\n');}
  function render(){
    if(disposed)return;runAll.disabled=busy||client.unauthorized;stopButton.disabled=!busy;mask.textContent=masked?'显示 IP':'隐藏 IP';mask.setAttribute('aria-pressed',String(!masked));unlocked.setAttribute('aria-pressed',String(onlyUnlocked));progress.textContent=total===0?'共 '+regionTests.length+' 项 · 尚未检测':(busy?'正在检测':'已完成')+' '+done+' / '+total+' · 可用 '+[...results.values()].filter(row=>row.state==='yes').length+' · 部分可用 '+[...results.values()].filter(row=>row.state==='partial').length+' · 失败 '+[...results.values()].filter(row=>row.state==='fail').length;
    for(const view of tiles.values()){const value=results.get(view.test.id),state=value?.state||'waiting';view.tile.dataset.state=state;view.state.textContent=stateNames[state];let label=state==='waiting'?'':value?.label||'';if(masked&&['ip','udp-ip'].includes(view.test.id)&&/^[\da-f:.]+$/i.test(label))label=label.includes(':')?label.split(':').slice(0,2).join(':')+':••••':label.split('.').slice(0,2).join('.')+'.•••.•••';view.label.textContent=label;view.region.textContent=[value?.region,value?.ms!=null?value.ms+' ms':''].filter(Boolean).join(' · ');view.run.disabled=busy||client.unauthorized;view.details.disabled=!value;view.tile.hidden=!!query&&!view.test.name.toLowerCase().includes(query.toLowerCase())||onlyUnlocked&&view.test.cat!=='basic'&&!['yes','partial'].includes(state);if(view.copyIP)view.copyIP.disabled=!value||!(/^[\da-f:.]+$/i.test(value.label||'')||value.ip);}
    for(const group of groups){group.run.disabled=busy||client.unauthorized;group.section.hidden=![...tiles.values()].some(view=>view.test.cat===group.category&&!view.tile.hidden);}
  }
  async function start(tests){
    if(busy||disposed||client.unauthorized)return;busy=true;done=0;total=tests.length;const id=++runId;controller=new AbortController();const signal=controller.signal,shared={};ui.message('');for(const test of tests)results.set(test.id,{id:test.id,state:'waiting',label:'等待检测'});render();
    const probe=probePool(async(body,signal)=>{
      const result=await client.request('/sniffbox/probe',{method:'POST',body,signal,timeout:Math.min(120000,Math.max(15000,(body.timeoutMs||15000)+5000))});if(typeof result?.ok!=='boolean')throw Error('检测响应无效。');return result;
    });
    async function run(test){if(signal.aborted)return;results.set(test.id,{id:test.id,state:'running',label:'正在检测…'});render();let result;
      try{result=await runRegionTest(test,probe,signal,shared);if(test.id==='ip'&&/^[\da-f:.]+$/i.test(result.label)){shared.ip={ip:result.label,loc:result.region};result.ip=result.label;}
        if(test.id==='udp-ip'&&/^[\da-f:.]+$/i.test(result.label)&&result.label!==shared.ip?.ip){const address=result.label;const enrich=await Promise.allSettled([{url:'https://ipinfo.io/widget/demo/'+encodeURIComponent(address),headers:{Referer:'https://ipinfo.io/'},maxBody:16384},{url:'https://api.ipapi.is/?q='+encodeURIComponent(address),maxBody:32768}].map(body=>probe(body,signal)));result.tooltip=[result.tooltip,...enrich.filter(row=>row.status==='fulfilled'&&row.value.ok).map(row=>{try{return JSON.stringify(JSON.parse(row.value.body));}catch{return '';}})].filter(Boolean).join('\n');}
      }catch(error){if(signal.aborted||error.name==='AbortError'||error.unauthorized)return;result={id:test.id,name:test.name,state:'fail',label:error.message};}
      if(signal.aborted||id!==runId||disposed)return;results.set(test.id,result);if(tests.some(row=>row.id===test.id))done++;render();
    }
    try{
      await run(regionTests.find(test=>test.id==='ip'));let index=0;const pending=tests.filter(test=>test.id!=='ip');await Promise.all(Array.from({length:Math.min(5,pending.length)},async()=>{while(!signal.aborted&&index<pending.length)await run(pending[index++]);}));
    }finally{if(id===runId&&!disposed){busy=false;controller=null;ui.message(signal.aborted?'检测已停止。':'本轮检测已完成。');render();}}
  }
  render();return {expire(){client.expire();},refresh(){client.refresh();render();},dispose(){disposed=true;stop('');client.dispose();if(detail.open)detail.close();host.replaceChildren();}};
}
