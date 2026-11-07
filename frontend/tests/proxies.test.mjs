import test from 'node:test';
import assert from 'node:assert/strict';
import {snapshot,preferences,groups,members,delay,state,nodeLabel,testPlan,testPath,testUrl,segment,usage,informationNode} from '../src/proxy-model.js';

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

test('information node filtering is explicit and never changes the snapshot',()=>{const data=snapshot({proxies:{Main:{type:'Selector',all:['剩余流量：10 GB','香港流量节点']},'剩余流量：10 GB':{type:'Shadowsocks'},'香港流量节点':{type:'Shadowsocks'}}});assert.deepEqual(members(data,'Main',{},preferences({})),['剩余流量：10 GB','香港流量节点']);assert.deepEqual(members(data,'Main',{},preferences({hideInfoNodes:true})),['香港流量节点']);assert.equal(data.proxies.Main.all.length,2);assert.deepEqual(testPlan(data,preferences({hideInfoNodes:true})).map(row=>row.name),['剩余流量：10 GB','香港流量节点']);});

test('reset and quota notices are recognized with decorations and generated DNS suffixes',()=>{
  for(const name of ['Traffic Reset: 18 Days Left','Traffic Reset: 18 Days Left@196','traffic reset：18 days left','6.53 GB | 200 GB','6.53 GB | 200 GB@196','6.53GB/200GB','1.2 GiB | 2 TiB','🔁 Traffic Reset: 18 Days Left','📊 6.53 GB | 200 GB@subdns1','６.５３ ＧＢ ｜ ２００ ＧＢ','Expire Date: 2027/05/25','Expire Date: 2027/05/25@196','Expiry Date: 2027-05-25','剩余流量：10 GB'])
    assert.equal(informationNode(name),true,name);
  for(const name of ['Hong Kong 02','香港流量节点','GB London 01','200 GB London','6.53 GB | 200 GB Hong Kong','10 Gbps Hong Kong','Traffic Reset Hong Kong','Traffic Remaining-HK','DIRECT','REJECT'])
    assert.equal(informationNode(name),false,name);
});

test('hidden notices remain in original selections and all test plans while similarly named groups stay visible',()=>{
  const notices=['Traffic Reset: 18 Days Left','6.53 GB | 200 GB','Expire Date: 2027/05/25@196'];
  const names=['DIRECT','Hong Kong 02',...notices,'剩余流量组'];
  const data=snapshot({mode:'rule',proxies:{GLOBAL:{type:'Selector',all:names,now:notices[0]},'剩余流量组':{type:'Selector',all:['Hong Kong 02']},...Object.fromEntries(names.slice(0,-1).map(name=>[name,{type:'AnyTLS'}]))}});
  const original=structuredClone(data),show=preferences({}),hide=preferences({hideInfoNodes:true});
  assert.deepEqual(members(data,'GLOBAL',{},show),names);
  assert.deepEqual(members(data,'GLOBAL',{},hide),['DIRECT','Hong Kong 02','剩余流量组']);
  assert.deepEqual(testPlan(data,hide),testPlan(data,show));
  assert.deepEqual(data,original);
});

test('custom hiding supports exact names and literal case-insensitive keywords without changing groups or test plans',()=>{
  const names=['Hong Kong 01','Hong Kong 010','Traffic Hint: 18 days','<img src=x onerror=alert(1)>','a.*b','Hong Kong Group'];
  const data=snapshot({proxies:{Main:{type:'Selector',all:names,now:names[0]},Second:{type:'Selector',all:names.slice(0,3)},...Object.fromEntries(names.slice(0,-1).map(name=>[name,{type:'AnyTLS'}])),'Hong Kong Group':{type:'Selector',all:[names[1]]}}});
  const original=structuredClone(data),hide=preferences({hiddenNames:[names[0],names[3],'Hong Kong Group'],hiddenKeywords:['TRAFFIC HINT','a.*b','Hong Kong Group']});
  assert.deepEqual(members(data,'Main',{},hide),['Hong Kong 010','Hong Kong Group']);assert.deepEqual(members(data,'Second',{},hide),['Hong Kong 010']);
  assert.deepEqual(members(data,'Main',{},preferences({hiddenKeywords:['a.b']})),names,'keywords are plain text, never regular expressions');
  assert.deepEqual(testPlan(data,hide),testPlan(data,preferences({})));assert.deepEqual(data,original);
  assert.deepEqual(members(data,'Main',{},preferences({})),names,'removing custom rules restores every candidate');
});

test('custom hiding sanitizes persisted preferences and ignores blank and malformed rules',()=>{
  const prefs=preferences({unrelated:'preserved',hiddenNames:['A','A','',null,1,'  A  '],hiddenKeywords:['ＴＲＡＦＦＩＣ',' traffic ','',null,4,'  '],hideInfoNodes:false});
  assert.deepEqual(prefs.hiddenNames,['A','  A  ']);assert.deepEqual(prefs.hiddenKeywords,['traffic']);assert.equal(prefs.unrelated,'preserved');
  assert.deepEqual(preferences({hiddenNames:{A:true},hiddenKeywords:'a'}).hiddenNames,[]);assert.deepEqual(preferences({hiddenKeywords:'a'}).hiddenKeywords,[]);
  const data=snapshot({proxies:{Main:{all:['Traffic Hint','ＨＫ One','HK Two']}}});
  assert.deepEqual(members(data,'Main',{},preferences({hiddenKeywords:['ｈｋ']})),['Traffic Hint']);
});
