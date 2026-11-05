import test from 'node:test';
import assert from 'node:assert/strict';
import {rulesSnapshot,providersSnapshot,rulePage,proxyChain,matchLog,testAddress,geoResult} from '../src/rule-model.js';

test('rule toggle addresses retain backend indexes and reject ambiguous or malformed indexes',()=>{
  const rows=rulesSnapshot({rules:[null,{type:'Domain',payload:'a',proxy:'Main',extra:{}},{index:40,type:'Match',extra:{disabled:true}},{index:40,extra:{}},{index:'41',extra:{}},{index:-1,extra:{}},{index:42}]});
  assert.equal(rows[0].index,1);assert.equal(rows[0].canToggle,true);assert.equal(rows[1].canToggle,false);assert.equal(rows[2].canToggle,false);assert.equal(rows[3].canToggle,false);assert.equal(rows[4].canToggle,false);assert.equal(rows[5].canToggle,false);
  assert.throws(()=>rulesSnapshot({}));assert.equal(providersSnapshot({providers:{'Sub /?#':{ruleCount:9}}})[0].name,'Sub /?#');
});
test('search and pagination preserve original order and clamp shrinking results',()=>{
  const rows=rulesSnapshot({rules:Array.from({length:201},(_,index)=>({index:index*2,type:'DomainSuffix',payload:'fixture-'+index+'.example',proxy:index%2?'Main':'DIRECT'}))});
  assert.equal(rulePage(rows).rows.length,100);assert.equal(rulePage(rows,{page:3}).rows[0].index,400);
  const found=rulePage(rows,{query:'199',page:8,pageSize:'20'});assert.equal(found.page,1);assert.equal(found.rows[0].index,398);
  assert.equal(rulePage(rows,{located:400,query:'not-found'}).rows[0].payload,'fixture-200.example');
  assert.equal(rulePage(rows,{pageSize:'all'}).rows.length,201);assert.equal(rulePage(rows,{query:'missing',page:3}).pages,1);
});
test('proxy traversal is bounded through cycles and match logs locate the exact normalized rule',()=>{
  const proxies={Main:{all:['Auto'],now:'Auto'},Auto:{all:['Node'],now:'Node'},Node:{type:'Trojan'}};
  assert.deepEqual(proxyChain(proxies,'Main'),['Auto','Node']);proxies.Auto.now='Main';assert.deepEqual(proxyChain(proxies,'Main'),['Auto']);
  const rows=rulesSnapshot({rules:[{type:'DomainSuffix',payload:'example.com',proxy:'Main'},{type:'Domain',payload:'example.com',proxy:'DIRECT'}]});
  const result=matchLog([{payload:'[TCP] 127.0.0.1:1000 --> example.com:443 match DOMAIN-SUFFIX(example.com) using Main'}],rows);assert.equal(result.row,rows[0]);assert.equal(result.chain,'Main');
  assert.equal(matchLog([{payload:"[TCP] x --> host:80 doesn't match any rule using DIRECT"}],rows).none,true);
  assert.equal(matchLog([{payload:'unrelated'}],rows),null);assert.equal(matchLog([{payload:'x --> host:80 match DomainSuffix(other.com) using Main'}],rows).row,null);
  const duplicates=rulesSnapshot({rules:[{type:'DomainSuffix',payload:'example.com',proxy:'Main'},{type:'DomainSuffix',payload:'example.com',proxy:'Other'}]});
  assert.equal(matchLog([{payload:'x --> host:80 match DomainSuffix(example.com) using Other'}],duplicates).row,duplicates[1]);
  assert.equal(matchLog([{payload:'x --> host:80 match DomainSuffix(example.com) using Unknown[Node]'}],duplicates).row,null);
});
test('active tests validate normalized URLs and GEO results report mixed failures accurately',()=>{
  assert.equal(testAddress('example.com/path?q=1'),'http://example.com/path?q=1');assert.equal(testAddress('https://[2001:db8::1]/'),'https://[2001:db8::1]/');
  for(const value of ['', 'ftp://example.com','https://u:p@example.com','https://example.com/'+ 'x'.repeat(2048)])assert.throws(()=>testAddress(value));
  assert.equal(geoResult({files:[{name:'geoip',status:'downloaded'},{name:'geosite',status:'failed'}]}).complete,false);
  const failed=geoResult({files:[{name:'geoip',status:'fetch_failed'},{name:'geosite',status:'download_failed'}]});assert.equal(failed.complete,false);assert.match(failed.message,/获取版本失败.*下载失败/);
  assert.equal(geoResult({files:[{name:'geoip',status:'uptodate'},{name:'mmdb',status:'skipped'}]}).complete,true);
  assert.equal(geoResult({}).complete,false);assert.equal(geoResult({files:[{status:'future'}]}).files[0].status,'unknown');
});
