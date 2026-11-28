import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {sha256} from '../src/auth.js';
import {documentModel,exported,move,groupMode,setGroupMode,setGroupFailover,setRuleType} from '../src/model.js';
import {maskUrl} from '../src/url-privacy.js';
test('subscription summaries hide path, query, userinfo, fragment and malformed credentials',()=>{
 for(const value of ['https://user:synthetic-password@example.com/path-token?key=query-token#fragment-token','not-a-url-synthetic-token','ss://synthetic-token']){
  const masked=maskUrl(value);assert.doesNotMatch(masked,/synthetic|path-token|query-token|fragment-token|user:/);
 }
 assert.match(maskUrl('https://example.com/secret'),/^https:\/\/example.com\/•••$/);
});
test('new PPsub rule providers default to automatic format detection',()=>{
 const ppsub={};
 setRuleType(ppsub,'rule-set');
 assert.deepEqual(ppsub,{type:'rule-set',url:'',behavior:'classical',format:'auto',interval:86400});
 const existing={behavior:'domain',format:'text',interval:7200};
 setRuleType(existing,'rule-set');
 assert.deepEqual(existing,{type:'rule-set',url:'',behavior:'domain',format:'text',interval:7200});
});
test('PPsub has three group modes and failover stays within manual mode',()=>{
 for(const [row,mode] of [
  [{},'manual'],[{failover:true},'manual'],[{speedtest_url:'https://example.test/204'},'speedtest'],
  [{interval:600},'speedtest'],[{smart:true,speedtest_url:'https://example.test/204',failover:true},'smart'],
 ]) assert.equal(groupMode(row),mode);

 const original={name:'Main',failover:true,monitor_url:'https://example.test/health',custom:{flag:1}};
 for(const [mode,fields] of [
  ['speedtest',{speedtest_url:'https://www.gstatic.com/generate_204',interval:600}],['smart',{smart:true}],
 ]){
  const row=structuredClone(original);
  setGroupMode(row,'manual');
  assert.deepEqual(row,original);
  setGroupMode(row,mode);
  const {failover,...retained}=original;
  assert.deepEqual(row,{...retained,...fields});
  assert.equal(groupMode(row),mode);
  setGroupMode(row,'manual');
  assert.deepEqual(row,retained);
 }
});
test('manual failover fills missing defaults and keeps health settings on disable',()=>{
 const row={name:'Manual',custom:{enabled:true}};
 setGroupFailover(row,true);
 assert.deepEqual(row,{name:'Manual',custom:{enabled:true},failover:true,
  monitor_url:'https://www.youtube.com/generate_204',expected_status:'0',retries:3});
 assert.equal(groupMode(row),'manual');

 Object.assign(row,{monitor_url:'https://example.test/check',expected_status:'204',retries:0});
 setGroupFailover(row,false);
 assert.equal(Object.hasOwn(row,'failover'),false);
 setGroupFailover(row,true);
 assert.deepEqual(row,{name:'Manual',custom:{enabled:true},failover:true,
  monitor_url:'https://example.test/check',expected_status:'204',retries:0});
});
test('HTTP LAN password hash agrees with server SHA256 UTF8',()=>{for(const value of ['', 'abc','网关管理密码','a'.repeat(1000)])assert.equal(sha256(value),createHash('sha256').update(value).digest('hex'));});
test('editing and moving preserve untouched identities, optional secrets, and unknown nested fields',()=>{const value={version:'custom',custom:{a:[1,2]},subs:[{_clash_id:'s:0',name:'test',url:'https://example.invalid',custom:{headers:['a']}}],'node-groups':[{_clash_id:'g:0',name:'a',hidden:123,monitor_url:'https://example.invalid'}],rules:[{_clash_id:'p:rules',fixrule:['MATCH,a']},{_clash_id:'rules',fixrule:['DOMAIN,a,a']}]};const doc=documentModel(value);doc.subs[0].name='renamed';move(doc.rules,0,1);setGroupMode(doc['node-groups'][0],'smart');const result=exported(doc);assert.deepEqual(result.subs[0].custom,value.subs[0].custom);assert.equal(result.subs[0]._clash_id,'s:0');assert.equal(result['node-groups'][0].hidden,123);assert.equal(result['node-groups'][0].monitor_url,'https://example.invalid');assert.equal(result.rules[1]._clash_id,'p:rules');assert.deepEqual(result.custom,value.custom);assert.equal(value.subs[0].name,'test');});
