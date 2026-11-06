export function modeControl(onChange){
  const root=document.createElement('div');root.className='mode-control';root.setAttribute('role','radiogroup');root.setAttribute('aria-label','核心运行模式');
  root.title='临时切换运行模式；配置重新加载后恢复配置中的模式。';
  const items=[];
  for(const [value,label]of [['rule','规则'],['global','全局'],['direct','直连']]){
    const item=document.createElement('button');item.type='button';item.className='btn';item.dataset.action='mode';item.dataset.mode=value;
    item.setAttribute('role','radio');item.setAttribute('aria-checked','false');item.textContent=label;
    item.onclick=()=>{if(item.getAttribute('aria-checked')!=='true')onChange(value);};items.push(item);root.append(item);
  }
  root.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
    const enabled=items.filter(item=>!item.disabled);if(!enabled.length)return;
    event.preventDefault();const at=enabled.indexOf(document.activeElement);
    const next=enabled[event.key==='Home'?0:event.key==='End'?enabled.length-1:(at+(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1)+enabled.length)%enabled.length];next.focus();next.click();
  });
  return {root,update(mode,disabled=false){items.forEach(item=>{const selected=item.dataset.mode===mode;item.disabled=disabled;item.setAttribute('aria-checked',String(selected));item.tabIndex=selected||!items.some(candidate=>candidate.dataset.mode===mode)&&item===items[0]?0:-1;});}};
}
