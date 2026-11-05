import test from 'node:test';
import assert from 'node:assert/strict';
import {formatBytes,trafficSample,poolWindow} from '../src/overview.js';

test('traffic rates require two valid monotonic samples and reset on core or counter changes',()=>{
  const first=trafficSample(null,{downloadTotal:1024,uploadTotal:512},1000,'first');
  assert.equal(first.downRate,null);
  const next=trafficSample(first,{downloadTotal:2048,uploadTotal:768},2000,'first');
  assert.equal(next.downRate,1024);assert.equal(next.upRate,256);
  assert.equal(trafficSample(next,{downloadTotal:2,uploadTotal:1},3000,'first').downRate,null);
  assert.equal(trafficSample(next,{downloadTotal:4096,uploadTotal:900},3000,'second').downRate,null);
  assert.equal(trafficSample(next,{downloadTotal:1},3000,'first'),null);
  for(const bad of [null,undefined,-1,Infinity,NaN,'1'])assert.equal(formatBytes(bad),'—');
  assert.equal(formatBytes(0,true),'0.0 B/s');assert.equal(formatBytes(1024),'1.0 KB');
});
test('pool activity never spans a counter reset and stays bounded',()=>{
  let history=[];
  for(let i=0;i<50;i++)history=poolWindow(history,{acquires:i,cold_acquires:i,discarded_dead:0,warmed:0},i*1000);
  assert.equal(history.length,40);assert.equal(history[0].cold,10);
  history=poolWindow(history,{acquires:0,cold_acquires:0,discarded_dead:0,warmed:0},51000);
  assert.equal(history.length,1);assert.deepEqual(poolWindow(history,null,52000),[]);
});
