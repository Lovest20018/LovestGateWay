import test from 'node:test';
import assert from 'node:assert/strict';
import {monitorClient} from '../src/monitor-client.js';

const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check){for(let i=0;i<100;i++){if(check())return;await wait(5);}assert.fail('Expected lifecycle condition did not occur');}

test('shared monitoring cancels hidden/disposed reads, handles timeouts and stops all work on 401',async()=>{
  const original={document:globalThis.document,localStorage:globalThis.localStorage,fetch:globalThis.fetch},document=new EventTarget();document.hidden=false;globalThis.document=document;globalThis.localStorage={getItem:()=>JSON.stringify({clashAPIConfigs:[{secret:'fixture'}]})};
  let calls=0,aborts=0,expired=0,status=200,values=0;
  globalThis.fetch=(url,options)=>{assert.equal(options.headers.Authorization,undefined);assert.equal(url.includes('fixture'),false);calls++;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>resolve(new Response(JSON.stringify({value:1}),{status})),10);options.signal.addEventListener('abort',()=>{aborts++;clearTimeout(timer);reject(new DOMException('stop','AbortError'));},{once:true});});};
  const client=monitorClient({onUnauthorized:()=>expired++});
  try{
    client.poll('/sample',()=>values++,{interval:10});await until(()=>values>=1);document.hidden=true;document.dispatchEvent(new Event('visibilitychange'));const count=calls;await wait(35);assert.equal(calls,count);document.hidden=false;document.dispatchEvent(new Event('visibilitychange'));await until(()=>values>=2);
    await assert.rejects(client.request('/timeout',{timeout:1}),error=>error.name==='TimeoutError');
    status=401;await until(()=>expired===1);const stopped=calls;await wait(40);assert.equal(calls,stopped);await assert.rejects(client.request('/write',{method:'POST'}),error=>error.unauthorized);assert.equal(calls,stopped);
    status=200;client.refresh();await until(()=>values>=3);client.dispose();const disposed=calls;await wait(30);assert.equal(calls,disposed);assert.ok(aborts>=1);
  }finally{client.dispose();Object.assign(globalThis,original);}
});

test('refresh replaces a live event stream and disposal ignores late events',async()=>{
  const original={document:globalThis.document,localStorage:globalThis.localStorage,fetch:globalThis.fetch},document=new EventTarget();document.hidden=false;globalThis.document=document;globalThis.localStorage={getItem:()=>JSON.stringify({clashAPIConfigs:[{secret:'fixture'}]})};
  let opened=0,closed=0;const values=[];
  globalThis.fetch=async(url,options)=>{opened++;let ended=false;return new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('data: 中文\n\n'));options.signal.addEventListener('abort',()=>{if(!ended){ended=true;closed++;controller.error(new DOMException('stop','AbortError'));}},{once:true});},cancel(){if(!ended){ended=true;closed++;}}}),{headers:{'Content-Type':'text/event-stream'}});};
  const client=monitorClient();try{client.stream('/events',value=>values.push(value));await until(()=>values.length===1);client.refresh();await until(()=>values.length===2);assert.equal(opened,2);assert.equal(closed,1);client.dispose();await until(()=>closed===2);assert.deepEqual(values,['中文','中文']);}finally{client.dispose();Object.assign(globalThis,original);}
});
