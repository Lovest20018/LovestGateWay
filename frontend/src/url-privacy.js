// Subscription credentials can occur in paths, queries or userinfo.
export function maskUrl(value){
  if(!value)return '未填写地址';
  try{const url=new URL(value);if(!['http:','https:'].includes(url.protocol))return '地址已隐藏';return url.origin+(url.pathname==='/'?'/':'/•••')+(url.search?'?'+[...new Set(url.searchParams.keys())].map(()=> '•••=•••').join('&'):'')+(url.hash?'#•••':'');}
  catch{return '地址已隐藏';}
}
const button=(text,click)=>{const node=document.createElement('button');node.type='button';node.className='btn';node.textContent=text;node.onclick=event=>{event.preventDefault();event.stopPropagation();click();};return node;};
export function guardUrlInput(input,read){
  if(!read())return;
  const tools=document.createElement('span');tools.className='url-privacy-tools';let visible=false;
  const reveal=button('显示地址',()=>{visible=!visible;input.readOnly=true;input.value=visible?read():'';input.placeholder=maskUrl(read());reveal.textContent=visible?'隐藏地址':'显示地址';reveal.setAttribute('aria-pressed',String(visible));edit.hidden=false;});
  reveal.setAttribute('aria-pressed','false');
  const edit=button('修改地址',()=>{visible=true;input.value=read();input.readOnly=false;reveal.textContent='隐藏地址';reveal.setAttribute('aria-pressed','true');edit.hidden=true;input.focus();});
  input.value='';input.readOnly=true;input.placeholder=maskUrl(read());input.autocomplete='off';input.spellcheck=false;tools.append(reveal,edit);input.after(tools);
}
export function urlSummary(value,copy){
  const root=document.createElement('span'),code=document.createElement('code'),status=document.createElement('span');root.className='url-privacy-summary';code.className='source-location';code.textContent=maskUrl(value);status.setAttribute('role','status');let visible=false;
  const reveal=button('显示地址',()=>{visible=!visible;code.textContent=visible?value:maskUrl(value);reveal.textContent=visible?'隐藏地址':'显示地址';reveal.setAttribute('aria-pressed',String(visible));});reveal.setAttribute('aria-pressed','false');
  const copying=button('复制完整地址',async()=>{try{await copy(value);status.textContent='完整地址已复制。';}catch{status.textContent='复制失败，请显示地址后手动复制。';}});root.append(code,reveal,copying,status);return root;
}
