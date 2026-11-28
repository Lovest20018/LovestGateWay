import {clone,documentModel,exported,move,groupMode,setGroupMode,setGroupFailover,ruleType,setRuleType,ruleSetDefaults} from './model.js';
import {guardUrlInput} from './url-privacy.js';
import {confirmAction} from './dialog.js';

export function mountEditor(host,{navigate}) {
  let doc=documentModel(),destroy,serial=0,editRevision=0,importSequence=0,stopped=false;const expanded=new WeakMap();
  const defaultCipher='aes-128-gcm';
  const el=(tag,attrs={},children=[])=>{const node=document.createElement(tag);for(const [key,value]of Object.entries(attrs)){if(key==='text')node.textContent=value;else if(key.startsWith('on'))node.addEventListener(key.slice(2),value);else if(key==='class')node.className=value;else node.setAttribute(key,value);}node.append(...children);return node;};
  // Prevent summary activation, but keep bubbling so the persistence owner sees document edits.
  const button=(text,onclick)=>el('button',{type:'button',class:'btn',text,onclick:event=>{if(event.currentTarget.closest('summary'))event.preventDefault();onclick(event);}});
  const root=el('section',{class:'source-editor layout-page','aria-label':'订阅与策略编辑器'}),header=el('header',{class:'phead layout-toolbar layout-toolbar--panel'}),body=el('div',{class:'editor-body'});
  const menu=(text,action)=>el('span',{class:'menuwrap'},[button(text,action)]);
  const download=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)],{type:'application/json'}));const a=el('a',{href:url,download:'lovestsub.json'});a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const upload=el('input',{type:'file',accept:'.json,application/json',hidden:''});
  const importStatus=el('p',{'data-import-status':'',role:'status','aria-live':'polite',hidden:''});
  function importNotice(text){importStatus.textContent=text;importStatus.hidden=!text;}
  // File.text cannot be aborted. Reject stale reads after any new edit, action,
  // reload, second import, or unmount rather than replacing a newer document.
  const edited=event=>{if(event.target!==upload)++editRevision;};
  for(const type of ['input','change','click'])root.addEventListener(type,edited,true);
  upload.onchange=async()=>{
    const sequence=++importSequence,baseline=JSON.stringify(doc),file=upload.files[0];
    try{
      if(!file||stopped)return;
      if(window.ppgwPpsub.hasChanges()&&!await confirmAction('导入将替换未保存的编辑内容，继续吗？',{title:'导入 LovestSub',accept:'替换并导入',trigger:importMenu.firstElementChild}))return;
      if(stopped||sequence!==importSequence)return;
      if(baseline!==JSON.stringify(doc)){importNotice('编辑内容已变化，请重新选择文件。');return;}
      const revision=editRevision;
      importNotice('正在读取导入文件；期间的新编辑会保留。');
      const raw=await file.text();
      if(stopped||sequence!==importSequence)return;
      if(revision!==editRevision||baseline!==JSON.stringify(doc)){importNotice('编辑内容或操作已变化，已取消迟到的导入。请重新选择文件。');return;}
      const value=JSON.parse(raw);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('请选择 LovestSub JSON 配置');
      doc=documentModel(value);render();importNotice('已导入编辑器，保存并应用后生效。');root.dispatchEvent(new Event('input',{bubbles:true}));
    }catch(error){if(!stopped&&sequence===importSequence)importNotice('导入失败：'+error.message);}
    finally{if(!stopped&&sequence===importSequence)upload.value='';}
  };
  const importMenu=menu('导入 LovestSub',()=>upload.click());
  header.append(menu('导出编辑内容',download),importMenu,button('重新读取',()=>window.ppgwPpsub.loadCurrent(importConfig)),upload);
  const skeleton=el('div',{class:'editor-skeleton',role:'status',text:'正在读取配置…'});body.hidden=true;root.append(header,importStatus,skeleton,body);host.append(root);
  function field(row,key,label,type='text',options={}) {
    const id='editor-field-'+(++serial),wrap=el('label',{class:'editor-field'+(type==='checkbox'?' editor-check':''),for:id}),input=el(type==='lines'||type==='textarea'?'textarea':type==='select'?'select':'input',{id,'data-field':key});
    wrap.append(el('span',{text:label}),input);input.className='input';
    if(type==='select') for(const [value,title]of options.choices)input.append(el('option',{value,text:title}));
    else if(type!=='textarea'&&type!=='lines')input.type=type;
    if(type==='checkbox')input.checked=!!row[key];else input.value=type==='lines'?(Array.isArray(row[key])?row[key].join('\n'):key==='fixrule'&&typeof row.content==='string'?row.content:''):row[key]??options.default??'';
    if(type==='select'&&input.selectedIndex===-1){
      const invalid=el('option',{value:'',disabled:'',text:'请选择有效选项'});
      input.prepend(invalid);invalid.selected=true;
    }
    if(options.list)input.setAttribute('list',options.list);
    if(type==='number'){if(options.min!=null)input.min=options.min;if(options.max!=null)input.max=options.max;}
    input.addEventListener(type==='checkbox'||type==='select'?'change':'input',()=>{row[key]=type==='checkbox'?input.checked:type==='number'?(input.value===''?undefined:Number(input.value)):type==='lines'?input.value.split('\n').filter(v=>v.trim()):input.value;options.change?.(row[key]);if(key==='name'||key==='remark')for(const [selector,rows]of [['.subs',doc.subs],['.groups',doc['node-groups']],['.rules',doc.rules]])root.querySelectorAll(selector+' > .item').forEach((item,index)=>{const title=item.querySelector('.editor-item-tools strong'),value=(index+1)+'. '+(rows[index]?.name||rows[index]?.remark||'未命名');if(title.textContent!==value)title.textContent=value;});});
    return wrap;
  }
  function advanced(row,title='更多字段（JSON）') {
    const details=el('details',{class:'editor-advanced'}),area=el('textarea',{'aria-label':title,class:'input',rows:'7'}),status=el('p',{role:'status'});
    details.append(el('summary',{text:title}),el('p',{class:'subtext',text:'保留扩展字段；应用修改后请检查表单。'}),area,button('应用 JSON 修改',()=>{try{let value=JSON.parse(area.value);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('需要 JSON 对象');if(row===doc)value=documentModel(value);for(const key of Object.keys(row))delete row[key];Object.assign(row,value);render();root.dispatchEvent(new Event('input',{bubbles:true}));}catch(error){status.textContent=error.message;}}),status);
    details.addEventListener('toggle',()=>{if(details.open)area.value=JSON.stringify(row,null,2);});return details;
  }
  function section(key,title,create,fields) {
    const rows=doc[key],sectionClass=key==='node-groups'?'groups':key==='subs'?'subs':'rules',area=el('section',{class:sectionClass+' editor-section','data-editor-section':key}),top=el('div',{class:'editor-section-heading'},[el('h3',{text:title+'（'+rows.length+'）'})]);
    const add=button('＋ 添加'+title,()=>{const row=create();expanded.set(row,true);rows.push(row);if(key==='rules')rows.forEach((r,i)=>r.priority=(i+1)*10);render();const item=root.querySelector('.'+sectionClass+' > .item:last-of-type');item?.querySelector('input')?.focus({preventScroll:true});item?.scrollIntoView({block:'nearest',behavior:'smooth'});});add.setAttribute('aria-label','添加'+title);add.classList.add('editor-add');area.append(top);
    rows.forEach((row,index)=>{
      const item=el('details',{class:'item'}),tools=el('summary',{class:'editor-item-tools'},[el('strong',{text:(index+1)+'. '+(row.name||row.remark||title)})]);item.open=expanded.get(row)??index===0;item.addEventListener('toggle',()=>expanded.set(row,item.open));
      for(const [label,offset]of [['上移',-1],['下移',1]]){const b=button(label,()=>{move(rows,index,offset);if(key==='rules')rows.forEach((r,i)=>r.priority=(i+1)*10);render();});b.disabled=index+offset<0||index+offset>=rows.length;tools.append(b);}
      const remove=button('删除',async()=>{
        if(!await confirmAction('删除此'+title+'？',{title:'删除'+title,accept:'确认删除',trigger:remove}))return;
        const at=doc[key]===rows?rows.indexOf(row):-1;
        if(stopped||at<0)return;
        rows.splice(at,1);if(key==='rules')rows.forEach((r,i)=>r.priority=(i+1)*10);render();root.dispatchEvent(new Event('input',{bubbles:true}));
        body.querySelector('[data-editor-section="'+key+'"] .editor-add')?.focus({preventScroll:true});
      });tools.append(remove);
      const copy=button('复制',()=>{const duplicate=clone(row);if(duplicate.name)duplicate.name+=' 副本';expanded.set(duplicate,true);rows.splice(index+1,0,duplicate);if(key==='rules')rows.forEach((r,i)=>r.priority=(i+1)*10);render();});tools.append(copy);
      item.append(tools,...fields(row),advanced(row));area.append(item);
    });area.append(el('div',{class:'editor-section-footer'},[add]));return area;
  }
  function subscription(row){const url=field(row,'url','订阅链接','url');guardUrlInput(url.querySelector('input'),()=>row.url);return [el('div',{class:'row editor-grid'},[field(row,'name','名称'),url]),field(row,'isforced','强制覆盖节点名称','checkbox')];}
  function chips(row,key,label,{choices=[],placeholder='添加',display=value=>value}={}) {
    const wrap=el('div',{class:'editor-token-row',role:'group','aria-label':label}),tokens=el('div',{class:'editor-tokens'}),entry=el('div',{class:'editor-token-entry',hidden:''}),input=el('input',{class:'input','aria-label':'新增'+label,'data-field':key,placeholder}),add=button('＋ '+placeholder,()=>{entry.hidden=false;add.hidden=true;input.focus();});
    const values=()=>Array.isArray(row[key])?row[key]:[];
    const changed=()=>root.dispatchEvent(new Event('input',{bubbles:true}));
    function paint(){tokens.replaceChildren(...values().map((value,index)=>el('span',{class:'editor-token'},[el('span',{text:display(value)}),el('button',{type:'button','aria-label':'移除'+label+'：'+value,text:'×',onclick:()=>{row[key]=values().filter((_,i)=>i!==index);paint();changed();}})])));}
    const commit=()=>{const additions=input.value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);if(additions.length){row[key]=[...values()];for(const value of additions)if(!row[key].includes(value))row[key].push(value);paint();changed();}input.value='';entry.hidden=true;add.hidden=false;};
    input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();commit();}if(event.key==='Escape'){event.preventDefault();event.stopPropagation();input.value='';entry.hidden=true;add.hidden=false;add.focus();}};
    // Keep a typed token in the document when the user moves to another field.
    input.onchange=commit;
    if(choices.length){const id='editor-choices-'+(++serial),list=el('datalist',{id});for(const name of choices)list.append(el('option',{value:name}));input.setAttribute('list',id);entry.append(list);}
    entry.prepend(input);entry.append(button('添加',commit));paint();wrap.append(el('span',{class:'editor-inline-label',text:label}),tokens,add,entry);return wrap;
  }
  function group(row){
    const mode=groupMode(row),modes=el('div',{class:'editor-modes',role:'radiogroup','aria-label':'节点组模式'}),modeName='editor-mode-'+(++serial);
    for(const[value,title]of [['manual','手动选择'],['speedtest','自动测速'],['smart','智能测速']]){const input=el('input',{type:'radio',name:modeName,value});input.checked=mode===value;input.onchange=()=>{if(input.checked){setGroupMode(row,value);render();}};modes.append(el('label',{},[input,el('span',{text:title})]));}
    const names=[...new Set(['DIRECT','REJECT',...doc['node-groups'].map(r=>r.name),...window.ppgwPpsub.nodeChoices().map(r=>r.name)])].filter(name=>name&&name!==row.name);
    const sources=chips(row,'subs','订阅来源',{choices:['all',...doc.subs.map(value=>value.name).filter(Boolean)],placeholder:'选择订阅来源',display:value=>value==='all'?'全部订阅':value});
    const fixed=chips(row,'include','固定节点 / 组',{choices:names,placeholder:'添加节点或组'});
    const grid=el('div',{class:'editor-group-body'},[el('div',{class:'editor-group-basics'},[field(row,'name','节点组名称'),modes]),chips(row,'keywords','包含关键词',{placeholder:'添加关键词'}),chips(row,'exclude_keywords','排除关键词',{placeholder:'添加排除关键词'}),sources,fixed]);
    const extra=el('div',{class:'editor-group-extra'});
    if(mode==='smart'||mode==='speedtest')extra.append(field(row,'speedtest_url','测速地址','url'));
    if(mode==='speedtest')extra.append(field(row,'interval','测速间隔（秒）','number',{min:1,default:600}));
    if(mode==='manual'){
      grid.append(field(row,'failover','故障转移','checkbox',{change:enabled=>{setGroupFailover(row,enabled);render();}}));
      if(row.failover)extra.append(field(row,'monitor_url','检测地址','url'),field(row,'expected_status','期望 HTTP 状态码'),field(row,'retries','重试次数','number',{min:0,max:100,default:3}),
        el('p',{class:'subtext',text:'平时可手动切换节点；当前节点连续检测失败时自动切换到延迟最低的可用节点。'}));
    }
    grid.append(field(row,'use_pre_proxy','使用前置代理','checkbox',{change:()=>render()}));
    if(row.use_pre_proxy)extra.append(field(row,'pre_proxy_group','前置代理组','text',{list:'node-choices'}));
    if(extra.childElementCount)grid.append(extra);return[grid];
  }
  function rules(row){
    const choices=[['manual','手动规则'],['url','URL 下载'],['rule-set','规则集提供商']];
    const type=el('select',{'aria-label':'规则类型','data-field':'type',class:'input'});for(const [value,text]of choices)type.append(el('option',{value,text}));type.value=ruleType(row);type.onchange=()=>{setRuleType(row,type.value);render();};
    const grid=el('div',{class:'editor-grid'},[field(row,'remark','备注'),el('label',{class:'editor-field'},[el('span',{text:'规则类型'}),type])]);
    if(ruleType(row)==='manual')grid.append(field(row,'fixrule','规则（每行一条，顺序保持不变）','lines'));
    else grid.append(field(row,'url','规则链接','url'));
    if(ruleType(row)==='rule-set'){
      const defaults=ruleSetDefaults();
      const formats=['auto','yaml','text','mrs'];
      grid.append(field(row,'name','规则集名称'),
        field(row,'behavior','规则行为','select',{choices:['domain','ipcidr','classical'].map(value=>[value,value]),default:defaults.behavior}),
        field(row,'format','格式','select',{choices:formats.map(value=>[value,value]),default:defaults.format}),
        field(row,'interval','更新间隔（秒）','number',{max:604800,default:defaults.interval}),
        field(row,'proxy','下载使用的代理','text',{list:'node-choices'}));
    }
    if(ruleType(row)!=='rule-set')grid.append(field(row,'isforced','强制规则','checkbox'));return [grid];
  }
  function features(){
    const area=el('section',{class:'monitor glass'},[el('h3',{text:'全局选项'})]);
    const specs=[['global_monitor','全局连通监测',[['url','检测地址','url'],['retries','重试次数','number'],['expected_status','期望 HTTP 状态','text']]],['shadowsocks_in','Shadowsocks 入站',[['password','入站密码','password'],['cipher','加密方式','select',{choices:[[defaultCipher,defaultCipher],['2022-blake3-aes-128-gcm','2022-blake3-aes-128-gcm']],default:defaultCipher}]]],['restart_cron','定时重启',[['hour','每天重启时间（0–23 时）','number',{min:0,max:23}]]]];
    for(const [key,title,fields]of specs){const value=doc[key]||{};const group=el('fieldset',{'aria-label':title});const enable=field(value,'enable',title,'checkbox',{change:enabled=>{
      if(key==='shadowsocks_in'&&enabled)value.cipher??=defaultCipher;
      doc[key]=value;render();
    }});group.append(enable);if(value.enable){const inputs=el('div',{class:'editor-feature-fields'});for(const [name,label,type,options]of fields)inputs.append(field(value,name,label,type,options));group.append(inputs);}area.append(group);}return area;
  }
  function render(){
    ++editRevision;
    const optionsHost=root.querySelector('.editor-schedule')||root.querySelector('.ppgw-global-settings');
    const list=el('datalist',{id:'node-choices'}),names=[...new Set(['DIRECT','REJECT',...doc['node-groups'].map(r=>r.name),...window.ppgwPpsub.nodeChoices().map(r=>r.name)])];for(const name of names)list.append(el('option',{value:name}));
    body.replaceChildren(list,features(),section('subs','订阅提供商',()=>({name:'',url:''}),subscription),section('node-groups','节点组',()=>({name:'',subs:['all'],include:[]}),group),section('rules','规则',()=>({priority:(doc.rules.length+1)*10,fixrule:[]}),rules));
    if(optionsHost)body.querySelector('.monitor').append(optionsHost);
    body.append(advanced(doc,'完整配置与扩展字段（JSON）'));
    root.dispatchEvent(new Event('ppgw:editor-render'));
  }
  function showIssue(issue,message){
    if(!Number.isSafeInteger(issue.index)||issue.index<0)return;
    const section=[...body.querySelectorAll('[data-editor-section]')].find(node=>node.dataset.editorSection===issue.section);
    const item=section?.querySelectorAll(':scope > .item')[issue.index];
    if(!item)return;
    body.querySelector('[data-editor-issue]')?.remove();
    const previous=body.querySelector('[data-editor-invalid]');
    previous?.removeAttribute('aria-invalid');previous?.removeAttribute('aria-describedby');previous?.removeAttribute('data-editor-invalid');
    const field=[...item.querySelectorAll('[data-field]')].find(node=>node.dataset.field===issue.field);
    const note=el('p',{id:'editor-issue-'+(++serial),'data-editor-issue':'',class:'subtext',text:message});
    item.append(note);
    item.open=true;
    if(field){
      field.setAttribute('aria-invalid','true');field.setAttribute('aria-describedby',note.id);field.dataset.editorInvalid='';
      field.addEventListener('input',()=>{field.removeAttribute('aria-invalid');field.removeAttribute('aria-describedby');delete field.dataset.editorInvalid;note.remove();},{once:true});
      if(!field.closest('[hidden]'))field.focus({preventScroll:true});
    }
  }
  function importConfig(value){doc=documentModel(value);render();}
  destroy=window.ppgwPpsub.mount({header,navigate,confirmAction,setLoading:(loading,loaded)=>{root.dataset.editorState=loading?'loading':loaded?'loaded':'error';root.setAttribute('aria-busy',String(loading));body.hidden=loading||!loaded;skeleton.hidden=!loading&&loaded;skeleton.textContent=loading?'正在读取配置…':'当前配置尚未读取，请重新读取。';},exportConfig:()=>exported(doc),importConfig,showIssue,providers:()=>doc.subs});
  return ()=>{stopped=true;++importSequence;destroy();root.remove();};
}
