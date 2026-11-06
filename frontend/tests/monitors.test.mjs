import test from 'node:test';
import assert from 'node:assert/strict';
import {eventDecoder,rateWindow,csv} from '../src/monitor-model.js';
import {connectionTracker} from '../src/connection-model.js';
import {trafficSnapshot,aggregateTraffic,filterTraffic,distributions,apex,nodeSnapshot} from '../src/traffic-model.js';
import {ansiSegments,stripAnsi,logBuffer,logEvent} from '../src/log-model.js';
import {regionTests,categories} from '../src/region-catalog.js';
import {valueOf,predicate,runRegionTest,probePool} from '../src/region-engine.js';

test('fragmented SSE framing preserves Unicode, multiline data, comments and CRLF',()=>{
  const output=[],events=eventDecoder(value=>output.push(value)),decoder=new TextDecoder(),bytes=new TextEncoder().encode(': heartbeat\r\ndata: 中文\r\ndata: second\r\n\r\ndata: []\n\n');
  for(const byte of bytes)events.push(decoder.decode(Uint8Array.of(byte),{stream:true}));assert.deepEqual(output,['中文\nsecond','[]']);
});
test('rate histories reset on counter and identity changes and remain bounded',()=>{const rates=rateWindow(3);assert.equal(rates.sample(10,2,1000).down,null);assert.equal(rates.sample(30,4,2000).total,22);assert.equal(rates.sample(1,1,3000).down,null);rates.sample(10,10,4000);assert.equal(rates.sample(20,20,5000,'new').down,null);for(let i=0;i<10;i++)rates.sample(30+i,30+i,6000+i*1000,'new');assert.equal(rates.history.down.length,3);rates.reset();assert.equal(rates.sample(100,100,20000).total,null);});
test('connection history is atomic, tracks disappearance, rates and resets',()=>{const tracker=connectionTracker();const row=(id,download)=>({id,metadata:{host:'example.com',sourceIP:'192.0.2.1'},download,upload:1,chains:['DIRECT'],start:'2026-01-01T00:00:00Z'});assert.equal(tracker.update({connections:[row('a',10)]},1000).active[0].dlSpeed,null);assert.equal(tracker.update({connections:[row('a',30)]},2000).active[0].dlSpeed,20);assert.throws(()=>tracker.update({connections:[row('x',1),row('x',2)]},3000));assert.equal(tracker.update({connections:[]},4000).closed[0].id,'a');for(let i=0;i<105;i++){tracker.update({connections:[row(String(i),1)]},5000+i*2000);tracker.update({connections:[]},6000+i*2000);}assert.equal(tracker.update({connections:[]},300000).closed.length,100);});
test('idle null connections close tracked rows while malformed responses preserve history',()=>{
 const tracker=connectionTracker();tracker.update({connections:[{id:'active',download:7,upload:2}]},1000);
 const idle=tracker.update({connections:null,downloadTotal:7,uploadTotal:2},2000);
 assert.deepEqual(idle.active,[]);assert.equal(idle.closed[0].id,'active');assert.equal(idle.closed[0].closedAt,2000);assert.equal(idle.downloadTotal,7);
 for(const malformed of [null,[],{}, {connections:{}},{connections:'empty'}])assert.throws(()=>tracker.update(malformed,3000));
 assert.equal(tracker.update({connections:null},4000).closed.length,1);
 assert.equal(tracker.update({connections:[{id:'new'}]},5000).active[0].id,'new');
});
test('traffic projection, filters, grouping and ranking preserve business meanings',()=>{
 const rows=trafficSnapshot([{client:'192.0.2.1',domain:'a.example.co.uk',down:10,up:2,conns:2,ts:1000,last_seen:8000,sniff:['tls'],private:'secret'},{client:'192.0.2.1',domain:'b.example.co.uk',down:30,up:3,conns:1,ts:2000,last_seen:6000,sniff:['Video']},{client:'192.0.2.2',domain:'a.example.co.uk',down:4,up:1,conns:3,ts:1000,last_seen:7000}]);
 assert.equal('private'in rows[0],false);const groups=aggregateTraffic(rows,'domain');assert.equal(groups[0].total,17);assert.equal(groups[0].clients.length,2);assert.equal(aggregateTraffic(rows,'client')[0].conns,3);assert.equal(filterTraffic(rows,{query:'192.0.2.1',now:9000,range:2000}).length,1);assert.equal(distributions(rows,'client')[0].duration,7000);assert.equal(distributions(rows,'service')[0].label,'example.co.uk');assert.equal(apex('2001:db8::1'),'2001:db8::1');assert.equal(nodeSnapshot({nodes:[{node:'a',up:1,down:2,total:99}]}).nodes[0].total,3);
 assert.match(csv([{x:'=HYPERLINK("x")'}],[{label:'x',value:row=>row.x}]),/"'=HYPERLINK/);
});
test('logs retain a bounded paused buffer and ANSI is text with safe color metadata',()=>{
 const raw='\x1b[31m<img src=x onerror=alert(1)>\x1b[0m';assert.equal(stripAnsi(raw),'<img src=x onerror=alert(1)>');const parts=ansiSegments(raw);assert.ok(parts[0].fg);assert.equal(parts[0].text,stripAnsi(raw));assert.equal(logEvent('invalid'),null);const buffer=logBuffer(3);buffer.append([logEvent({msg:'one',level:'info'}),logEvent({msg:'two',level:'debug'})]);buffer.pause(true);for(let i=0;i<5;i++)buffer.append([logEvent({msg:'line'+i,level:'error'})]);assert.equal(buffer.rows.length,2);assert.equal(buffer.pending,3);buffer.pause(false);assert.equal(buffer.rows.length,3);assert.equal(buffer.filter({query:'line4',level:'error'}).length,1);buffer.clear();assert.equal(buffer.pending,0);
});
test('59 detection definitions execute without unknown DSL expressions and retain dependencies',async()=>{
 assert.equal(regionTests.length,59);assert.equal(new Set(regionTests.map(test=>test.id)).size,59);assert.equal(categories.length,8);
 for(const definition of regionTests){const result=await runRegionTest(definition,async()=>({ok:true,status:200,body:'ip=192.0.2.3\nloc=US\n{}',ms:1,setCookie:[]}),new AbortController().signal);assert.ok(['yes','no','partial','info','fail'].includes(result.state),definition.id);}
 const steps={a:{ok:true,body:'{"country":"us"}',setCookie:['a=b; Secure','c=d; Path=/']}};assert.equal(valueOf({upper:{json:['a','country']}},steps),'US');assert.equal(valueOf({cookies:'a'},steps),'a=b; c=d');assert.equal(predicate({all:[{ok:'a'},{eq:[{json:['a','country']},'us']}]},steps),true);
 const visited=[];const result=await runRegionTest({id:'fixture',steps:[{id:'a',url:'https://example.com'},{id:'b',url:'https://example.com/next',needs:['a']}],rules:[{then:{state:'yes'}}]},async request=>{visited.push(request.url);return {ok:false,error:'offline'};});assert.equal(visited.length,1);assert.equal(result.state,'fail');
});
test('probe scheduling caps concurrency, retries only 429 and cancels queued work',async()=>{
 let active=0,max=0,calls=0;const pool=probePool(async(request,signal)=>{calls++;active++;max=Math.max(max,active);try{await new Promise((resolve,reject)=>{const timer=setTimeout(resolve,20);signal?.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('stop','AbortError'));},{once:true});});return request;}finally{active--;}});
 const abort=new AbortController(),pending=Array.from({length:30},(_,id)=>pool(id,abort.signal));setTimeout(()=>abort.abort(),5);const settled=await Promise.allSettled(pending);assert.equal(max,5);assert.equal(calls,5);assert.ok(settled.every(row=>row.status==='rejected'));assert.equal(await pool('next'), 'next');
 let attempts=0;const retry=probePool(async()=>{if(++attempts<3)throw Object.assign(Error('busy'),{status:429});return 'ok';});assert.equal(await retry({}),'ok');assert.equal(attempts,3);attempts=0;await assert.rejects(probePool(async()=>{attempts++;throw Object.assign(Error('invalid'),{status:400});})({}));assert.equal(attempts,1);
});
