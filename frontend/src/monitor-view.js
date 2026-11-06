import {pageRows} from './monitor-model.js';
export function el(tag,text,className) { const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node; }
export function button(text,callback,className='btn') {const node=el('button',text,className);node.type='button';node.addEventListener('click',callback);return node;}
export function input(label,{type='search',placeholder=label,onInput=()=>{}}={}) {const node=el('input');node.type=type;node.placeholder=placeholder;node.setAttribute('aria-label',label);node.addEventListener('input',()=>onInput(node.value));return node;}
export function select(label,options,onChange,value) {const node=el('select');node.setAttribute('aria-label',label);for(const [key,name]of options){const option=el('option',name);option.value=key;node.append(option);}if(value!==undefined)node.value=value;node.addEventListener('change',()=>onChange(node.value));return node;}
export function field(label,control) {const node=el('label',undefined,'monitor-field');node.append(el('span',label),control);return node;}
export function page(host,id,title) {const root=el('section',undefined,'monitor-page');root.id=id;const head=el('header',undefined,'monitor-head'),heading=el('h1',title),status=el('p',undefined,'monitor-status');status.hidden=true;status.setAttribute('role','status');head.append(heading);root.append(head,status);host.replaceChildren(root);return {root,head,status,message(text){status.textContent=text;status.hidden=!text;}};}
export function download(text,name,type='text/plain;charset=utf-8') {const url=URL.createObjectURL(new Blob([text],{type})),link=el('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
export async function copy(text) {try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);return true;}}catch{}const node=el('textarea');node.value=text;node.style.cssText='position:fixed;left:-9999px';document.body.append(node);node.select();const ok=document.execCommand('copy');node.remove();return ok;}
export function confirmation(root) {
  const dialog=el('dialog',undefined,'monitor-dialog'),title=el('h2'),description=el('p'),cancel=button('取消',()=>dialog.close());let work=null;
  const confirm=button('确认',()=>{const current=work;work=null;dialog.close();current?.();});dialog.setAttribute('aria-label','操作确认');dialog.append(title,description,cancel,confirm);dialog.addEventListener('close',()=>{work=null;});root.append(dialog);
  return {show(heading,text,callback){title.textContent=heading;description.textContent=text;work=callback;if(!dialog.open)dialog.showModal();},dispose(){if(dialog.open)dialog.close();}};
}
export function pager(onChange,{size=100,sizes=[10,20,50,100]}={}) {
  const root=el('div',undefined,'monitor-pager'),previous=button('上一页',()=>onChange(current-1,Number(choose.value))),next=button('下一页',()=>onChange(current+1,Number(choose.value))),count=el('span');let current=1;
  const choose=select('每页条数',sizes.map(value=>[String(value),String(value)]),value=>onChange(1,Number(value)),String(size));root.append(field('每页',choose),previous,count,next);
  return {root,update(rows,page,size){const value=pageRows(rows,page,size);current=value.page;previous.disabled=current<=1;next.disabled=current>=value.pages;count.textContent=current+' / '+value.pages+' · '+value.total+' 条';choose.value=String(size);return value;}};
}
export function table({columns,onSort=()=>{},onFilter,filters={},key=row=>row.id,empty='暂无数据。',expansion}={}) {
  const wrap=el('div',undefined,'monitor-table-wrap'),root=el('table'),thead=el('thead'),tbody=el('tbody');wrap.tabIndex=0;wrap.setAttribute('aria-label','数据列表，可横向滚动');root.append(thead,tbody);wrap.append(root);
  let definitions=columns,views=new Map();
  function headers() {
    const row=el('tr'),filterRow=el('tr',undefined,'monitor-filter-row');
    for(const column of definitions){const th=el('th');th.scope='col';th.dataset.column=column.key;if(column.sortable!==false)th.append(button(column.label,()=>onSort(column.key),'monitor-sort'));else th.textContent=column.label;row.append(th);if(onFilter){const cell=el('th');if(column.filter){const control=input('筛选 '+column.label,{placeholder:'筛选…',onInput:value=>onFilter(column.key,value)});control.value=filters[column.key]||'';cell.append(control);}filterRow.append(cell);}}
    thead.replaceChildren(row);if(onFilter)thead.append(filterRow);
  }
  headers();
  return {wrap,tbody,
    columns(next){definitions=next;views.clear();tbody.replaceChildren();headers();},
    render(rows,{sort,direction=-1}={}){
      for(const th of thead.querySelectorAll('[data-column]'))th.setAttribute('aria-sort',th.dataset.column===sort?(direction===1?'ascending':'descending'):'none');
      const keep=new Set(),nodes=[];
      for(const [position,row]of rows.entries()){
        const identity=String(key(row));keep.add(identity);let view=views.get(identity);
        if(!view){const tr=el('tr');tr.dataset.key=identity;view={tr,row,cells:[]};for(const column of definitions){const cell=el('td');if(column.numeric)cell.className='num';if(column.action){const control=button('',()=>column.action(view.row),'monitor-cell-button');cell.append(control);view.cells.push({cell,control});}else view.cells.push({cell,control:cell});tr.append(cell);}views.set(identity,view);}
        view.row=row;
        definitions.forEach((column,index)=>{const {cell,control}=view.cells[index],value=String(column.value(row)??'—');if(control.textContent!==value)control.textContent=value;const title=column.title?.(row)||value;if(control.title!==title)control.title=title;if(column.action){control.disabled=!!column.disabled?.(row);control.setAttribute('aria-label',column.accessible?.(row)||value);}cell.dataset.column=column.key;});
        nodes.push(view.tr);const detail=expansion?.(row);if(detail){const tr=el('tr',undefined,'monitor-expanded'),cell=el('td');cell.colSpan=definitions.length;cell.append(detail);tr.append(cell);nodes.push(tr);}
      }
      if(!nodes.length){const tr=el('tr',undefined,'monitor-empty'),cell=el('td',empty);cell.colSpan=definitions.length;tr.append(cell);nodes.push(tr);}
      // Moving existing rows retains focused controls and their event handlers.
      for(let index=0;index<nodes.length;index++)if(tbody.children[index]!==nodes[index])tbody.insertBefore(nodes[index],tbody.children[index]||null);
      for(const node of [...tbody.children])if(!nodes.includes(node))node.remove();
      for(const [identity,view]of views)if(!keep.has(identity)){view.tr.remove();views.delete(identity);}
    }
  };
}
export function metrics(root,items) {root.replaceChildren(...items.map(([name,value])=>{const card=el('div',undefined,'monitor-metric');card.append(el('span',name),el('strong',value));return card;}));}
export const streamState=state=>({connecting:'正在连接…',reconnecting:'已断开，正在重连',open:'实时连接已建立',paused:'实时连接已暂停',unauthorized:'登录已失效',closed:''})[state]||'';
export function sparkline(values) {if(values.filter(Number.isFinite).length<2)return el('p','等待速率数据…','monitor-chart-empty');const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 300 60');svg.setAttribute('role','img');svg.setAttribute('aria-label','最近采样速率');const max=Math.max(1,...values.filter(Number.isFinite)),points=values.map((value,index)=>Number.isFinite(value)?(index/Math.max(1,values.length-1)*300)+','+(58-value/max*54):null).filter(Boolean),line=document.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('points',points.join(' '));line.setAttribute('fill','none');line.setAttribute('stroke','currentColor');line.setAttribute('stroke-width','2');svg.append(line);return svg;}
