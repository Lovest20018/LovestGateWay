import {clone,documentModel,exported,move,groupMode,setGroupMode,ruleType,setRuleType} from './model.js';

export function mountEditor(host) {
  let doc=documentModel(),destroy,serial=0,editRevision=0,importSequence=0,stopped=false;
  const el=(tag,attrs={},children=[])=>{const node=document.createElement(tag);for(const [key,value]of Object.entries(attrs)){if(key==='text')node.textContent=value;else if(key.startsWith('on'))node.addEventListener(key.slice(2),value);else if(key==='class')node.className=value;else node.setAttribute(key,value);}node.append(...children);return node;};
  const button=(text,onclick)=>el('button',{type:'button',class:'btn',text,onclick});
  const root=el('section',{class:'source-editor','aria-label':'订阅与策略编辑器'}),header=el('header',{class:'phead'}),body=el('div',{class:'editor-body'});
  const menu=(text,action)=>el('span',{class:'menuwrap'},[button(text,action)]);
  const download=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)],{type:'application/json'}));const a=el('a',{href:url,download:'ppsub.json'});a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const upload=el('input',{type:'file',accept:'.json,application/json',hidden:''});
  const importStatus=el('p',{'data-import-status':'',role:'status','aria-live':'polite',hidden:''});
  function importNotice(text){importStatus.textContent=text;importStatus.hidden=!text;}
  // File.text cannot be aborted. Reject stale reads after any new edit, action,
  // reload, second import, or unmount rather than replacing a newer document.
  const edited=event=>{if(event.target!==upload)++editRevision;};
  for(const type of ['input','change','click'])root.addEventListener(type,edited,true);
  upload.onchange=async()=>{
    const sequence=++importSequence,revision=editRevision,baseline=JSON.stringify(doc),file=upload.files[0];
    try{
      if(!file||stopped)return;
      if(window.ppgwPpsub.hasChanges()&&!confirm('导入将替换未保存的编辑内容，继续吗？'))return;
      importNotice('正在读取导入文件；期间的新编辑会保留。');
      const raw=await file.text();
      if(stopped||sequence!==importSequence)return;
      if(revision!==editRevision||baseline!==JSON.stringify(doc)){importNotice('编辑内容或操作已变化，已取消迟到的导入。请重新选择文件。');return;}
      const value=JSON.parse(raw);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('请选择 PPsub JSON 配置');
      doc=documentModel(value);render();importNotice('已导入编辑器，保存并应用后生效。');root.dispatchEvent(new Event('input',{bubbles:true}));
    }catch(error){if(!stopped&&sequence===importSequence)importNotice('导入失败：'+error.message);}
    finally{if(!stopped&&sequence===importSequence)upload.value='';}
  };
  header.append(menu('导出编辑内容',download),menu('导入 PPsub',()=>upload.click()),button('重新读取',()=>window.ppgwPpsub.loadCurrent(importConfig)),upload);
  root.append(header,importStatus,body);host.append(root);
  function field(row,key,label,type='text',options={}) {
    const id='editor-field-'+(++serial),wrap=el('label',{class:'editor-field',for:id}),input=el(type==='lines'||type==='textarea'?'textarea':type==='select'?'select':'input',{id,'data-field':key});
    wrap.append(el('span',{text:label}),input);input.className='input';
    if(type==='select') for(const [value,title]of options.choices)input.append(el('option',{value,text:title}));
    else if(type!=='textarea'&&type!=='lines')input.type=type;
    if(type==='checkbox')input.checked=!!row[key];else input.value=type==='lines'?(Array.isArray(row[key])?row[key].join('\n'):key==='fixrule'&&typeof row.content==='string'?row.content:''):row[key]??options.default??'';
    if(options.readOnly)input.readOnly=true;if(options.list)input.setAttribute('list',options.list);
    if(type==='number'){if(options.min!=null)input.min=options.min;if(options.max!=null)input.max=options.max;}
    input.addEventListener(type==='checkbox'||type==='select'?'change':'input',()=>{row[key]=type==='checkbox'?input.checked:type==='number'?(input.value===''?undefined:Number(input.value)):type==='lines'?input.value.split('\n').filter(v=>v.trim()):input.value;options.change?.(row[key]);if(key==='name'||key==='remark')for(const [selector,rows]of [['.subs',doc.subs],['.groups',doc['node-groups']],['.rules',doc.rules]])root.querySelectorAll(selector+' > .item').forEach((item,index)=>{const title=item.querySelector('.editor-item-tools strong'),value=(index+1)+'. '+(rows[index]?.name||rows[index]?.remark||'未命名');if(title.textContent!==value)title.textContent=value;});});
    return wrap;
  }
  function advanced(row,title='更多字段（JSON）') {
    const details=el('details',{class:'editor-advanced'}),area=el('textarea',{'aria-label':title,class:'input',rows:'7'}),status=el('p',{role:'status'});
    details.append(el('summary',{text:title}),el('p',{class:'subtext',text:'保留扩展字段；应用修改后请检查表单。'}),area,button('应用 JSON 修改',()=>{try{let value=JSON.parse(area.value);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('需要 JSON 对象');if(row===doc)value=documentModel(value);if(window.ppgwPpsub.clashEditing()&&value._clash_id!==row._clash_id)throw Error('不能修改配置身份标识');for(const key of Object.keys(row))delete row[key];Object.assign(row,value);render();root.dispatchEvent(new Event('input',{bubbles:true}));}catch(error){status.textContent=error.message;}}),status);
    details.addEventListener('toggle',()=>{if(details.open)area.value=JSON.stringify(row,null,2);});return details;
  }
  function section(key,title,create,fields) {
    const rows=doc[key],full=window.ppgwPpsub.clashEditing(),area=el('section',{class:key==='node-groups'?'groups':key==='subs'?'subs':'rules'}),top=el('div',{class:'editor-section-heading'},[el('h3',{text:title})]);
    const add=button('添加'+title,()=>{rows.push(create());if(key==='rules')rows.forEach((r,i)=>r.priority=(i+1)*10);render();});if(full&&key==='subs')add.hidden=true;top.append(add);area.append(top);
    rows.forEach((row,index)=>{
      const item=el('article',{class:'item glass'}),tools=el('div',{class:'editor-item-tools'},[el('strong',{text:(index+1)+'. '+(row.name||row.remark||title)})]);
      for(const [label,offset]of [['上移',-1],['下移',1]]){const b=button(label,()=>{move(rows,index,offset);if(key==='rules')rows.forEach((r,i)=>r.priority=(i+1)*10);render();});b.disabled=index+offset<0||index+offset>=rows.length;if(full&&key==='subs')b.hidden=true;tools.append(b);}
      const remove=button('删除',()=>{if(!confirm('删除此'+title+'？'))return;rows.splice(index,1);if(key==='rules')rows.forEach((r,i)=>r.priority=(i+1)*10);render();});if(full&&key==='subs')remove.hidden=true;tools.append(remove);
      const copy=button('复制',()=>{const duplicate=clone(row);delete duplicate._clash_id;if(duplicate.name)duplicate.name+=' 副本';rows.splice(index+1,0,duplicate);if(key==='rules')rows.forEach((r,i)=>r.priority=(i+1)*10);render();});if(full&&key==='subs')copy.hidden=true;tools.append(copy);
      item.append(tools,...fields(row,full));if(!full)item.append(advanced(row));area.append(item);
    });return area;
  }
  function subscription(row,full){return [el('div',{class:'row editor-grid'},[field(row,'name','名称','text',{readOnly:full}),field(row,'url','订阅链接','url')]),...(!full?[field(row,'isforced','强制覆盖节点名称','checkbox')]:[])];}
  function group(row){
    const mode=el('select',{'aria-label':'节点组模式',class:'input'});for(const [value,text]of [['manual','手动选择'],['speedtest','自动测速'],['smart','智能选择'],['fallback','故障转移']])mode.append(el('option',{value,text}));mode.value=groupMode(row);mode.onchange=()=>{setGroupMode(row,mode.value);render();};
    const grid=el('div',{class:'editor-grid'},[field(row,'name','节点组名称'),el('label',{class:'editor-field'},[el('span',{text:'节点组模式'}),mode]),field(row,'subs','订阅提供商（每行一个，all 表示全部）','lines'),field(row,'include','固定节点 / 组（每行一个）','lines',{list:'node-choices'}),field(row,'keywords','包含关键词（每行一个，exp# 为正则）','lines'),field(row,'exclude_keywords','排除关键词（每行一个）','lines'),field(row,'use_pre_proxy','启用前置代理','checkbox'),field(row,'pre_proxy_group','前置代理组','text',{list:'node-choices'})]);
    const choices=el('select',{'aria-label':'可选节点或代理组',class:'input'});for(const name of [...new Set(['DIRECT','REJECT',...doc['node-groups'].map(r=>r.name),...window.ppgwPpsub.nodeChoices().map(r=>r.name)])].filter(name=>name&&name!==row.name))choices.append(el('option',{value:name,text:name}));grid.append(el('div',{class:'editor-field'},[el('span',{text:'添加固定节点或代理组'}),choices,button('加入固定列表',()=>{row.include=[...new Set([...(row.include||[]),choices.value])];render();})]));
    if(['smart','speedtest'].includes(groupMode(row)))grid.append(field(row,'speedtest_url','测速地址','url'));
    if(groupMode(row)==='speedtest')grid.append(field(row,'interval','测速间隔（秒）','number',{min:1,default:600}));
    if(row.failover)grid.append(field(row,'monitor_url','检测地址','url'),field(row,'expected_status','期望 HTTP 状态码'),field(row,'retries','重试次数','number',{min:1,default:3}));return[grid];
  }
  function rules(row,full){
    const choices=full?[['manual','完整规则'],['rule-set','规则集提供商']]:[['manual','手动规则'],['url','URL 下载'],['rule-set','规则集提供商']];
    const type=el('select',{'aria-label':'规则类型',class:'input'});for(const [value,text]of choices)type.append(el('option',{value,text}));type.value=ruleType(row);type.onchange=()=>{setRuleType(row,type.value);render();};if(full&&row._clash_id)type.disabled=true;
    const grid=el('div',{class:'editor-grid'},[field(row,'remark','备注'),el('label',{class:'editor-field'},[el('span',{text:'规则类型'}),type])]);
    if(ruleType(row)==='manual')grid.append(field(row,'fixrule','规则（每行一条，顺序保持不变）','lines'));
    else grid.append(field(row,'url','规则链接','url'));
    if(ruleType(row)==='rule-set')grid.append(field(row,'name','规则集名称'),field(row,'behavior','规则行为','select',{choices:[['domain','domain'],['ipcidr','ipcidr'],['classical','classical']]}),field(row,'format','格式','select',{choices:[['auto','auto'],['yaml','yaml'],['text','text'],['mrs','mrs']]}),field(row,'interval','更新间隔（秒）','number',{min:1,default:86400}),field(row,'proxy','下载使用的代理','text',{list:'node-choices'}));
    if(!full&&ruleType(row)!=='rule-set')grid.append(field(row,'isforced','强制规则','checkbox'));return [grid];
  }
  function features(){
    const area=el('section',{class:'monitor glass'},[el('h3',{text:'全局选项'})]);
    const specs=[['global_monitor','全局连通监测',[['url','检测地址','url'],['retries','重试次数','number'],['expected_status','期望 HTTP 状态','text']]],['shadowsocks_in','Shadowsocks 入站',[['password','入站密码','password'],['cipher','加密方式','select',{choices:[['aes-128-gcm','aes-128-gcm'],['2022-blake3-aes-128-gcm','2022-blake3-aes-128-gcm']]}]]],['restart_cron','定时重启',[['hour','每天重启时间（0–23 时）','number',{min:0,max:23}]]]];
    for(const [key,title,fields]of specs){const value=doc[key]||{};const group=el('fieldset',{},[el('legend',{text:title})]);const enable=field(value,'enable','启用','checkbox',{change:()=>{doc[key]=value;render();}});group.append(enable);if(value.enable)for(const [name,label,type,options]of fields)group.append(field(value,name,label,type,options));area.append(group);}return area;
  }
  function render(){
    ++editRevision;
    const optionsHost=root.querySelector('.ppgw-global-settings');
    const full=window.ppgwPpsub.clashEditing(),list=el('datalist',{id:'node-choices'}),names=[...new Set(['DIRECT','REJECT',...doc['node-groups'].map(r=>r.name),...window.ppgwPpsub.nodeChoices().map(r=>r.name)])];for(const name of names)list.append(el('option',{value:name}));
    body.replaceChildren(list,features(),section('subs','订阅提供商',()=>({name:'',url:''}),subscription),section('node-groups','节点组',()=>({name:'',subs:['all'],include:[]}),group),section('rules','规则',()=>({priority:(doc.rules.length+1)*10,fixrule:[]}),rules));
    if(optionsHost)body.querySelector('.monitor').append(optionsHost);
    if(!full)body.append(advanced(doc,'完整配置与扩展字段（JSON）'));
  }
  function importConfig(value){doc=documentModel(value);render();}
  render();destroy=window.ppgwPpsub.mount({header,exportConfig:()=>exported(doc),importConfig,providers:()=>doc.subs});
  return ()=>{stopped=true;++importSequence;destroy();root.remove();};
}
