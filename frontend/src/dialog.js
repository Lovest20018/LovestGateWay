export function showDialog(dialog,trigger=document.activeElement) {
  if(dialog.open)return;
  dialog.addEventListener('close',()=>{
    if(trigger?.isConnected)trigger.focus({preventScroll:true});
  },{once:true});
  dialog.showModal();
}

export function confirmAction(message,{title='确认操作',accept='确认',trigger=document.activeElement}={}) {
  if(document.querySelector('dialog:modal'))return Promise.resolve(false);
  const dialog=document.createElement('dialog'),heading=document.createElement('h2'),description=document.createElement('p');
  dialog.className='application-dialog';
  heading.textContent=title;description.textContent=message;
  heading.id='action-dialog-title';
  description.id='action-dialog-description';
  dialog.setAttribute('aria-labelledby',heading.id);
  dialog.setAttribute('aria-describedby',description.id);
  const cancel=Object.assign(document.createElement('button'),{type:'button',className:'btn',textContent:'取消',autofocus:true});
  const confirm=Object.assign(document.createElement('button'),{type:'button',className:'btn btn-brand',textContent:accept});
  cancel.onclick=()=>dialog.close();confirm.onclick=()=>dialog.close('confirmed');
  const actions=document.createElement('div');actions.className='dialog-actions button-row';
  actions.append(cancel,confirm);
  dialog.append(heading,description,actions);
  document.body.append(dialog);
  return new Promise(resolve=>{
    showDialog(dialog,trigger);
    dialog.addEventListener('close',()=>{
      dialog.remove();
      resolve(dialog.returnValue==='confirmed');
    },{once:true});
  });
}
