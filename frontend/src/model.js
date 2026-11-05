// Keep the entire document as the authority. A field changes only on user input.
export const clone=value=>JSON.parse(JSON.stringify(value));
export function documentModel(value={}) {
  const doc=clone(value);
  doc.version ??= 'PaoPaoGateWay 3.1';
  doc.exported_at ??= new Date().toISOString();
  for(const key of ['subs','node-groups','rules']) {
    if(key in doc&&!Array.isArray(doc[key]))throw Error(key+' 必须是数组');
    if(!(key in doc))doc[key]=key==='rules'&&Array.isArray(doc['rule-sets'])?clone(doc['rule-sets']):[];
    if(doc[key].some(row=>!row||typeof row!=='object'||Array.isArray(row)))throw Error(key+' 的每条记录必须是对象');
  }
  for(const key of ['global_monitor','shadowsocks_in','restart_cron'])if(doc[key]&&(typeof doc[key]!=='object'||Array.isArray(doc[key])))throw Error(key+' 必须是对象');
  return doc;
}
export function move(rows,index,offset) {const target=index+offset;if(target<0||target>=rows.length)return;rows.splice(target,0,...rows.splice(index,1));}
export function groupMode(row) {return row.smart?'smart':row.speedtest_url||row.interval?'speedtest':row.failover?'fallback':'manual';}
export function setGroupMode(row,mode) {
  delete row.smart;delete row.speedtest_url;delete row.interval;delete row.failover;
  if(mode==='smart')row.smart=true;
  if(mode==='speedtest'){row.speedtest_url='https://www.gstatic.com/generate_204';row.interval=600;}
  if(mode==='fallback'){row.failover=true;row.monitor_url ||= 'https://www.youtube.com/generate_204';row.expected_status ??='0';row.retries ??=3;}
}
export function ruleType(row) {return row.type==='rule-set'?'rule-set':row.type==='url'||row.url?'url':'manual';}
export function setRuleType(row,type) {delete row.type;delete row.url;if(type==='rule-set'||type==='url'){row.type=type;row.url='';}else row.fixrule ||= [];}
export function exported(doc) {return clone(doc);}
