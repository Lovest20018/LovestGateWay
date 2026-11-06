import test from 'node:test';
import assert from 'node:assert/strict';
import {snapshot,preferences,groups,members,delay,state,nodeLabel,testPlan,testPath,testUrl,segment,usage} from '../src/proxy-model.js';

const raw={mode:'rule',testUrl:'https://example.test/default',subs:[],proxies:{
  GLOBAL:{type:'Selector',all:['Auto','Main','A','DIRECT','REJECT'],now:'Main'},
  Main:{type:'Selector',all:['A','B','C','A','Usage_Usage: 4G/10G@@2027-01-01'],now:'A'},
  Auto:{type:'URLTest',all:['A','B'],now:'B',testUrl:'https://example.test/group',scores:{A:{score:55}}},
  Hidden:{type:'Selector',all:['B'],hidden:true},A:{type:'Trojan',delay:99,extra:{'https://example.test/group':51}},B:{type:'Shadowsocks',alive:false,delay:0},C:{type:'Vless'},DIRECT:{type:'Direct'},REJECT:{type:'Reject'},
  'Usage_Usage: 4G/10G@@2027-01-01':{type:'Shadowsocks'}},providers:{'Sub /?':{vehicleType:'HTTP',proxies:['A','B','A']},default:{vehicleType:'Compatible',proxies:['C']}}};

test('legacy usage rows never become switchable candidates and snapshot order follows GLOBAL',()=>{
  const data=snapshot(raw);assert.deepEqual(data.proxies.Main.all,['A','B','C']);assert.equal(data.subs[0].name,'Usage');assert.equal(data.subs[0].download,6*1024**3);
  assert.deepEqual(groups(data),['Auto','Main','Hidden','GLOBAL']);data.mode='global';assert.deepEqual(groups(data),['GLOBAL']);
  assert.deepEqual(members(data,'GLOBAL'),['A','DIRECT','REJECT']);assert.equal(raw.proxies.Main.all.length,5);
  const hostile=snapshot(JSON.parse('{"proxies":{"__proto__":{"type":"Selector","all":["constructor"]},"constructor":{"type":"Direct"}}}'));
  assert.equal(Object.hasOwn(hostile.proxies,'__proto__'),true);assert.deepEqual(groups(hostile),['__proto__']);
});
test('group-specific delay, unknown state and offline filtering keep independent meanings',()=>{
  const data=snapshot(raw),prefs=preferences({sort:'latDesc',hideUnavail:true,gsort:{},gcollapse:{},unrelated:'preserved'});
  assert.equal(delay(data,'A','Auto'),51);assert.equal(delay(data,'A','Main'),99);assert.equal(state(data,'C','Main'),'unknown');
  assert.deepEqual(members(data,'Main',{},prefs),['A','C']);assert.deepEqual(members(data,'Main',{status:'offline'},preferences({})),['B']);
  assert.equal(prefs.unrelated,'preserved');prefs.gcollapse.__proto__=true;assert.equal(prefs.gcollapse.__proto__,true);
  assert.equal(nodeLabel('Sub_🇯🇵 Tokyo@subdns1').display,'Tokyo');assert.equal(nodeLabel('Sub_🇯🇵 Tokyo@subdns1').dns,'subdns1');assert.equal(nodeLabel('Sub_🇯🇵 Tokyo@subdns1').code,'JP');
  assert.deepEqual(members(snapshot({proxies:{Main:{all:['🇯🇵 Tokyo','HK One','US Two']}}}),'Main',{region:'JP'}),['🇯🇵 Tokyo']);
});
test('batch plan preserves each test URL and encodes provider and node names as single path segments',()=>{
  const data=snapshot(raw),prefs=preferences({});const plan=testPlan(data,prefs);
  assert.ok(plan.some(row=>row.name==='A'&&row.url===raw.testUrl));assert.ok(plan.some(row=>row.name==='A'&&row.url===raw.proxies.Auto.testUrl));
  assert.equal(plan.filter(row=>row.name==='A'&&row.url===raw.testUrl).length,1);assert.equal(plan.some(row=>row.name==='REJECT'),false);
  assert.equal(testPath(data,'A','https://example.test/a?b=1'),'/providers/proxies/Sub%20%2F%3F/A/healthcheck?url=https%3A%2F%2Fexample.test%2Fa%3Fb%3D1&timeout=5000');
  assert.equal(segment("节点/?#'"),'%E8%8A%82%E7%82%B9%2F%3F%23%27');assert.throws(()=>segment('..'));
  for(const value of ['javascript:alert(1)','https://user:pass@example.test','not a url'])assert.throws(()=>testUrl(data,'Main',preferences({testUrl:value})));
  assert.equal(testUrl(data,'Auto',preferences({testUrl:'javascript:invalid'})),raw.proxies.Auto.testUrl);
});
test('missing usage numbers are never presented as a fully unused subscription',()=>{
  assert.deepEqual(usage({total:100,expire:0}),{total:100,up:null,down:null,used:null,left:null,expire:null});
  assert.equal(usage({total:100,upload:30,download:80}).left,0);assert.equal(usage({total:NaN,upload:0,download:0}).used,0);
});

test('information node filtering is explicit and never changes the snapshot',()=>{const data=snapshot({proxies:{Main:{type:'Selector',all:['剩余流量：10 GB','香港流量节点']},'剩余流量：10 GB':{type:'Shadowsocks'},'香港流量节点':{type:'Shadowsocks'}}});assert.deepEqual(members(data,'Main',{},preferences({})),['剩余流量：10 GB','香港流量节点']);assert.deepEqual(members(data,'Main',{},preferences({hideInfoNodes:true})),['香港流量节点']);assert.equal(data.proxies.Main.all.length,2);});
