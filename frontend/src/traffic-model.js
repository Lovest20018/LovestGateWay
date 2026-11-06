import {record,text,number,strings,sortRows} from './monitor-model.js';
const genericSniff=new Set(['tls','http','quic','bt','FakeIP','RealIP','SOCKS5','httpProxy','skip']);
const compoundSuffixes=new Set(['com.cn','net.cn','org.cn','gov.cn','edu.cn','ac.cn','com.hk','org.hk','com.tw','org.tw','co.jp','ne.jp','or.jp','ac.jp','co.kr','or.kr','co.uk','org.uk','ac.uk','gov.uk','me.uk','com.au','net.au','org.au','co.nz','net.nz','org.nz','com.br','com.mx','com.ar','com.tr','com.sg','com.my','co.in','co.th','co.id','co.za','com.vn','com.ph']);
export function trafficSnapshot(value) {
  if(!Array.isArray(value))throw Error('流量记录响应无效，请重新连接。');
  return value.filter(item=>item&&typeof item==='object'&&!Array.isArray(item)).map((item,index)=>{
    const row=record(item);return {key:JSON.stringify([text(row.client),text(row.domain),text(row.transport),index]),client:text(row.client),domain:text(row.domain),transport:text(row.transport),sniff:strings(row.sniff),inbound:strings(row.inbound),dport:Array.isArray(row.dport)?row.dport.filter(value=>(typeof value==='number'&&Number.isInteger(value)&&value>=0&&value<=65535)||typeof value==='string'):[],head:text(row.head),down:number(row.down),up:number(row.up),conns:number(row.conns),ts:number(row.ts),last_seen:number(row.last_seen)};
  });
}
export const transport=row=>[row.transport.toUpperCase(),...row.dport].filter(Boolean).join('/');
export const sniff=row=>row.sniff.join(' / ');
export function filterTraffic(rows,{query='',filters={},range=Infinity,now=Date.now()}={}) {
  const term=query.trim().toLocaleLowerCase(),since=range===Infinity?0:now-range;
  return rows.filter(row=>row.last_seen>=since&&['client','domain','transport','sniff','inbound'].every(key=>!filters[key]||String(key==='transport'?transport(row):key==='sniff'?sniff(row):key==='inbound'?row.inbound.join(' / '):row[key]).toLocaleLowerCase().includes(filters[key].toLocaleLowerCase()))&&(!term||[row.domain,row.client,transport(row),sniff(row),row.inbound.join(' ')].join(' ').toLocaleLowerCase().includes(term)));
}
export function aggregateTraffic(rows,view='domain') {
  if(view==='full')return rows;
  const groups=new Map();
  for(const row of rows){const label=view==='client'?row.client:row.domain;let item=groups.get(label);if(!item){item={key:label,[view]:label,down:0,up:0,total:0,conns:0,last_seen:0,clients:new Set(),domains:new Map()};groups.set(label,item);}item.down+=row.down;item.up+=row.up;item.total+=row.down+row.up;item.conns+=row.conns;item.last_seen=Math.max(item.last_seen,row.last_seen);item.clients.add(row.client);item.domains.set(row.domain,(item.domains.get(row.domain)||0)+row.down+row.up);}
  return [...groups.values()].map(row=>({...row,clients:[...row.clients],domains:[...row.domains].sort((a,b)=>b[1]-a[1])}));
}
export const trafficValues={ts:row=>row.ts,last_seen:row=>row.last_seen,client:row=>row.client,domain:row=>row.domain,transport,sniff,inbound:row=>row.inbound.join(' / '),conns:row=>row.conns,down:row=>row.down,up:row=>row.up,total:row=>row.down+row.up,clients:row=>row.clients.length};
export const sortTraffic=(rows,key,direction)=>sortRows(rows,trafficValues[key]||trafficValues.total,direction);
export function apex(domain) {
  const value=domain.toLowerCase().replace(/\.$/,'');if(/^(\d{1,3}\.){3}\d{1,3}$/.test(value)||value.includes(':'))return value;
  const parts=value.split('.');return parts.length<=2?value:compoundSuffixes.has(parts.slice(-2).join('.'))?parts.slice(-3).join('.'):parts.slice(-2).join('.');
}
export function distributions(rows,kind='client') {
  const values=new Map();
  for(const row of rows){const label=kind==='client'?row.client:row.sniff.find(value=>value&&!genericSniff.has(value))||apex(row.domain);let value=values.get(label);if(!value){value={label,down:0,up:0,total:0,conns:0,duration:0};values.set(label,value);}value.down+=row.down;value.up+=row.up;value.total+=row.down+row.up;value.conns+=row.conns;value.duration=Math.max(value.duration,Math.max(0,row.last_seen-row.ts));}
  return [...values.values()];
}
export function nodeSnapshot(value) {
  if(!Array.isArray(value?.nodes))throw Error('节点分布响应无效，请稍后重试。');
  return {since:number(value.since),nodes:value.nodes.filter(item=>typeof item?.node==='string').map(item=>({node:item.node,up:number(item.up),down:number(item.down),total:number(item.up)+number(item.down)}))};
}
