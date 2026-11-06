import {text,number} from './monitor-model.js';
export const levels={trace:0,debug:1,info:2,warn:3,warning:3,error:4,fatal:4};
export const sourceName=value=>value==='clash'?'ClashCore':value==='sniffbox'?'LovestGateWay':value;
export const stripAnsi=value=>value.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g,'').replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g,'').replace(/\r/g,'');
const palette=['#6b7689','#ff6b6e','#3ed69a','#f5c94c','#5aa7ff','#c792ea','#4dd0e1','#d5dbe8','#8a93a6','#ff8f92','#63e6b0','#ffd97a','#82bfff','#dcaef7','#7ee3f0','#ffffff'];
const color=index=>index<16?palette[index]:index<232?'rgb('+[Math.floor((index-16)/36),Math.floor((index-16)/6)%6,(index-16)%6].map(value=>value?value*40+55:0).join(',')+')':'rgb('+Array(3).fill((index-232)*10+8).join(',')+')';
export function ansiSegments(raw) {
  const result=[],pattern=/\x1b\[([0-9;]*)m|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]/g;let start=0,style={};
  for(const match of raw.matchAll(pattern)){
    if(match.index>start)result.push({text:raw.slice(start,match.index),...style});start=match.index+match[0].length;
    if(match[1]===undefined)continue;const codes=match[1]?match[1].split(';').map(Number):[0];
    for(let index=0;index<codes.length;index++){const code=codes[index];if(code===0)style={};else if(code===1)style.bold=true;else if(code===2)style.dim=true;else if(code===3)style.italic=true;else if(code===4)style.underline=true;else if(code===22){style.bold=false;style.dim=false;}else if(code===23)style.italic=false;else if(code===24)style.underline=false;else if(code>=30&&code<=37)style.fg=color(code-30);else if(code>=90&&code<=97)style.fg=color(code-90+8);else if(code>=40&&code<=47)style.bg=color(code-40);else if(code>=100&&code<=107)style.bg=color(code-100+8);else if(code===39)delete style.fg;else if(code===49)delete style.bg;else if(code===38||code===48){const key=code===38?'fg':'bg';if(codes[index+1]===5){style[key]=color(Math.max(0,Math.min(255,codes[index+2]||0)));index+=2;}else if(codes[index+1]===2){style[key]='rgb('+codes.slice(index+2,index+5).map(value=>Math.max(0,Math.min(255,value))).join(',')+')';index+=4;}}}
  }
  if(start<raw.length)result.push({text:raw.slice(start),...style});return result;
}
export function logEvent(value,at=Date.now()) {
  let row;try{row=typeof value==='string'?JSON.parse(value):value;}catch{return null;}
  if(!row||typeof row!=='object'||typeof row.msg!=='string')return null;
  const level=text(row.level).toLowerCase();return {ts:number(row.ts)||at,source:text(row.source)||'?',level:Object.hasOwn(levels,level)?level:'info',raw:row.msg,text:stripAnsi(row.msg)};
}
export function logBuffer(limit=2000) {
  let rows=[],pending=[],paused=false,sequence=0;
  return {
    get rows(){return rows;},get paused(){return paused;},get pending(){return pending.length;},
    append(values){const entries=values.map(row=>({...row,id:++sequence}));if(paused)pending=[...pending,...entries].slice(-limit);else rows=[...rows,...entries].slice(-limit);},
    pause(value){paused=value;if(!paused&&pending.length){rows=[...rows,...pending].slice(-limit);pending=[];}},
    clear(){rows=[];pending=[];},
    filter({query='',level='trace'}={}){const term=query.trim().toLowerCase(),minimum=levels[level]??2;return rows.filter(row=>(levels[row.level]??2)>=minimum&&(!term||[row.text,sourceName(row.source||'')].join(' ').toLowerCase().includes(term)));}
  };
}
