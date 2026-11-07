let overviewCache=null;
import {formatBytes} from './format.js';
export {formatBytes} from './format.js';
import {sessionContext} from './auth.js';
import {icon} from './icons.js';

const number=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;
const list=value=>Array.isArray(value)?value:[];
const text=value=>typeof value==='string'?value:'—';
export const modeLabel=data=>data?.ppsub?'原生 PPsub':({free:'直连',socks5:'SOCKS5',ovpn:'OpenVPN',yaml:'本地 YAML',suburl:'订阅'})[data?.mode]||text(data?.mode);

export function trafficSample(previous,traffic,at,boot=''){
  const down=number(traffic?.downloadTotal),up=number(traffic?.uploadTotal);
  if(down===null||up===null)return null;
  const sample={down,up,at,boot,downRate:null,upRate:null};
  if(previous&&previous.boot===boot&&at>previous.at&&down>=previous.down&&up>=previous.up){
    const seconds=Math.max(.2,(at-previous.at)/1000);
    sample.downRate=(down-previous.down)/seconds;sample.upRate=(up-previous.up)/seconds;
  }
  return sample;
}
export function poolWindow(history,pool,at){
  if(!pool)return [];
  const current={at,cold:number(pool.cold_acquires),dead:number(pool.discarded_dead),warmed:number(pool.warmed),acquires:number(pool.acquires)},last=history.at(-1);
  if(last&&['cold','dead','warmed','acquires'].some(key=>current[key]!==null&&last[key]!==null&&current[key]<last[key]))history=[];
  return [...history.slice(-39),current];
}
function duration(value){value=number(value);if(value===null)return '—';return (value>=86400?Math.floor(value/86400)+'天 ':'')+[Math.floor(value/3600)%24,Math.floor(value/60)%60,Math.floor(value)%60].map(v=>String(v).padStart(2,'0')).join(':');}
function percent(value){return number(value)===null?'—':value.toFixed(1)+'%';}
const make=(tag,content,className)=>{const node=document.createElement(tag);if(content!==undefined)node.textContent=content;if(className)node.className=className;return node;};
function chart(label,series,format){
  const box=make('div',undefined,'overview-chart'),canvas=make('canvas'),tip=make('output',undefined,'overview-chart-tip');
  canvas.setAttribute('role','img');canvas.setAttribute('aria-label',label);box.append(canvas,tip);tip.hidden=true;
  let values=[],maximum=1;
  const redraw=()=>{
    const bounds=box.getBoundingClientRect(),width=Math.max(1,bounds.width),height=box.clientHeight,scale=devicePixelRatio||1;
    canvas.width=width*scale;canvas.height=height*scale;const ctx=canvas.getContext('2d');if(!ctx)return;ctx.scale(scale,scale);
    const style=getComputedStyle(box);ctx.clearRect(0,0,width,height);ctx.strokeStyle=style.getPropertyValue('--edge');ctx.lineWidth=1;
    for(let i=1;i<4;i++){const y=i*height/4;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
    series.forEach(([key,,color])=>{ctx.strokeStyle=style.getPropertyValue(color)||color;ctx.lineWidth=1.8;ctx.beginPath();let begun=false;
      values.forEach((value,index)=>{const v=number(value[key]);if(v===null){begun=false;return;}const x=index*width/39,y=height-6-Math.min(v/maximum,1)*(height-20);if(begun)ctx.lineTo(x,y);else ctx.moveTo(x,y);begun=true;});ctx.stroke();});
  };
  const observer=new ResizeObserver(redraw);observer.observe(box);
  const themeObserver=new MutationObserver(redraw);themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  canvas.addEventListener('pointermove',event=>{if(!values.length)return;const width=canvas.getBoundingClientRect().width,index=Math.min(values.length-1,Math.max(0,Math.round(event.offsetX/width*39))),sample=values[index];tip.textContent=series.map(([key,name])=>name+' '+format(sample[key])).join(' · ');tip.hidden=false;});
  canvas.addEventListener('pointerleave',()=>{tip.hidden=true;});
  return {node:box,update(next,max,visibleSeries=series){series=visibleSeries;values=next;box.dataset.samples=String(values.length);maximum=max||Math.max(1,...values.flatMap(row=>series.map(([key])=>number(row[key])||0)))*1.15;canvas.setAttribute('aria-label',label+'，'+(values.length?series.map(([key,name])=>name+' '+format(values.at(-1)[key])).join('，'):'暂无采样'));redraw();},dispose(){observer.disconnect();themeObserver.disconnect();}};
}

export function mountOverview(host,{initialInfo=null,onUnauthorized=()=>{},onInfo=()=>{}}={}){
  const page=make('div',undefined,'page overview-page layout-page');page.id='overview-page';
  // The template contains fixed application strings only. API values use textContent.
  page.innerHTML=`<header class="overview-head layout-heading"><div class="layout-title"><h1 class="page-title">概览</h1></div><div class="layout-actions"><div class="overview-clock"><small data-field="date"></small><strong data-field="clock">—</strong></div><button class="btn" type="button" data-refresh>刷新</button></div></header><div class="overview-badges overview-runtime-summary"><span data-field="version"></span><span data-field="clash-version"></span><span data-field="mode"></span><span data-field="geo"></span></div>
  <p class="overview-status" role="status" hidden></p>
  <section class="glass overview-traffic"><h2>流量总览</h2><div class="overview-traffic-stats">
  <div class="overview-stat total"><small>总量</small><strong data-field="rate-total">—</strong><b data-field="traffic-total">—</b></div><div class="overview-stat down"><small>↓ 下载</small><strong data-field="rate-down">—</strong><b data-field="traffic-down">—</b></div><div class="overview-stat up"><small>↑ 上传</small><strong data-field="rate-up">—</strong><b data-field="traffic-up">—</b></div>
  <div class="overview-stat mini"><small>TCP / UDP</small><strong data-field="protocols">—</strong><span data-field="active">活跃连接 —</span></div><div class="overview-stat mini"><small>已映射域名</small><strong data-field="mapped">—</strong><span data-field="mapped-active"></span></div><div class="overview-stat mini"><small>客户端</small><strong data-field="clients">—</strong><span>唯一客户端 IP</span></div></div><div data-chart="traffic"></div>
  </section><section class="glass overview-interfaces"><h2 class="overview-interfaces-heading">网络接口</h2><div class="overview-table-wrap"><table><thead><tr><th>IF</th><th>IPv4 / IPv6</th><th>网关</th><th>MAC</th><th>驱动型号 / 协商速率</th></tr></thead><tbody data-interfaces></tbody></table></div></section>
  <div class="overview-columns"><section class="glass overview-cpu" data-section="cpu"><h2>CPU <small data-field="cores"></small></h2><p data-field="cpu-model"></p><div class="overview-inline"><strong data-field="cpu-usage">—</strong><span data-field="frequency"></span><span data-field="temperature"></span></div><div class="overview-legend" data-cpu-legend></div><div data-chart="cpu"></div><div class="overview-cores" data-cores></div><dl class="overview-details" data-cpu-processes></dl></section>
  <section class="glass overview-memory" data-section="memory"><h2>内存</h2><div class="overview-memory-summary"><span>已用 <b data-field="memory-used">—</b></span><span>空闲 <b data-field="memory-free">—</b></span><span>总内存 <b data-field="memory-total">—</b></span></div><div class="overview-memory-bar" role="img" aria-label="内存使用比例"><i></i></div><div class="overview-memory-blocks" data-memory-blocks aria-hidden="true"></div><small data-field="memory-block-size"></small><div class="overview-legend" data-memory-legend></div><div data-chart="memory"></div><dl class="overview-details" data-memory-processes></dl></section></div>
  <section class="glass overview-config"><h2>配置信息</h2><dl class="overview-details" data-config></dl><div class="overview-features" data-features></div></section>
  <section class="glass overview-pool" data-section="pool" hidden><h2>SOCKS 连接池</h2><p>空闲连接与近期活动</p><div class="overview-pool-stats" data-pool></div><div class="overview-memory-blocks" data-pool-blocks aria-hidden="true"></div></section>`;
  host.replaceChildren(page);
  page.querySelector('.overview-columns').after(page.querySelector('.overview-interfaces'));
  const fields=Object.fromEntries([...page.querySelectorAll('[data-field]')].map(node=>[node.dataset.field,node]));
  const put=(key,value)=>{fields[key].textContent=value??'—';};
  const trafficChart=chart('流量速率',[['total','总量','--brand'],['down','下载','--down'],['up','上传','--up']],value=>formatBytes(value,true));
  const cpuSeries=[['cpu','整机','--brand'],['cpuSb','LovestGateWay','--down'],['cpuCore','出站核心','--up']],cpuChart=chart('CPU 占用',cpuSeries,percent);
  const memorySeries=[['memOther','系统组件','--brand'],['memSb','LovestGateWay','--down'],['memCore','出站核心','--up']],memoryChart=chart('内存占用',memorySeries,formatBytes);
  for(const [key,graphic]of [['traffic',trafficChart],['cpu',cpuChart],['memory',memoryChart]])page.querySelector('[data-chart="'+key+'"]').append(graphic.node);
  page.querySelector('.overview-traffic h2').prepend(icon('swap'));
  page.querySelector('.overview-interfaces-heading').prepend(icon('radio'));
  let cloudflaredSeen=false;
  let data={},history=[],poolHistory=[],previous=null,disposed=false,paused=false,timer=null,controller=null,sequence=0,clockAt=performance.now(),lastFull=0;
  let cacheChecked=false;const authIdentity=sessionContext();
  const button=page.querySelector('[data-refresh]'),status=page.querySelector('[role=status]');
  const currentBoot=()=>text(data.integration?.data?.boot_id??data.integration?.boot_id);
  function merge(value){
    const previous=data;data={...data,...value};for(const key of ['memory','cpu','fakeip'])if(previous[key]||value[key])data[key]={...previous[key],...value[key]};
  }
  function rows(target,values){target.replaceChildren(...values.flatMap(([label,value,title,className])=>{const dt=make('dt',label),dd=make('dd',value);if(title)dd.title=title;if(className)dd.className=className;return [dt,dd];}));}
  function legend(target,values){target.replaceChildren(...values.map(([label,color])=>{const item=make('span',label),swatch=make('i');swatch.style.background='var('+color+')';item.prepend(swatch);return item;}));}
  function clock(){
    if(disposed)return;const timestamp=number(data.timestamp);if(timestamp===null){put('date','');put('clock','—');return;}
    const date=new Date(timestamp*1000+performance.now()-clockAt);put('date',date.toLocaleDateString('zh-CN'));put('clock',date.toLocaleTimeString('zh-CN',{hour12:false}));
  }
  function render(){
    const m=data.memory||{},c=data.cpu||{},connections=data.connections||{},traffic=data.traffic||{},features=data.features||{},coreName=m.ovpn_rss!=null?'OpenVPN':data.clash_meta?'mihomo':'Clash';
    put('version','LovestGateWay '+text(data.version));put('clash-version',data.clash_version?coreName+' '+text(data.clash_version):'');put('mode',modeLabel(data));
    const count=value=>number(value)===null?'—':String(value);
    put('traffic-down',formatBytes(traffic.downloadTotal));put('traffic-up',formatBytes(traffic.uploadTotal));put('traffic-total',formatBytes(number(traffic.downloadTotal)!==null&&number(traffic.uploadTotal)!==null?traffic.downloadTotal+traffic.uploadTotal:null));
    put('rate-down',formatBytes(previous?.downRate,true));put('rate-up',formatBytes(previous?.upRate,true));put('rate-total',formatBytes(previous?.downRate!=null&&previous?.upRate!=null?previous.downRate+previous.upRate:null,true));
    put('protocols',count(connections.tcp)+' / '+count(connections.udp));put('active','活跃连接 '+count(connections.active));put('mapped',count(data.fakeip?.mapped));put('mapped-active','活跃 FakeIP '+count(connections.fakeip)+' · RealIP '+count(connections.realip));put('clients',count(connections.clients));
    const interfaces=page.querySelector('[data-interfaces]');
    const allInterfaces=list(data.interfaces),virtual=allInterfaces.filter(item=>!item.gateway&&/^(docker\d*|br-|veth|virbr|lo$)/.test(item.name||'')),shown=allInterfaces.filter(item=>!virtual.includes(item));
    const buildRows=items=>items.map(item=>{const row=make('tr');const speed=item.link===false?'未连接':number(item.speed)!==null?item.speed+' Mbps'+(item.duplex?' · '+item.duplex:''):'—';
      [text(item.name),[...list(item.ipv4),...list(item.ipv6)].map(text).join('\n')||'—',text(item.gateway),text(item.mac),[item.model||item.driver||'',speed].filter(Boolean).join('\n')].forEach((value,index)=>row.append(make('td',value,index===3?'overview-mac':undefined)));return row;});
    interfaces.replaceChildren(...buildRows(shown));
    let virtualBox=page.querySelector('.overview-virtual');
    if(!virtualBox){virtualBox=make('details',undefined,'overview-virtual');virtualBox.append(make('summary'));const wrap=make('div',undefined,'overview-table-wrap'),table=make('table');table.append(make('tbody'));wrap.append(table);virtualBox.append(wrap);interfaces.closest('.overview-table-wrap').after(virtualBox);}
    virtualBox.hidden=!virtual.length;virtualBox.querySelector('summary').textContent='虚拟接口（'+virtual.length+'）';virtualBox.querySelector('tbody').replaceChildren(...buildRows(virtual));
    if(!interfaces.children.length){const cell=make('td',virtual.length?'其他接口已收起':'暂无数据');cell.colSpan=5;interfaces.append(make('tr'));interfaces.firstChild.append(cell);}
    page.querySelector('[data-section=cpu]').hidden=!data.cpu;page.querySelector('[data-section=memory]').hidden=!data.memory;
    put('cpu-model',text(c.model));put('cores',number(c.cores)!==null?c.cores+' 核':'');put('cpu-usage',percent(c.usage));put('frequency',number(c.freq)!==null?c.freq+' MHz':'');fields.frequency.title=[['最低',c.freq_min],['最高',c.freq_max]].filter(([,v])=>number(v)!==null).map(([label,v])=>label+' '+v+' MHz').join(' · ');
    put('temperature',number(c.temp)!==null?c.temp.toFixed(1)+' °C':'');fields.temperature.title=text(c.temp_sensor);
    const processes=[['LovestGateWay','sniffbox','--down'],[coreName,m.ovpn_rss!=null?'ovpn':'clash','--up'],['Cloudflared','cloudflared','--err']].filter(([,key])=>key==='cloudflared'?cloudflaredSeen:number(m[key+'_rss'])!==null);
    legend(page.querySelector('[data-cpu-legend]'),[['整机','--brand'],...processes.map(([label,,color])=>[label,color])]);legend(page.querySelector('[data-memory-legend]'),[['系统组件','--brand'],...processes.map(([label,,color])=>[label,color])]);
    const cores=page.querySelector('[data-cores]');cores.replaceChildren(...list(c.per_core).map((value,index)=>{const cell=make('span',percent(value),'overview-core');cell.title='核 '+index+'：'+percent(value)+(number(c.freq_per_core?.[index])!==null?' · '+c.freq_per_core[index]+' MHz':'');cell.style.setProperty('--usage',(number(value)===null?0:Math.min(100,value))+'%');return cell;}));
    const used=number(m.total)!==null&&number(m.available)!==null?Math.max(0,m.total-m.available):null;
    put('memory-used',formatBytes(used));put('memory-free',formatBytes(m.available));put('memory-total',formatBytes(m.total));
    const fraction=used!==null&&m.total>0?Math.min(1,used/m.total):0;page.querySelector('.overview-memory-bar i').style.width=fraction*100+'%';page.querySelector('.overview-memory-bar').setAttribute('aria-label','内存已用 '+(fraction*100).toFixed(1)+'%');
    const blocks=page.querySelector('[data-memory-blocks]');blocks.replaceChildren(...Array.from({length:80},(_,index)=>make('i',undefined,index<Math.round(fraction*80)?'used':'')));put('memory-block-size',number(m.total)!==null?'80 块 · 每块 ≈ '+formatBytes(m.total/80):'');
    rows(page.querySelector('[data-cpu-processes]'),[['系统运行时长',duration(data.uptime)],...processes.map(([label,key])=>[label,percent(c[key])])]);
    rows(page.querySelector('[data-memory-processes]'),processes.map(([label,key])=>[label,formatBytes(m[key+'_rss'])+' · '+duration(m[key+'_uptime'])]));
    rows(page.querySelector('[data-config]'),[['允许管理 IP 段',Array.isArray(data.acl?.admin_cidr)?data.acl.admin_cidr.join(', ')||'全部（建议限制可信网段）':'—',Array.isArray(data.acl?.admin_cidr)&&!data.acl.admin_cidr.length?'允许所有可到达网关的来源访问管理页面':undefined,Array.isArray(data.acl?.admin_cidr)&&!data.acl.admin_cidr.length?'overview-acl-warning':undefined],['允许代理客户端 IP 段（含透明 TCP）',Array.isArray(data.acl?.proxy_cidr)?data.acl.proxy_cidr.join(', ')||'全部':'—'],['信任 DNS',data.dns?.trusted?text(data.dns.trusted.ip)+':'+data.dns.trusted.port:'—'],['扩展 DNS',list(data.dns?.ex_dns).join(', ')||'—'],['系统 DNS',list(data.dns?.system).join(', ')||'—'],['FakeIP CIDR 段',text(data.fakeip?.cidr)],...(data.pplog?[['pplog 上报',text(data.pplog)]]:[])]);
    const featureValues=[['流量记录',features.net_rec],['DNS 硬烧录',features.dns_burn],['SOCKS5/HTTP '+(features.openport_port??'')+' 端口入站',features.openport],['启用 UDP',features.udp_enable],['禁止 BT 下载',data.route?.block_bittorrent],['禁止 QUIC 流量',data.route?.block_quic],['禁止未识别流量',data.route?.block_unknown]];
    page.querySelector('[data-features]').replaceChildren(...featureValues.map(([label,value])=>{const item=make('span',label+' · '+(typeof value==='boolean'?value?'开启':'关闭':'—'),'overview-feature');item.dataset.enabled=String(value===true);return item;}),make('span','记录上限 '+count(features.max_rec)+' · 自动清理 '+(features.net_cleanday===0?'关闭':count(features.net_cleanday)+' 天'),'overview-feature'));
    const pool=data.pool,debug=localStorage.getItem('debug')==='1';page.querySelector('[data-section=pool]').hidden=!pool||!debug;
    if(pool&&debug){const first=poolHistory[0],last=poolHistory.at(-1),seconds=first&&last?Math.round((last.at-first.at)/1000):0;
      page.querySelector('[data-pool]').replaceChildren(...[['空闲连接',count(pool.idle)+' / '+count(pool.min)+'（上限 '+count(pool.max)+'）'],['冷连接',count(pool.cold_acquires)],['丢弃失效',count(pool.discarded_dead)],['预热连接',count(pool.warmed)]].map(([label,value],index)=>{const card=make('div');card.append(make('small',label),make('strong',value));if(index&&seconds){const key=['','cold','dead','warmed'][index];card.append(make('span','近 '+seconds+' 秒 +'+(last[key]-first[key])));}return card;}));
      page.querySelector('[data-pool-blocks]').replaceChildren(...Array.from({length:Math.min(80,number(pool.min)||0)},(_,index)=>make('i',undefined,index<(number(pool.idle)||0)?'used':'')));
    }
    clock();
  }
  function accept(value,sample=true){
    if(number(value.cpu?.cloudflared)!==null||number(value.memory?.cloudflared_rss)!==null)cloudflaredSeen=true;
    const oldBoot=currentBoot();merge(value);const boot=currentBoot();if(oldBoot!==boot){history=[];poolHistory=[];previous=null;}
    const identity=[boot,data.version,data.mode,data.clash_version,data.integration?.core?.start_time].join('|');
    if(!cacheChecked){cacheChecked=true;const cached=overviewCache;overviewCache=null;if(cached&&boot!=='—'&&cached.auth===authIdentity&&cached.identity===identity&&Date.now()-cached.at<60000&&Date.now()>=cached.at){history=cached.history;poolHistory=cached.poolHistory;previous=cached.previous;cloudflaredSeen||=cached.cloudflaredSeen;}}
    if(number(value.timestamp)!==null)clockAt=performance.now();
    if(sample){
      const next=trafficSample(previous,value.traffic,performance.now(),boot);if(next){if(previous&&(next.down<previous.down||next.up<previous.up)){history=[];poolHistory=[];}previous=next;}else previous=null;
      const m=data.memory||{},c=data.cpu||{},core=m.ovpn_rss??m.clash_rss,total=number(m.total),free=number(m.available);
      history=[...history.slice(-39),{total:previous?.downRate!=null?previous.downRate+previous.upRate:null,down:previous?.downRate,up:previous?.upRate,cpu:c.usage,cpuSb:c.sniffbox,cpuCore:c.ovpn??c.clash,cpuCf:c.cloudflared,memSb:m.sniffbox_rss,memCore:core,memCf:m.cloudflared_rss,memOther:total!==null&&free!==null?Math.max(0,total-free-(m.sniffbox_rss||0)-(core||0)-(m.cloudflared_rss||0)):null}];
      poolHistory=poolWindow(poolHistory,data.pool,performance.now());
    }
    render();trafficChart.update(history);cpuChart.update(history,100,cloudflaredSeen?[...cpuSeries,['cpuCf','Cloudflared','--err']]:cpuSeries);memoryChart.update(history,data.memory?.total||undefined,cloudflaredSeen?[...memorySeries,['memCf','Cloudflared','--err']]:memorySeries);
  }
  async function request(url,signal){const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal});if(response.status===401){const error=new Error('登录已失效。');error.unauthorized=true;throw error;}if(!response.ok)throw Error('读取状态失败（HTTP '+response.status+'）。');const value=await response.json();if(!value||typeof value!=='object'||Array.isArray(value))throw Error('状态数据格式无效。');return value;}
  async function refresh(full=true){
    clearTimeout(timer);controller?.abort();const current=++sequence,active=new AbortController();controller=active;const timeout=setTimeout(()=>active.abort(),10000);button.disabled=true;paused=false;
    try{
      const [value,geo]=await Promise.all([request(full?'/sniffbox/info':'/sniffbox/info/dynamic',active.signal),full?request('/sniffbox/geo',active.signal).catch(()=>null):null]);if(disposed||current!==sequence)return;
      if(full){const version=geo?.version||list(geo?.files).find(file=>typeof file?.version==='string')?.version;put('geo',version?'GEO '+String(version).slice(0,10):'');}
      if(full){lastFull=performance.now();onInfo(value);}else{
        const boot=value.integration?.data?.boot_id??value.integration?.boot_id;
        if(boot&&currentBoot()!=='—'&&boot!==currentBoot()){const fresh=await request('/sniffbox/info',active.signal);if(disposed||current!==sequence)return;accept(fresh,false);lastFull=performance.now();onInfo(fresh);}
      }
      accept(value);status.hidden=true;
    }catch(error){if(disposed||current!==sequence)return;status.hidden=false;status.textContent=error.name==='AbortError'?'状态读取超时，稍后自动重试。':error.message;if(error.unauthorized){overviewCache=null;history=[];poolHistory=[];previous=null;paused=true;onUnauthorized();}}
    finally{clearTimeout(timeout);if(!disposed&&current===sequence){button.disabled=false;if(!paused)timer=setTimeout(()=>refresh(performance.now()-lastFull>30000),1000);}}
  }
  button.addEventListener('click',()=>refresh());
  const visibility=()=>{if(document.hidden){clearTimeout(timer);controller?.abort();sequence++;button.disabled=false;}else if(!disposed&&!paused)refresh();};document.addEventListener('visibilitychange',visibility);
  if(initialInfo)accept(initialInfo,false);else render();
  if(!document.hidden)refresh();const clockTimer=setInterval(clock,1000);
  return {refresh,dispose(){if(!paused&&authIdentity===sessionContext())overviewCache={auth:authIdentity,identity:[currentBoot(),data.version,data.mode,data.clash_version,data.integration?.core?.start_time].join('|'),at:Date.now(),history:history.slice(-40),poolHistory:poolHistory.slice(-40),previous,cloudflaredSeen};else overviewCache=null;disposed=true;sequence++;clearTimeout(timer);clearInterval(clockTimer);controller?.abort();document.removeEventListener('visibilitychange',visibility);trafficChart.dispose();cpuChart.dispose();memoryChart.dispose();host.replaceChildren();}};
}
