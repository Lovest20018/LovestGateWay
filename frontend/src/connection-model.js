import {record,text,number,strings,sortRows} from './monitor-model.js';
const port=value=>typeof value==='string'||typeof value==='number'?String(value):'';
const endpoint=(host,port)=>port?(host.includes(':')?'['+host+']':host)+':'+port:host;
export function connectionRow(value) {
  const raw=record(value), metadata=record(raw.metadata);
  return {id:text(raw.id),download:number(raw.download),upload:number(raw.upload),start:text(raw.start),chains:strings(raw.chains),rule:text(raw.rule),rulePayload:text(raw.rulePayload),metadata:{host:text(metadata.host),sourceIP:text(metadata.sourceIP),sourcePort:port(metadata.sourcePort),destinationIP:text(metadata.destinationIP),destinationPort:port(metadata.destinationPort),type:text(metadata.type),network:text(metadata.network),processPath:text(metadata.processPath),sniffHost:text(metadata.sniffHost)}};
}
export const connectionValues={
  host:row=>endpoint(row.metadata.host||row.metadata.destinationIP||'?',row.metadata.destinationPort),
  dl:row=>row.download,ul:row=>row.upload,dlSpeed:row=>row.dlSpeed,ulSpeed:row=>row.ulSpeed,
  chains:row=>[...row.chains].reverse().join(' / '),rule:row=>row.rule+(row.rulePayload?'('+row.rulePayload+')':''),
  active:row=>row.closedAt??row.lastActive,source:row=>endpoint(row.metadata.sourceIP||'?',row.metadata.sourcePort),inbound:row=>(row.metadata.type||'?')+'('+(row.metadata.network||'?')+')'
};
export function connectionTracker(limit=100) {
  const previous=new Map();let closed=[];
  return {
    resetRates(){for(const value of previous.values()){value.at=null;value.row.dlSpeed=null;value.row.ulSpeed=null;}},
    update(value,at=Date.now()) {
      if(!value||typeof value!=='object'||Array.isArray(value)||(value.connections!==null&&!Array.isArray(value.connections)))throw Error('连接状态响应无效，请稍后重试。');
      const normalized=(value.connections||[]).map(connectionRow),ids=new Set();
      for(const row of normalized){if(!row.id||ids.has(row.id))throw Error('连接标识不完整或重复，已保留上次状态。');ids.add(row.id);}
      const active=normalized.map(row=>{
        const old=previous.get(row.id),seconds=old?.at!=null&&at>old.at?(at-old.at)/1000:null;
        const rates=seconds&&row.download>=old.row.download&&row.upload>=old.row.upload?{dlSpeed:(row.download-old.row.download)/seconds,ulSpeed:(row.upload-old.row.upload)/seconds}:{dlSpeed:null,ulSpeed:null};
        const lastActive=!old||row.download!==old.row.download||row.upload!==old.row.upload?at:old.row.lastActive;
        const result={...row,...rates,lastActive};previous.set(row.id,{row:result,at});return result;
      });
      for(const [id,old]of previous)if(!ids.has(id)){closed.unshift({...old.row,dlSpeed:0,ulSpeed:0,closedAt:at});previous.delete(id);}
      closed=closed.slice(0,limit);
      return {active,closed:[...closed],downloadTotal:number(value.downloadTotal),uploadTotal:number(value.uploadTotal)};
    }
  };
}
export function filterConnections(rows,{query='',filters={},sort='active',direction=-1}={}) {
  const term=query.trim().toLocaleLowerCase();
  return sortRows(rows.filter(row=>{
    for(const key of ['host','chains','rule','source','inbound'])if(filters[key]&&!connectionValues[key](row).toLocaleLowerCase().includes(filters[key].toLocaleLowerCase()))return false;
    return !term||[...['host','chains','rule','source','inbound'].map(key=>connectionValues[key](row)),row.metadata.destinationIP,row.metadata.processPath].join(' ').toLocaleLowerCase().includes(term);
  }),connectionValues[sort]||connectionValues.active,direction);
}
