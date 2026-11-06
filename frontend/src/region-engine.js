const asText=value=>value==null?'':String(value);
const empty=value=>value==null||value===''||value===false;
const equal=(left,right)=>left==null&&right==null||asText(left)===asText(right);
const states=new Set(['yes','no','partial','info','fail']);
function regex(value) {return value instanceof RegExp?value:typeof value?.pattern==='string'?new RegExp(value.pattern,value.flags||''):new RegExp(value);}
function path(value,key) {for(const part of String(key).split('.')){if(value==null||!Object.hasOwn(Object(value),part))return undefined;value=value[part];}return value;}
function json(step) {if(!step)return null;if(!Object.hasOwn(step,'_json')){try{step._json=JSON.parse(step.body??'');}catch{step._json=null;}}return step._json;}
export function valueOf(expression,steps) {
  if(expression==null)return '';
  if(typeof expression!=='object'||expression instanceof RegExp)return expression;
  if('$'in expression)return path(steps,expression.$)??'';
  if('re'in expression){const [source,pattern,group=1]=expression.re,match=asText(valueOf(source,steps)).match(regex(pattern));return match?match[group]??'':'';}
  if('json'in expression){const [step,key]=expression.json;return path(json(steps[step]),key)??'';}
  if('cookies'in expression){const cookies=new Map();for(const cookie of steps[expression.cookies]?.setCookie??[]){const first=cookie.split(';')[0].trim(),at=first.indexOf('=');if(at>0)cookies.set(first.slice(0,at),first.slice(at+1));else if(first)cookies.set(first,'');}return [...cookies].map(([key,value])=>value?key+'='+value:key).join('; ');}
  for(const kind of ['upper','lower','trim'])if(kind in expression){const value=asText(valueOf(expression[kind],steps));return kind==='upper'?value.toUpperCase():kind==='lower'?value.toLowerCase():value.trim();}
  if('first'in expression){for(const item of expression.first){const value=valueOf(item,steps);if(!empty(value))return value;}return '';}
  if('wikiDuration'in expression){const blocks=path(json(steps[expression.wikiDuration]),'query.blocks');if(!Array.isArray(blocks))return '';const values=[...new Set(blocks.map(row=>row['duration-l10n']).filter(Boolean))];for(const candidate of ['无限','1年','3个月','7天','1天'])if(values.includes(candidate))return candidate;return values.sort((a,b)=>a.length-b.length)[0]||'';}
  if('concat'in expression)return expression.concat.map(value=>asText(valueOf(value,steps))).join('');
  if('map'in expression){const [source,mapping,fallback]=expression.map,key=asText(valueOf(source,steps));return Object.hasOwn(mapping,key)?mapping[key]:fallback!==undefined?valueOf(fallback,steps):key;}
  if('if'in expression){const [condition,yes,no='']=expression.if;return valueOf(predicate(condition,steps)?yes:no,steps);}
  throw Error('无法识别检测值表达式。');
}
export function predicate(expression,steps) {
  if(typeof expression==='boolean')return expression;
  if(!expression||typeof expression!=='object')throw Error('检测条件无效。');
  if('has'in expression){const [source,part]=expression.has;return asText(valueOf(source,steps)).includes(part);}
  if('hasI'in expression){const [source,part]=expression.hasI;return asText(valueOf(source,steps)).toLowerCase().includes(part.toLowerCase());}
  if('match'in expression){const [source,pattern]=expression.match;return regex(pattern).test(asText(valueOf(source,steps)));}
  if('eq'in expression)return equal(...expression.eq.map(value=>valueOf(value,steps)));
  if('in'in expression){const [source,values]=expression.in,value=valueOf(source,steps);return values.some(candidate=>equal(value,candidate));}
  if('present'in expression)return !empty(valueOf(expression.present,steps));
  if('empty'in expression)return empty(valueOf(expression.empty,steps));
  if('ok'in expression)return !!steps[expression.ok]?.ok;
  if('failed'in expression)return !steps[expression.failed]?.ok;
  if('anyFailed'in expression)return expression.anyFailed.some(key=>!steps[key]?.ok);
  if('allFailed'in expression)return expression.allFailed.every(key=>!steps[key]?.ok);
  if('any'in expression)return expression.any.some(value=>predicate(value,steps));
  if('all'in expression)return expression.all.every(value=>predicate(value,steps));
  if('not'in expression)return !predicate(expression.not,steps);
  throw Error('无法识别检测条件。');
}
const randomHex=size=>{const values=new Uint8Array(Math.ceil(size/2));crypto.getRandomValues(values);return [...values].map(value=>value.toString(16).padStart(2,'0')).join('').slice(0,size);};
export function probeRequest(step,steps,now=Date.now()) {
  if(step.udp)return {udp:true};
  const vars={rand32:randomHex(32),rand16:randomHex(16),ts:String(now),uuid:randomHex(8)+'-'+randomHex(4)+'-4'+randomHex(3)+'-a'+randomHex(3)+'-'+randomHex(12)};
  for(const [key,expression]of Object.entries(step.vars??{}))vars[key]=asText(valueOf(expression,steps));
  const substitute=value=>typeof value==='string'?value.replace(/\$\{(\w+)\}/g,(_,key)=>vars[key]??''):value;
  const request={url:substitute(step.url)};
  for(const key of ['method','body'])if(step[key]!=null)request[key]=substitute(step[key]);
  if(step.headers)request.headers=Object.fromEntries(Object.entries(step.headers).map(([key,value])=>[key,substitute(value)]));
  if(step.ua&&step.ua!=='browser')request.ua=step.ua==='dalvik'?'Dalvik/2.1.0 (Linux; U; Android 9; ALP-AL00 Build/HUAWEIALP-AL00)':step.ua;
  for(const key of ['follow','timeoutMs','maxBody'])if(step[key]!==undefined)request[key]=step[key];
  return request;
}
export async function runRegionTest(test,probe,signal,shared={}) {
  const results=Object.create(null),finished=new Set();
  const check=()=>{if(signal?.aborted)throw new DOMException('检测已停止。','AbortError');};
  for(const step of test.steps){const cached=step.sharedKey?shared[step.sharedKey]:null;if(cached?.ip){results[step.id]={ok:true,body:'ip='+cached.ip+'\nloc='+(cached.loc||''),ms:0};finished.add(step.id);}}
  let remaining=test.steps.filter(step=>!finished.has(step.id));
  while(remaining.length){
    check();const ready=remaining.filter(step=>(step.needs??[]).every(key=>finished.has(key)));if(!ready.length)throw Error('检测步骤存在循环依赖。');
    await Promise.all(ready.map(async step=>{
      check();if(!(step.needs??[]).every(key=>results[key]?.ok)){results[step.id]={ok:false,error:'依赖失败，已跳过'};return;}
      try{results[step.id]=await probe(probeRequest(step,results),signal);}
      catch(error){if(error.name==='AbortError'||error.unauthorized||error.unavailable)throw error;results[step.id]={ok:false,error:error.message};}
    }));check();for(const step of ready)finished.add(step.id);remaining=remaining.filter(step=>!finished.has(step.id));
  }
  let verdict;
  if(test.steps.length&&test.steps.every(step=>!results[step.id]?.ok)){const first=results[test.steps[0].id]||{};verdict={state:'fail',label:(first.denied?'规则错误：':'')+(first.error||'网络错误'),region:''};}
  else for(const rule of test.rules)if(rule.when==null||predicate(rule.when,results)){
    const outcome=rule.then;if(!states.has(outcome.state))throw Error('检测结果状态无效。');verdict={state:outcome.state};
    for(const key of ['label','region','ip','tooltip'])verdict[key]=asText(valueOf(outcome[key],results));
    if(outcome.extra)verdict.extra=Object.fromEntries(Object.entries(outcome.extra).map(([key,value])=>[key,asText(valueOf(value,results))]));
    if(outcome.ms!=null){const ms=Number.parseInt(asText(valueOf(outcome.ms,results)),10);if(Number.isFinite(ms))verdict.ms=ms;}
    break;
  }
  return {id:test.id,name:test.name,cat:test.cat,ms:Object.values(results).reduce((sum,row)=>sum+(Number.isFinite(row?.ms)?row.ms:0),0),...(verdict||{state:'fail',label:'无规则命中',region:''})};
}
export function probePool(send,{limit=5,retries=3}={}) {
  let active=0;const queue=[];
  const aborted=()=>new DOMException('检测已停止。','AbortError');
  async function execute(request,signal) {
    for(let attempt=0;attempt<retries;attempt++){
      if(signal?.aborted)throw aborted();
      try{return await send(request,signal);}
      catch(error){if(error.status!==429||attempt===retries-1)throw error;await new Promise((resolve,reject)=>{const abort=()=>{clearTimeout(timer);reject(aborted());},timer=setTimeout(()=>{signal?.removeEventListener('abort',abort);resolve();},300*(attempt+1));signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();});}
    }
  }
  function drain(){while(active<limit&&queue.length){const entry=queue.shift();entry.signal?.removeEventListener('abort',entry.abort);if(entry.signal?.aborted){entry.reject(aborted());continue;}active++;execute(entry.request,entry.signal).then(entry.resolve,entry.reject).finally(()=>{active--;drain();});}}
  return (request,signal)=>new Promise((resolve,reject)=>{if(signal?.aborted){reject(aborted());return;}const entry={request,signal,resolve,reject};entry.abort=()=>{const index=queue.indexOf(entry);if(index>=0){queue.splice(index,1);reject(aborted());}};signal?.addEventListener('abort',entry.abort,{once:true});queue.push(entry);drain();});
}
