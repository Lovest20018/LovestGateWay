import {eventDecoder} from './monitor-model.js';

export function monitorClient({onUnauthorized=()=>{}, onPause=()=>{}, onResume=()=>{}}={}) {
  let disposed = false, unauthorized = false, suspended = false;
  const requests = new Set(), jobs = new Set();
  const active = () => !disposed && !unauthorized && !suspended && !document.hidden;
  function stopJobs(reason) {
    for (const job of jobs) { clearTimeout(job.timer); job.controller?.abort(); job.state?.(reason); }
  }
  function expire() {
    if (unauthorized || disposed) return;
    unauthorized = true; stopJobs('unauthorized'); onPause('unauthorized'); onUnauthorized();
  }
  async function request(url, {method='GET', body, signal, timeout=15000, headerTimeoutOnly=false, consume, headers={}}={}) {
    if (disposed || unauthorized) throw Object.assign(Error('登录已失效，请重新登录。'), {unauthorized:true});
    const controller = new AbortController(), abort = () => controller.abort();
    requests.add(controller); signal?.addEventListener('abort',abort,{once:true}); if (signal?.aborted) abort();
    let timedOut=false;
    const timer = timeout ? setTimeout(()=>{timedOut=true;abort();},timeout) : null;
    try {
      const response = await fetch(url, {method, signal:controller.signal, cache:'no-store', headers:{ ...headers, ...(body!==undefined?{'Content-Type':'application/json'}:{})}, ...(body!==undefined?{body:JSON.stringify(body)}:{})});
      if(headerTimeoutOnly)clearTimeout(timer);
      if (response.status === 401) { expire(); throw Object.assign(Error('登录已失效，请重新登录后读取最新状态。'), {unauthorized:true}); }
      if (!response.ok) throw Object.assign(Error(response.status===429?'后端繁忙，请稍后手动重试。':'请求失败（HTTP '+response.status+'）。'), {status:response.status});
      if (signal?.aborted || disposed) throw new DOMException('已停止。','AbortError');
      if (consume) return await consume(response,controller.signal);
      if (response.status===204) return null;
      const raw = await response.text(); return raw ? JSON.parse(raw) : null;
    } catch(error) {if(timedOut&&!signal?.aborted)throw Object.assign(Error('请求超时，响应未确认，请刷新核对。'),{name:'TimeoutError'});throw error;}
    finally { clearTimeout(timer); signal?.removeEventListener('abort',abort); requests.delete(controller); }
  }
  function observe(work, {interval=1000, enabled=true, onError=()=>{}, state}={}) {
    const job = {enabled, timer:null, controller:null, running:false, rerun:false, state}; jobs.add(job);
    const run = async () => {
      clearTimeout(job.timer);
      if (!active() || !job.enabled) return;
      if (job.running) { job.rerun=true; return; }
      job.running=true; const controller = new AbortController(); job.controller=controller;
      try { await work(controller.signal); }
      catch (error) { if (!disposed && !controller.signal.aborted && error.name!=='AbortError' && !error.unauthorized) onError(error); }
      finally {
        job.running=false; job.controller=null;
        if (active() && job.enabled) { const delay=job.rerun?0:interval; job.rerun=false; job.timer=setTimeout(run,delay); }
      }
    };
    job.run=run;
    queueMicrotask(run);
    return {refresh:run, setEnabled(value) { job.enabled=value; if (value) run(); else { job.rerun=false; clearTimeout(job.timer); job.controller?.abort(); state?.('paused'); } }};
  }
  function poll(url,onValue,options={}) {
    return observe(async signal => { const value=await request(url,{signal}); if (!signal.aborted && !disposed) onValue(value); },options);
  }
  function stream(url,onData,{onState=()=>{},onError=()=>{},enabled=true}={}) {
    let failures=0;
    return observe(async signal => {
      onState(failures?'reconnecting':'connecting');
      try {
        await request(url,{signal,headerTimeoutOnly:true,headers:{Accept:'text/event-stream'},consume:async (response,requestSignal)=>{
          if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw Error('实时数据响应无效，请重新连接。');
          const reader=response.body.getReader(), decoder=new TextDecoder(), events=eventDecoder(value=>{if(!signal.aborted&&!disposed&&!unauthorized)onData(value);});
          let idle;
          const renew=()=>{clearTimeout(idle);idle=setTimeout(()=>reader.cancel().catch(()=>{}),45000);};
          onState('open'); failures=0; renew();
          try { while (!signal.aborted&&!requestSignal.aborted) { const chunk=await reader.read(); if(chunk.done)break; renew(); events.push(decoder.decode(chunk.value,{stream:true})); } }
          finally { clearTimeout(idle); await reader.cancel().catch(()=>{}); reader.releaseLock(); }
        }});
      } catch (error) { if (error.name!=='AbortError'&&!error.unauthorized) { failures++; onError(error); } }
      finally { if(active()&&!signal.aborted)onState('reconnecting'); }
    },{interval:2000,enabled,state:onState,onError});
  }
  const visibility=()=>{if(document.hidden){stopJobs('paused');onPause('hidden');}else if(!disposed&&!unauthorized){onResume();for(const job of jobs)if(job.enabled)job.run();}};
  document.addEventListener('visibilitychange',visibility);
  return {
    request,poll,stream,observe,expire,
    get disposed(){return disposed;}, get unauthorized(){return unauthorized;}, get active(){return active();},
    suspend(){suspended=true;stopJobs('paused');},
    resume(){suspended=false;if(active())for(const job of jobs)if(job.enabled)job.run();},
    refresh(){if(disposed)return;unauthorized=false;suspended=false;onResume();for(const job of jobs)if(job.enabled){job.controller?.abort();job.run();}},
    dispose(){disposed=true;stopJobs('closed');for(const controller of requests)controller.abort();document.removeEventListener('visibilitychange',visibility);onPause('disposed');jobs.clear();}
  };
}
