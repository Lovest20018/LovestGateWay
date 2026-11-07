// Public, trimmed proxy snapshots contain no connection credentials.
const record=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const strings=value=>Array.isArray(value)?value.filter(item=>typeof item==='string'):[];
export const isGroup=value=>Array.isArray(value?.all);
export const automatic=value=>['URLTest','Fallback','LoadBalance'].includes(value?.type)||value?.smart===true;
export const lockable=value=>['URLTest','Fallback'].includes(value?.type)||value?.smart===true;
export function informationNode(name){
  const value=name.normalize('NFKC').trim().replace(/^[^\p{L}\p{N}]+/u,'').replace(/@(?:subdns|hosts)?(?:\d{1,3}|[0-9a-f]{4})[A-Z]*$/,'').trim();
  return /^(?:剩余流量|到期时间|套餐到期|流量重置|重置剩余|官网地址|备用网址|订阅更新|traffic\s+remaining(?:[:：\s]|$)|traffic\s+reset\s*[:：]|expires?[:：\s]|(?:expiry|expiration)\s+date[:：\s])/i.test(value)||
    /^\d+(?:\.\d+)?\s*(?:[KMGTPE]i?B|B)\s*[|/]\s*\d+(?:\.\d+)?\s*(?:[KMGTPE]i?B|B)$/i.test(value);
}
export const rejected=name=>['REJECT','REJECT-DROP'].includes(name);
export const segment=value=>{if(value==='.'||value==='..')throw Error('该名称不能用于代理 API 路径，请先调整配置名称。');return encodeURIComponent(value).replace(/[!'()*]/g,char=>'%'+char.charCodeAt(0).toString(16).toUpperCase());};
export const defaultTestUrl='http://cp.cloudflare.com/generate_204';
export const sorts=[['natural','默认顺序'],['latAsc','延迟 ↑'],['latDesc','延迟 ↓'],['scoreAsc','打分 优→劣'],['nameAsc','名称 A-Z'],['nameDesc','名称 Z-A']];

function legacyUsage(name){
  const match=/^(.+)_\1: ([\d.]+)G\/([\d.]+)G@@(.*)$/.exec(name);
  if(!match)return null;
  const total=Number(match[3])*1024**3,left=Number(match[2])*1024**3,date=Date.parse(match[4]);
  return {name:match[1],upload:0,download:Math.max(0,total-left),total,expire:Number.isFinite(date)?date/1000:0,legacy:true};
}
export function snapshot(value){
  const input=record(value),raw=record(input.proxies),meta=Object.keys(raw).filter(name=>name.includes('@@'));
  const keep=values=>[...new Set(strings(values).filter(name=>!name.includes('@@')))];
  const proxies=Object.fromEntries(Object.entries(raw).filter(([name])=>!name.includes('@@')).map(([name,node])=>[name,{...record(node),...(isGroup(node)?{all:keep(node.all)}:{})}]));
  const providers=Object.fromEntries(Object.entries(record(input.providers)).map(([name,provider])=>[name,{...record(provider),proxies:keep(provider?.proxies)}]));
  const subs=Array.isArray(input.subs)&&input.subs.length?input.subs.filter(item=>typeof item?.name==='string'):meta.map(legacyUsage).filter(Boolean);
  return {proxies,providers,subs,mode:typeof input.mode==='string'?input.mode.toLowerCase():'',testUrl:typeof input.testUrl==='string'?input.testUrl:''};
}
export function preferences(value){
  const raw=record(value),sort=sorts.some(([key])=>key===raw.sort)?raw.sort:'natural';
  return {...raw,sort,hideUnavail:raw.hideUnavail===true,hideInfoNodes:raw.hideInfoNodes===true,autoClose:raw.autoClose===true,optimize:raw.optimize!==false,showUdp:raw.showUdp===true,
    curve:typeof raw.curve==='number'&&Number.isFinite(raw.curve)?Math.max(0,Math.min(100,raw.curve)):100,
    hiddenNames:[...new Set(strings(raw.hiddenNames).filter(name=>name.trim()))],hiddenKeywords:[...new Set(strings(raw.hiddenKeywords).map(word=>word.normalize('NFKC').trim().toLowerCase()).filter(Boolean))],
    testUrl:typeof raw.testUrl==='string'?raw.testUrl:'',gsort:Object.assign(Object.create(null),record(raw.gsort)),gcollapse:Object.assign(Object.create(null),record(raw.gcollapse))};
}
export function groups(data,globalOnly=true){
  const names=Object.keys(data.proxies).filter(name=>isGroup(data.proxies[name])&&name!=='GLOBAL');
  const ordered=[...new Set([...(data.proxies.GLOBAL?.all||[]).filter(name=>names.includes(name)),...names])];
  if(isGroup(data.proxies.GLOBAL))ordered.push('GLOBAL');
  return globalOnly&&data.mode==='global'?ordered.filter(name=>name==='GLOBAL'):ordered;
}
export function delay(data,name,group){
  const node=data.proxies[name],parent=data.proxies[group],url=automatic(parent)?parent.testUrl:'';
  if(!node)return null;
  let value=url&&Object.hasOwn(record(node.extra),url)?node.extra[url]:node.delay??node.history?.at(-1)?.delay??Object.values(record(node.extra))[0];
  if(value==null&&parent?.smart)value=parent.scores?.[name]?.samples?.at(-1);
  return typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;
}
export function state(data,name,group){
  const latency=delay(data,name,group);
  return data.proxies[name]?.alive===false||latency===0?'offline':latency===null?'unknown':'online';
}
const aliases={HK:['香港','hong kong'],TW:['台湾','台灣','taiwan'],US:['美国','美國','united states','america'],JP:['日本','japan'],SG:['新加坡','singapore'],KR:['韩国','韓國','korea'],GB:['英国','英國','united kingdom','britain'],DE:['德国','德國','germany'],FR:['法国','法國','france'],CA:['加拿大','canada'],AU:['澳大利亚','澳洲','australia'],RU:['俄罗斯','俄羅斯','russia'],IN:['印度','india'],NL:['荷兰','荷蘭','netherlands'],TR:['土耳其','turkey'],VN:['越南','vietnam'],TH:['泰国','泰國','thailand'],MY:['马来西亚','馬來西亞','malaysia'],PH:['菲律宾','菲律賓','philippines'],ID:['印度尼西亚','印尼','indonesia'],BR:['巴西','brazil'],CN:['中国','中國','china'],MO:['澳门','澳門','macau'],CH:['瑞士','switzerland'],IT:['意大利','italy'],ES:['西班牙','spain'],SE:['瑞典','sweden'],FI:['芬兰','芬蘭','finland'],NO:['挪威','norway'],NZ:['新西兰','紐西蘭','new zealand'],ZA:['南非','south africa']};
export function nodeLabel(name,optimize=true){
  if(!optimize)return {display:name,flag:'',code:'',tag:'',dns:''};
  if(name==='DIRECT')return {display:'直连',flag:'🌐',code:'',tag:'',dns:''};
  if(rejected(name))return {display:'拒绝',flag:'🚫',code:'',tag:'',dns:''};
  let display=name,code='',flag='',dns='';
  const suffix=/@((?:subdns|hosts)?(?:\d{1,3}|[0-9a-f]{4})[A-Z]*)$/.exec(display);
  if(suffix){dns=suffix[1];display=display.slice(0,suffix.index).trim();}
  const emoji=/[\u{1F1E6}-\u{1F1FF}]{2}/u.exec(display);
  if(emoji){flag=emoji[0];code=Array.from(flag,char=>String.fromCharCode(char.codePointAt(0)-0x1F1E6+65)).join('');display=display.replace(flag,'').trim();}
  if(!code){const lower=display.toLowerCase();for(const [key,terms]of Object.entries(aliases))if(terms.some(term=>lower.includes(term))||new RegExp('(?:^|[^a-z])'+key+'(?:$|[^a-z])','i').test(display)){code=key;flag=String.fromCodePoint(...[...key].map(char=>0x1F1E6+char.charCodeAt(0)-65));break;}}
  const split=display.indexOf('_'),tag=split>0&&split<display.length-1?display.slice(0,split):'';
  if(tag)display=display.slice(split+1).trim();
  return {display:display||name,flag,code,tag,dns};
}
export function customHiddenNode(name,prefs){
  return prefs.hiddenNames.includes(name)||prefs.hiddenKeywords.some(word=>name.normalize('NFKC').toLowerCase().includes(word));
}
export function members(data,name,filter={},prefs=preferences({})){
  let values=[...(data.proxies[name]?.all||[])];
  if(data.mode==='global'&&name==='GLOBAL')values=values.filter(node=>!isGroup(data.proxies[node]));
  values=values.filter(node=>(isGroup(data.proxies[node])||!(prefs.hideInfoNodes&&informationNode(node))&&!customHiddenNode(node,prefs))&&(!prefs.hideUnavail||isGroup(data.proxies[node])||state(data,node,name)!=='offline')&&(!filter.status||state(data,node,name)===filter.status)&&(!filter.region||nodeLabel(node).code===filter.region)&&(!filter.query||node.toLocaleLowerCase().includes(filter.query.toLocaleLowerCase())));
  const order=filter.sort||prefs.gsort[name]||prefs.sort,latency=node=>delay(data,node,name)>0?delay(data,node,name):Infinity;
  if(order==='latAsc'||order==='latDesc')values.sort((a,b)=>{const x=latency(a),y=latency(b);return x===y?0:x===Infinity?1:y===Infinity?-1:order==='latAsc'?x-y:y-x;});
  else if(order==='scoreAsc')values.sort((a,b)=>(data.proxies[name]?.scores?.[a]?.score??Infinity)-(data.proxies[name]?.scores?.[b]?.score??Infinity));
  else if(order==='nameAsc'||order==='nameDesc')values.sort((a,b)=>order==='nameAsc'?a.localeCompare(b):b.localeCompare(a));
  return values;
}
export function testUrl(data,name,prefs,custom=''){
  const node=data.proxies[name],value=(automatic(node)?node.testUrl:'')||custom.trim()||prefs.testUrl.trim()||data.testUrl||defaultTestUrl;
  let parsed;try{parsed=new URL(value);}catch{throw Error('测速 URL 必须是有效的 HTTP 或 HTTPS 地址。');}
  if(!['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password)throw Error('测速 URL 必须是有效且不含凭据的 HTTP 或 HTTPS 地址。');
  return value;
}
export function providerFor(data,name){return Object.keys(data.providers).find(key=>key!=='default'&&data.providers[key].vehicleType!=='Compatible'&&data.providers[key].proxies.includes(name));}
export function testPath(data,name,url){const provider=providerFor(data,name);return (provider?'/providers/proxies/'+segment(provider)+'/'+segment(name)+'/healthcheck':'/proxies/'+segment(name)+'/delay')+'?url='+encodeURIComponent(url)+'&timeout=5000';}
export function testPlan(data,prefs){
  const plans=new Map(),add=(name,url)=>{if(!rejected(name))plans.set(JSON.stringify([name,url]),{name,url});};
  for(const group of groups(data,false)){const node=data.proxies[group],url=testUrl(data,group,prefs);for(const name of node.all)add(name,url);}
  const url=testUrl(data,'',prefs);for(const provider of Object.values(data.providers))for(const name of provider.proxies)add(name,url);
  return [...plans.values()];
}
export function usage(value){
  const valid=key=>typeof value?.[key]==='number'&&Number.isFinite(value[key])&&value[key]>=0?value[key]:null;
  const total=valid('total'),up=valid('upload'),down=valid('download'),expire=valid('expire'),used=up!==null&&down!==null?up+down:null;
  return {total,up,down,used,left:total>0&&used!==null?Math.max(0,total-used):null,expire:expire>0?expire:null};
}
