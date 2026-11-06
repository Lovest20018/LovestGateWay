import {monitorClient} from './monitor-client.js';
import {date} from './monitor-model.js';
import {levels,sourceName,stripAnsi,ansiSegments,logEvent,logBuffer} from './log-model.js';
import {el,button,input,select,page,download,streamState} from './monitor-view.js';

export function mountLogs(host,{onUnauthorized=()=>{}}={}) {
  const ui=page(host,'logs-page','实时日志'),core=logBuffer(),screen=logBuffer(),panes=el('div',undefined,'monitor-log-panes');
  let mode='core',vertical=false,scheduled=null,screenLoaded=false,screenAvailable=true,level=localStorage.getItem('ppgw-log-level')||'info';if(!Object.hasOwn(levels,level))level='info';
  const client=monitorClient({onUnauthorized,onPause:reason=>{if(reason!=='disposed')ui.message(reason==='unauthorized'?'登录已失效，日志内容已保留。':'页面已隐藏，实时日志已暂停。');},onResume:()=>ui.message('')});
  const source=select('日志视图',[['core','核心日志'],['screen','终端输出'],['both','同时显示']],value=>{mode=value;render();},mode),layout=button('纵向分屏',()=>{vertical=!vertical;render();}),refresh=button('重新连接',()=>client.refresh());
  ui.head.append(source,layout,refresh);ui.root.append(panes);
  const connectionState=el('p','正在连接…','monitor-stream-state');connectionState.setAttribute('role','status');ui.head.after(connectionState);
  const views=[];
  for(const [name,title,buffer]of [['core','核心日志',core],['screen','终端输出',screen]]){
    const section=el('section',undefined,'glass monitor-log-pane'),head=el('div',undefined,'monitor-head'),body=el('div',undefined,'monitor-log-body'),count=el('p',undefined,'monitor-hint');body.tabIndex=0;body.setAttribute('aria-label',title+'内容');
    const state={name,buffer,section,body,count,query:'',limit:400,follow:true};
    const search=input('搜索'+title,{onInput:value=>{state.query=value;render();}}),pause=button('暂停',()=>{buffer.pause(!buffer.paused);render();}),clear=button('清空'+title,()=>{buffer.clear();render();}),exportButton=button('导出'+title,()=>download(filtered(state).map(row=>name==='core'?'['+date(row.ts)+'] ['+sourceName(row.source)+'] ['+row.level+'] '+row.text:row.text).join('\n'),name+'-'+new Date().toISOString().replace(/[:]/g,'-')+'.txt'));
    const paneTitle=el('h2',title),toolbar=el('div',undefined,'monitor-log-toolbar'),actions=el('div',undefined,'monitor-log-actions');head.append(paneTitle,toolbar);toolbar.append(search,actions);actions.append(pause,clear,exportButton);state.pause=pause;state.title=paneTitle;
    if(name==='core')actions.append(select('最低日志级别',[['trace','Trace'],['debug','Debug'],['info','Info'],['warn','Warning'],['error','Error']],value=>{level=value;localStorage.setItem('ppgw-log-level',value);render();},level));
    actions.append(select(title+'显示条数',[['400','最近 400 行'],['2000','全部保留行']],value=>{state.limit=Number(value);render();},'400'));
    body.addEventListener('scroll',()=>{state.follow=body.scrollHeight-body.scrollTop-body.clientHeight<30;});body.addEventListener('dblclick',()=>{buffer.pause(!buffer.paused);render();});
    section.append(head,count,body);panes.append(section);views.push(state);
  }
  function filtered(state){return state.buffer.filter({query:state.query,level:state.name==='core'?level:'trace'});}
  function schedule(){if(scheduled===null)scheduled=setTimeout(()=>{scheduled=null;render();},200);}
  function render(){
    if(client.disposed)return;panes.classList.toggle('vertical',vertical);layout.hidden=mode!=='both';layout.textContent=vertical?'横向分屏':'纵向分屏';
    for(const state of views){state.title.hidden=mode!=='both';state.section.hidden=mode!=='both'&&mode!==state.name;state.pause.textContent=state.buffer.paused?'恢复':'暂停';state.pause.setAttribute('aria-pressed',String(state.buffer.paused));if(state.section.hidden)continue;
      const rows=filtered(state),visible=rows.slice(-state.limit),scroll=state.body.scrollTop;
      state.count.textContent=(state.name==='screen'&&!screenAvailable?'终端输出暂不可用。 · ':'')+'匹配 '+rows.length+' / 保留 '+state.buffer.rows.length+' 行（上限 2000）'+(rows.length>visible.length?' · 当前显示最近 '+visible.length+' 行':'')+(state.buffer.paused?' · 已暂停，待显示 '+state.buffer.pending+' 行':'');
      state.body.replaceChildren(...visible.map(row=>{const line=el('div',undefined,'monitor-log-line');line.dataset.level=row.level;if(state.name==='core')line.append(el('span','['+date(row.ts)+'] ['+sourceName(row.source)+'] ['+row.level+'] ','monitor-log-prefix'));
        for(const segment of ansiSegments(row.raw)){const span=el('span',segment.text);if(segment.fg)span.style.color=segment.fg;if(segment.bg)span.style.backgroundColor=segment.bg;if(segment.bold)span.style.fontWeight='700';if(segment.dim)span.style.opacity='.65';if(segment.italic)span.style.fontStyle='italic';if(segment.underline)span.style.textDecoration='underline';line.append(span);}return line;}));
      if(!visible.length)state.body.append(el('p',state.query?'当前筛选下暂无日志。':state.name==='core'?'暂无日志（当前级别：'+(level==='info'?'Info':level)+'）':'暂无终端输出。','monitor-log-empty'));
      if(state.follow)state.body.scrollTop=state.body.scrollHeight;else state.body.scrollTop=scroll;
    }
  }
  const coreStream=client.stream('/sniffbox/logs/sse',raw=>{const row=logEvent(raw);if(row){core.append([row]);schedule();}},{onState:state=>{connectionState.textContent=streamState(state);},onError:error=>ui.message(error.message)});
  const appendScreen=raw=>{screen.append(raw.replace(/\r\n/g,'\n').split('\n').filter((line,index,array)=>line||index<array.length-1).map(line=>({raw:line,text:stripAnsi(line),level:'info'})));schedule();};
  const screenReader=client.observe(async signal=>{
    if(!screenLoaded){const value=await client.request('/sniffbox/screen',{signal,consume:async response=>({available:response.headers.get('X-Screen-Status')!=='unavailable',text:await response.text()})});if(signal.aborted)return;screenLoaded=true;screenAvailable=value.available;if(screenAvailable){screen.clear();appendScreen(value.text);}render();}
    if(!screenAvailable)return;
    // A stream is owned by this job so switching panes or leaving aborts both stages.
    await client.request('/sniffbox/screen/sse',{signal,headerTimeoutOnly:true,headers:{Accept:'text/event-stream'},consume:async response=>{
      if(!response.headers.get('content-type')?.includes('text/event-stream')||!response.body)throw Error('终端实时响应无效。');
      const {eventDecoder}=await import('./monitor-model.js'),events=eventDecoder(raw=>{if(!signal.aborted)appendScreen(raw);}),decoder=new TextDecoder(),reader=response.body.getReader();let idle;const renew=()=>{clearTimeout(idle);idle=setTimeout(()=>reader.cancel().catch(()=>{}),45000);};renew();
      try{while(!signal.aborted){const chunk=await reader.read();if(chunk.done)break;renew();events.push(decoder.decode(chunk.value,{stream:true}));}}finally{clearTimeout(idle);await reader.cancel().catch(()=>{});reader.releaseLock();}
    }});
  },{interval:2000,onError:error=>ui.message(error.message)});
  render();return {expire(){client.expire();},refresh(){screenLoaded=false;client.refresh();},dispose(){clearTimeout(scheduled);client.dispose();host.replaceChildren();}};
}
