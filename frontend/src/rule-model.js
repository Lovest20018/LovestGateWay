const record=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const text=value=>typeof value==='string'?value:'';
export const ruleType=value=>String(value??'').toUpperCase().replace(/[-_\s]/g,'');
export function rulesSnapshot(value){
  if(!Array.isArray(value?.rules))throw Error('规则状态暂不可用，请稍后重试。');
  const rows=value.rules.flatMap((row,position)=>{
    if(!row||typeof row!=='object'||Array.isArray(row))return [];
    const index=row.index??position,extra=record(row.extra);
    return [{...row,type:text(row.type),payload:text(row.payload),proxy:text(row.proxy),index,position,
      canToggle:Number.isSafeInteger(index)&&index>=0&&row.extra!=null&&typeof row.extra==='object'&&!Array.isArray(row.extra),extra:{...extra}}];
  });
  const counts=new Map();for(const row of rows)counts.set(row.index,(counts.get(row.index)||0)+1);
  return rows.map(row=>({...row,canToggle:row.canToggle&&counts.get(row.index)===1}));
}
export function providersSnapshot(value){
  if(!value?.providers||typeof value.providers!=='object'||Array.isArray(value.providers))throw Error('规则订阅状态暂不可用。');
  return Object.entries(value.providers).map(([key,item])=>({...record(item),name:typeof item?.name==='string'?item.name:key}));
}
export function rulePage(rows,{query='',located=null,page=1,pageSize='auto'}={}){
  const term=query.trim().toLocaleLowerCase(),filtered=located!==null?rows.filter(row=>row.index===located):rows.filter(row=>!term||[row.type,row.payload,row.proxy].some(value=>value.toLocaleLowerCase().includes(term)));
  const limit=pageSize==='all'?Infinity:pageSize==='auto'?(rows.length<=150?Infinity:100):['20','50','100'].includes(pageSize)?Number(pageSize):100;
  const pages=Math.max(1,Math.ceil(filtered.length/limit)),current=Math.min(Math.max(Number.isSafeInteger(page)?page:1,1),pages);
  return {rows:limit===Infinity?filtered:filtered.slice((current-1)*limit,current*limit),total:filtered.length,page:current,pages,limit};
}
export function proxyChain(proxies,start){
  const chain=[],seen=new Set([start]);let current=start;
  while(chain.length<64){const node=Object.hasOwn(proxies,current)?proxies[current]:null;if(!Array.isArray(node?.all)||typeof node.now!=='string'||!node.now||seen.has(node.now))break;current=node.now;chain.push(current);seen.add(current);}
  return chain;
}
export function matchLog(logs,rows){
  for(const item of Array.isArray(logs)?logs:[]){
    if(typeof item?.payload!=='string')continue;
    const line=item.payload;
    const matched=/-->\s+\S+\s+match\s+(.+?)\s+using\s+(.+?)\s*$/.exec(line);
    const fallback=/-->\s+\S+\s+doesn't match any rule\s+using\s+(.+?)\s*$/.exec(line);
    const using=/-->\s+\S+\s+using\s+(.+?)\s*$/.exec(line);
    if(!matched&&!fallback&&!using)continue;
    if(!matched)return {type:'',payload:'',chain:fallback?.[1]||using?.[1],none:!!fallback,row:null};
    const expression=/^([A-Za-z0-9_-]+)\((.*)\)$/s.exec(matched[1]),type=expression?.[1]||matched[1],payload=expression?.[2]||'';
    const candidates=rows.filter(row=>ruleType(row.type)===ruleType(type)&&row.payload===payload),targeted=candidates.filter(row=>row.proxy===matched[2]);
    const row=targeted.length===1?targeted[0]:candidates.length===1?candidates[0]:null;
    return {type,payload,chain:matched[2],none:false,row};
  }
  return null;
}
export function testAddress(raw){
  const value=raw.trim();if(!value)throw Error('请输入要测试的网址。');
  const address=/^[a-z][a-z\d+.-]*:\/\//i.test(value)?value:'http://'+value;
  let url;try{url=new URL(address);}catch{throw Error('请输入有效的 HTTP 或 HTTPS 网址。');}
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||new TextEncoder().encode(address).length>2048)throw Error('请输入不含凭据、长度不超过 2048 字节的 HTTP 或 HTTPS 网址。');
  return address;
}
export function geoResult(value){
  const files=(Array.isArray(value?.files)?value.files:[]).map(file=>({name:text(file?.name)||'GEO 文件',status:['downloaded','uptodate','skipped','failed','fetch_failed','download_failed'].includes(file?.status)?file.status:'unknown',version:text(file?.version)}));
  const failed=files.filter(file=>!['downloaded','uptodate','skipped'].includes(file.status));
  if(!files.length)return {files,complete:false,message:'更新响应未提供文件结果，请刷新核对 GEO 状态。'};
  return {files,complete:!failed.length,message:files.map(file=>file.name+'：'+({downloaded:'已下载',uptodate:'已是最新',skipped:'已跳过',failed:'更新失败',fetch_failed:'获取版本失败',download_failed:'下载失败',unknown:'状态未确认'})[file.status]).join('；')};
}
