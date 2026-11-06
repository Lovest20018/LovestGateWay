import {storedSecret} from './auth.js';

const storageKey='lovestgateway-recovery-job';
const services={'paopaogw':'转发核心','paopaogw-settings':'管理任务服务','paopaogw-subscription':'订阅任务服务','paopaogw-api':'观测 API','paopaogw-observe':'历史采集','paopaogw-maintenance':'核心维护','paopaogw-maintenance.timer':'维护计划'};
const kinds={manual:'手动完整备份',config:'配置变更',deploy:'安装升级',restore:'恢复备份',recover:'中断事务恢复','restore-rescue':'恢复前救援备份',asset:'资源导入',policy:'策略变更','policy-reset':'策略重置',backup:'创建完整备份'};
const sections={configuration:'配置与凭据',assets:'订阅与资源',program:'管理程序与页面',services:'服务定义',runtime_data:'运行数据'};
const phases={queued:'等待处理',prepared:'准备恢复',validating:'校验',stopping:'停止受影响服务',saving:'保存备份',restoring:'恢复文件',applying:'应用',verifying:'验证运行状态',rolling_back:'回退',rollback_verifying:'验证回退结果',rollback_unconfirmed:'回退待核实',interrupted:'任务中断',complete:'完成',rolled_back:'已回退'};
const errors={
  LOGIN_REQUIRED:'登录已失效，请重新登录后继续查询同一任务。',
  MANAGEMENT_BUSY:'已有管理任务正在执行，完成后再操作。',
  RECOVERY_REQUIRED:'存在未完成事务，请先预览并恢复中断事务。',
  EXTERNAL_CONFIG_CONFLICT:'配置已被其他操作修改。请核对后重新预览，系统没有覆盖这些修改。',
  PREVIEW_EXPIRED:'恢复预览已过期，请重新预览。',
  STALE_RECOVERY_PREVIEW:'配置、服务或备份已变化，请重新预览。',
  BACKUP_CHECKSUM_MISMATCH:'备份校验失败，不能恢复此备份。',
  INVALID_BACKUP:'备份不完整或格式不受支持，不能恢复。',
  NOTHING_TO_RECOVER:'没有待恢复的中断事务。',
  INVALID_TRANSACTION_JOURNAL:'事务记录无法校验，请使用管理脚本核对，勿重复应用。',
  READINESS_TIMEOUT:'转发核心未通过运行验收，请检查服务与端口占用。',
  API_READINESS_TIMEOUT:'观测 API 未通过运行验收，请检查 API 服务与监听端口。',
  SERVICE_STATE_MISMATCH:'实际服务状态与恢复目标不同，请检查服务。',
  INTERRUPTED_CHECK_RECOVERY:'任务已中断，请核对事务恢复状态。',
  ROLLBACK_UNCONFIRMED:'回退后运行状态尚未确认，请预览中断事务恢复。',
  RESTORE_FAILED_PREVIOUS_STATE_RESTORED:'恢复未完成，已还原恢复前状态并完成运行验证。',
  BACKUP_FAILED_SERVICES_RESTORED:'备份未完成，已还原服务状态并完成运行验证。',
  APPLY_FAILED_ROLLED_BACK:'应用未完成，已回退并验证。',
  STATE_SAMPLE_UNAVAILABLE:'部分运行状态暂时无法读取，请刷新后核对。',
  JOB_NOT_FOUND:'暂未查到此任务。请保留任务号并继续核对，勿重复提交。',
  JOB_ID_REUSED:'任务身份冲突，请保留任务号并核对管理记录。',
};
const message=code=>Object.hasOwn(errors,code)?errors[code]:'操作未完成，请核对下方诊断与最近操作记录。';
export const operationError=row=>row?.error==='MANAGEMENT_BUSY'&&!row.recovery_required&&!['queued','running','applying'].includes(row.status)?'该次操作因其他管理任务占用未执行。':message(row?.error);
const time=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString():'未知';
const el=(tag,attrs={},children=[])=>{const node=document.createElement(tag);for(const[key,value]of Object.entries(attrs)){if(key==='text')node.textContent=value;else if(key.startsWith('on'))node.addEventListener(key.slice(2),value);else node.setAttribute(key,value);}node.append(...children);return node;};
const button=(text,onclick,extra={})=>el('button',{type:'button',class:'btn',text,onclick,...extra});

export function pendingRecoveryJob(){try{const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');if(saved&&/^[a-f0-9]{32}$/.test(saved.id)&&['backup','restore','recover'].includes(saved.kind))return{id:saved.id,kind:saved.kind};}catch{}return null;}

const publicErrors=new Set([...Object.keys(errors),'OPERATION_FAILED','DEPLOY_FAILED_ROLLED_BACK','CONFIG_REVISION_CONFLICT','INVALID_RECOVERY_PREVIEW','INVALID_RECOVERY_REQUEST','REVISION_KEY_REQUIRED','SOURCE_CONFLICT','CORE_NOT_RUNNING','MIHOMO_REQUIRED','ASSET_REQUIRED','SUBSCRIPTION_REQUIRED','INTERFACE_REQUIRED','INTERFACE_IPV4_REQUIRED','NETWORK_RESOURCE_BUSY','LISTENER_UNAVAILABLE','SYSTEM_OPERATION_FAILED','STALE_POLICY_SNAPSHOT']);
const oneOf=(value,allowed,fallback='unknown')=>allowed.includes(value)?value:fallback;
const publicTime=value=>Number.isFinite(value)&&value>=0?value:null;
export function publicReport(value){
  const safeServices={};for(const name of Object.keys(services))if(value?.services?.[name]&&typeof value.services[name]==='object')safeServices[name]={active:value.services[name].active===true,enabled:value.services[name].enabled===true};
  const safeCode=value=>publicErrors.has(value)?value:'OPERATION_FAILED';
  return {schema:1,generated_at:publicTime(value?.generated_at),services:safeServices,
    runtime:{state:oneOf(value?.runtime?.state,['running','staged','unknown'])},
    recovery:{required:value?.recovery?.required===true,phase:oneOf(value?.recovery?.phase,[...Object.keys(phases),'none','conflict'])},
    source:{kind:oneOf(value?.source?.kind,['none','local_yaml','remote_yaml','native_ppsub']),policy_state:oneOf(value?.source?.policy_state,['none','invalid','active','source_mismatch']),overridden_sections:Number.isSafeInteger(value?.source?.overridden_sections)&&value.source.overridden_sections>=0?value.source.overridden_sections:0},
    recent:(Array.isArray(value?.recent)?value.recent:[]).slice(0,20).map(row=>({source:oneOf(row?.source,['settings','subscription','mihomo']),kind:oneOf(row?.kind,[...Object.keys(kinds),'policy_reset','check','update','rollback','install-local','scheduled','apply']),status:oneOf(row?.status,['queued','running','applying','succeeded','failed','interrupted','applied','unchanged','complete']),phase:oneOf(row?.phase,[...Object.keys(phases),'conflict','installing']),updated_at:publicTime(row?.updated_at),recovery_required:row?.recovery_required===true,...(row?.error?{error:safeCode(row.error)}:{})})),
    causes:[...new Set((Array.isArray(value?.causes)?value.causes:[]).map(safeCode))]};
}

export function mountDiagnostics(root,{onUnauthorized=()=>{},onComplete=()=>{}}={}) {
  const lifetime=new AbortController(),timers=new Set();
  let stopped=false,report=null,pending=pendingRecoveryJob(),preview=null,request=0,loading=false,polling=false,pollTimer=null,offset=0,notFoundId=null,reviewBusy=false;
  const status=el('div',{class:'notice',role:'status','aria-live':'polite',hidden:''}),task=el('section',{class:'card glass diagnostic-task',hidden:''});
  const overview=el('div',{id:'diagnostic-overview',class:'diagnostic-overview',role:'tabpanel','aria-labelledby':'diagnostic-tab-overview'}),list=el('div',{class:'diagnostic-backups'});
  const recentHost=el('section',{id:'diagnostic-recent',class:'card glass',role:'tabpanel','aria-labelledby':'diagnostic-tab-recent',hidden:''});
  const unknownReview=el('div',{'data-unknown-task':'',hidden:''});
  const refresh=button('刷新诊断',()=>refreshAll()),download=button('导出脱敏报告',()=>exportReport()),backup=button('创建完整备份',()=>confirmBackup()),recover=button('预览中断事务恢复',()=>showPreview('recover'));
  const dialog=el('section',{'aria-labelledby':'recovery-preview-title',class:'diagnostic-preview card glass',hidden:'',tabindex:'-1'}),dialogBody=el('div',{class:'dialog-body'});
  const cancel=button('返回',()=>{preview=null;dialog.hidden=true;}),apply=button('确认执行',()=>submitPreview(),{class:'btn btn-brand'}),detail=el('div');
  dialogBody.append(el('h2',{id:'recovery-preview-title',text:'恢复影响预览'}),detail,el('div',{class:'button-row dialog-actions'},[cancel,apply]));dialog.append(dialogBody);
  dialog.addEventListener('cancel',()=>{preview=null;});
  root.classList.add('diagnostic-page');
  const backupPanel=el('section',{id:'diagnostic-backups',class:'card glass',role:'tabpanel','aria-labelledby':'diagnostic-tab-backups',hidden:''},[el('div',{class:'card-heading'},[el('h2',{text:'备份与事务恢复'}),el('p',{class:'subtext',text:'完整备份包含程序、配置、历史凭据和运行数据。创建或恢复时可能短暂停止转发；应用前会展示影响范围。'})]),el('div',{class:'button-row diagnostic-actions'},[backup,recover]),list]);
  const panels={overview,backups:backupPanel,recent:recentHost},tabItems=[['overview','运行诊断'],['backups','备份与恢复'],['recent','最近操作']];
  const tabs=el('div',{class:'tabs page-tabs',role:'tablist','aria-label':'诊断与恢复分栏'},tabItems.map(([key,text],index)=>button(text,()=>selectTab(key),{id:'diagnostic-tab-'+key,role:'tab','aria-controls':panels[key].id,'aria-selected':String(key==='overview'),tabindex:key==='overview'?'0':'-1',class:key==='overview'?'active':'',onkeydown:event=>{
    const next={ArrowRight:(index+1)%tabItems.length,ArrowLeft:(index+tabItems.length-1)%tabItems.length,Home:0,End:tabItems.length-1}[event.key];if(next!==undefined){event.preventDefault();selectTab(tabItems[next][0]);}
  }})));
  function selectTab(key){for(const [name,panel]of Object.entries(panels))panel.hidden=name!==key;for(const tab of tabs.children){const selected=tab.id==='diagnostic-tab-'+key;tab.classList.toggle('active',selected);tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;}tabs.querySelector('[aria-selected="true"]').focus({preventScroll:true});}
  root.replaceChildren(el('header',{class:'page-head'},[el('h1',{class:'page-title',text:'诊断与恢复'}),el('div',{class:'button-row'},[download,refresh])]),el('p',{class:'page-description',text:'核对运行状态、配置来源和最近故障，按需备份与恢复。'}),status,tabs,task,overview,backupPanel,recentHost,dialog);
  function later(callback,ms){const id=setTimeout(()=>{timers.delete(id);if(!stopped)callback();},ms);timers.add(id);}
  function schedulePoll(ms){if(pollTimer!==null)clearTimeout(pollTimer);pollTimer=setTimeout(()=>{pollTimer=null;pollJob();},ms);}
  async function api(path,body){
    const response=await fetch('/sniffbox/settings/'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+storedSecret(),...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:AbortSignal.any([lifetime.signal,AbortSignal.timeout(15000)])});
    const data=await response.json().catch(()=>({}));
    if(stopped)throw new DOMException('View closed','AbortError');
    if(!response.ok){if(response.status===401)onUnauthorized();throw Object.assign(Error(message(data.error)),{status:response.status,code:data.error});}return data;
  }
  function notice(text,kind=''){if(stopped)return;status.textContent=text;status.hidden=!text;status.className='notice '+kind;}
  function controls(){const busy=!!pending||loading;for(const control of [refresh,backup,recover])control.disabled=busy;download.disabled=loading;list.querySelectorAll('button').forEach(control=>control.disabled=busy||control.dataset.invalid==='true');}
  function persist(){if(pending)sessionStorage.setItem(storageKey,JSON.stringify({id:pending.id,kind:pending.kind}));else sessionStorage.removeItem(storageKey);}
  function renderTask(value){
    task.hidden=!pending;if(!pending)return;
    task.replaceChildren(el('h2',{text:kinds[pending.kind]||'恢复任务'}),el('p',{text:'任务号：'+pending.id}),el('p',{text:value?phases[value.phase||value.stage]||'正在核对结果':'任务已保留，正在核对提交与运行结果。'}),button('重新查询此任务',()=>pollJob()),unknownReview);
    controls();
  }
  async function reviewUnknownTask(){
    if(stopped||!pending||pending.id!==notFoundId||reviewBusy)return;const id=pending.id;reviewBusy=true;unknownReview.hidden=false;unknownReview.replaceChildren(el('p',{text:'正在读取诊断与最近操作；任务号仍保留，尚未解除追踪。'}));
    try{
      const raw=await api('diagnostics');if(stopped||pending?.id!==id||notFoundId!==id)return;
      const value=publicReport(raw);report=value;renderReport(value);
      const reliable=raw?.schema===1&&typeof raw?.recovery?.required==='boolean';
      unknownReview.replaceChildren(el('p',{text:'服务器暂未找到此任务记录。下方诊断与最近操作供核对；这不代表任务未执行、失败或完成。'}));
      if(!reliable||value.recovery.required){unknownReview.append(el('p',{text:reliable?'仍存在待恢复事务，暂不解除追踪。请先通过管理脚本核对并恢复，再重新核对诊断。':'诊断状态无法确认，暂不解除追踪。请核对管理脚本结果后重试。'}));}
      else {
        const acknowledgement=el('input',{type:'checkbox'}),release=button('仅本浏览器停止追踪',()=>releaseUnknownTask(id,acknowledgement),{disabled:''});
        acknowledgement.onchange=()=>{release.disabled=!acknowledgement.checked;};
        unknownReview.append(el('label',{class:'diagnostic-ack'},[acknowledgement,document.createTextNode('已通过管理脚本或状态核对该任务没有执行或已结束')]),el('p',{class:'subtext',text:'仅清除此浏览器的追踪记录，不代表服务端取消、失败或完成；不会重放或重新提交任务。'}),release);
      }
      unknownReview.append(button('重新核对诊断',()=>reviewUnknownTask()));
    }catch(error){if(!stopped&&pending?.id===id)unknownReview.replaceChildren(el('p',{text:error.message}),button('重新核对诊断',()=>reviewUnknownTask()));}
    finally{reviewBusy=false;}
  }
  async function releaseUnknownTask(id,acknowledgement){
    if(stopped||reviewBusy||!acknowledgement.checked||pending?.id!==id||notFoundId!==id)return;reviewBusy=true;
    try{
      const raw=await api('diagnostics');if(stopped||pending?.id!==id||notFoundId!==id)return;report=publicReport(raw);renderReport(report);
      if(raw?.schema!==1||typeof raw?.recovery?.required!=='boolean'||report.recovery.required){unknownReview.replaceChildren(el('p',{text:'仍有待恢复事务或状态无法确认，追踪记录已保留。请先通过管理脚本核对并恢复。'}),button('重新核对诊断',()=>reviewUnknownTask()));return;}
      pending=null;notFoundId=null;persist();task.hidden=true;controls();notice('本浏览器已停止追踪该任务；没有取消、重放或重新提交服务端任务。');await refreshAll();
    }catch(error){if(!stopped)notice(error.message,'error');}
    finally{reviewBusy=false;}
  }
  function renderReport(value){
    const card=(title,children)=>el('section',{class:'card glass'},[el('div',{class:'card-heading'},[el('h2',{text:title})]),...children]);
    const rows=Object.entries(value.services||{}).filter(([name])=>Object.hasOwn(services,name)).map(([name,state])=>el('li',{text:services[name]+'：'+(state.active?'运行中':'未运行')+' · '+(state.enabled?'开机启用':'未设为开机启用')}));
    const runtime={running:'核心响应正常',staged:'暂存环境，未执行真实服务验收',unknown:'核心状态尚未确认'};
    const source={none:'未配置订阅',local_yaml:'本地 YAML',remote_yaml:'远程完整配置',native_ppsub:'原生 PPsub',unknown:'尚未确认'};
    const policy={active:'本地覆盖生效',none:'无本地覆盖',invalid:'本地策略无法校验',source_mismatch:'本地策略与当前来源不一致',unknown:'尚未确认'};
    const causes=(value.causes||[]).map(code=>el('li',{text:message(code)}));
    const recent=(value.recent||[]).slice(0,20).map(row=>el('li',{},[el('div',{class:'operation-heading'},[el('strong',{text:(kinds[row.kind]||'管理操作')+' · '+({succeeded:'成功',failed:'失败',interrupted:'中断',running:'执行中',queued:'等待中',applying:'应用中',applied:'已应用',unchanged:'未变化',complete:'已完成'}[row.status]||'待核对')})]),el('p',{class:'subtext',text:time(row.updated_at)+(row.error?' · '+operationError(row):'')} )]));
    overview.replaceChildren(card('当前状态',[el('p',{class:'diagnostic-runtime',text:runtime[value.runtime?.state]||runtime.unknown}),el('ul',{class:'diagnostic-services'},rows),el('p',{class:'subtext',text:'采样时间：'+time(value.generated_at)})]),card('配置来源与故障',[el('p',{text:(source[value.source?.kind]||source.unknown)+' · '+(policy[value.source?.policy_state]||policy.unknown)}),el('p',{text:value.recovery?.required?'存在待核对事务：'+(phases[value.recovery.phase]||'状态未知'):'没有已记录的待恢复事务。'}),causes.length?el('ul',{},causes):el('p',{text:'本次诊断未发现已记录的故障。'}),el('p',{class:'subtext',text:'本页核对网关本机状态；客户端 DNS、路由和透明转发仍需从对应客户端验证。'})]));
    recentHost.replaceChildren(el('div',{class:'card-heading'},[el('h2',{text:'最近操作'}),el('p',{class:'subtext',text:'最近 '+recent.length+' 条管理、订阅与核心维护记录。'})]),recent.length?el('ol',{class:'operation-list'},recent):el('p',{class:'empty-state',text:'暂无操作记录。'}));
  }
  function renderBackups(value){
    offset=value.offset||0;
    const rows=(value.backups||[]).map(row=>{
      const action=button('预览恢复',()=>showPreview('restore',row.id));action.dataset.invalid=String(row.integrity!=='verified');
      return el('article',{class:'diagnostic-backup'},[el('strong',{text:kinds[row.kind]||'历史备份'}),el('p',{text:time(row.created_at)+' · '+(row.full?'完整范围':'部分范围')+' · '+(Number.isInteger(row.file_count)?row.file_count+' 个文件':'文件数未知')}),el('code',{text:row.id}),el('p',{class:row.integrity==='verified'?'subtext':'notice error',text:row.integrity==='verified'?'全部备份文件校验通过':'校验未通过，恢复已停用'}),action]);
    });
    const previous=button('上一页',()=>loadBackups(Math.max(0,offset-10))),next=button('下一页',()=>loadBackups(value.next_offset));
    previous.hidden=offset===0;next.hidden=value.next_offset===null||value.next_offset===undefined;
    list.replaceChildren(el('p',{class:'subtext',text:'共 '+value.total+' 份备份；当前展示 '+rows.length+' 份。'}),...rows,el('div',{class:'button-row'},[previous,next]));controls();
  }
  async function loadBackups(next=0){if(loading||pending)return;loading=true;controls();try{renderBackups(await api('backups/list',{offset:next,limit:10}));}catch(error){if(!stopped)notice(error.message,'error');}finally{loading=false;controls();}}
  async function refreshAll(){if(loading||pending)return;const turn=++request;loading=true;controls();try{const [data,backups]=await Promise.all([api('diagnostics'),api('backups/list',{offset,limit:10})]);if(stopped||turn!==request)return;report=publicReport(data);renderReport(report);renderBackups(backups);}catch(error){if(!stopped&&turn===request)notice(error.message,'error');}finally{if(turn===request){loading=false;controls();}}}
  async function showPreview(action,id){
    if(loading||pending)return;loading=true;controls();notice('');
    try{
      const value=await api(action+'/preview',action==='restore'?{backup:id}:{});if(stopped)return;
      if(typeof value.preview_token!=='string'||!value.preview_token||value.preview_token.length>16384)throw Error('恢复预览无法校验，请刷新诊断后重新预览。');
      preview={action,value};detail.replaceChildren(el('p',{text:action==='restore'?'目标备份：'+value.backup:'恢复事务记录中的回退状态。'}),el('p',{class:'subtext',text:'本预览有效至 '+time(value.expires_at)+'；配置、服务或备份变化后必须重新预览。'}));
      const table=el('table',{class:'diagnostic-scope'}),head=el('tr',{},['范围','新增','修改','移除','不变'].map(text=>el('th',{text})));table.append(el('thead',{},[head]),el('tbody',{},Object.entries(sections).map(([key,label])=>el('tr',{},[el('th',{text:label}),...['added','modified','removed','unchanged'].map(field=>el('td',{text:String(value.scope?.[key]?.[field]||0)}))]))));detail.append(table);
      const impact=value.impact||{},notes=[];
      const targets=el('section',{},[el('h3',{text:'恢复后的服务状态'})]);
      const serviceLabel=state=>state?(state.active?'运行':'停止')+' · '+(state.enabled?'开机启用':'关闭开机启用'):'未记录';
      const states=Object.entries(value.effective_services||{}).filter(([name])=>Object.hasOwn(services,name)).map(([name,state])=>el('li',{text:services[name]+'：'+serviceLabel(state)+'；备份记录：'+serviceLabel(value.target_services?.[name])+(impact.preserved_services?.includes(name)?'（保留当前执行服务）':'')}));
      targets.append(el('ul',{},states));detail.append(targets);
      if(impact.connections_may_drop)notes.push('转发核心可能重启，活动连接可能中断。');
      if(impact.historical_credentials_may_return)notes.push('备份中的历史密码和令牌可能恢复，完成后可能需要使用恢复后的密码重新登录。');
      if(impact.version_may_change)notes.push('程序与页面版本可能回到备份时的版本。');
      if(impact.settings_worker_restart_required)notes.push('当前管理任务服务会保留运行；完成后需要重启管理任务服务，以加载恢复后的程序。');
      if(impact.settings_worker_state_preserved)notes.push('管理任务服务的运行与开机启用状态保留当前值，作为本次恢复的明确例外；不会自动恢复为备份记录的状态。');
      notes.push('将恢复并核对服务状态；目标核心与 API 启用时，还会验证其实际响应。');
      if(impact.service_restart?.length)notes.push('需要恢复运行的服务：'+impact.service_restart.map(name=>services[name]||'管理服务').join('、')+'。');
      detail.append(el('ul',{},notes.map(text=>el('li',{text}))));apply.textContent=action==='restore'?'确认恢复此备份':'确认恢复中断事务';dialog.hidden=false;dialog.scrollIntoView({behavior:'smooth',block:'start'});dialog.focus({preventScroll:true});
    }catch(error){if(!stopped)notice(error.message,'error');}finally{loading=false;controls();}
  }
  function confirmBackup(){if(loading||pending)return;preview={action:'backup'};detail.replaceChildren(el('p',{text:'将短暂停止受影响服务，创建完整文件快照后恢复原服务状态，并验证转发核心与已启用 API。活动连接可能中断。'}),el('p',{text:'备份包含程序、配置、资源和运行数据，保存在本机受限目录中。'}));apply.textContent='确认创建完整备份';dialog.hidden=false;dialog.scrollIntoView({behavior:'smooth',block:'start'});dialog.focus({preventScroll:true});}
  async function submitPreview(){
    if(!preview||pending||loading)return;const selected=preview;preview=null;dialog.hidden=true;
    const id=[...crypto.getRandomValues(new Uint8Array(16))].map(value=>value.toString(16).padStart(2,'0')).join('');
    pending={id,kind:selected.action};notFoundId=null;unknownReview.hidden=true;unknownReview.replaceChildren();persist();renderTask();controls();
    try{const value=await api('apply',{id,kind:selected.action,payload:selected.action==='backup'?{}:{preview_token:selected.value.preview_token}});if(stopped)return;renderTask(value);await pollJob();}
    catch(error){if(stopped)return;if([400,401,403,409].includes(error.status)&&error.code!=='JOB_ID_REUSED'){pending=null;persist();task.hidden=true;controls();notice(error.message,'error');}else{notice('提交结果尚未确认。任务号已保留，正在查询同一任务；请勿重复执行。');schedulePoll(1000);}}
  }
  async function pollJob(){
    if(stopped||!pending)return;
    // A previous terminal poll may still be refreshing runtime information.
    // Keep the newly accepted job scheduled until that poll releases the guard.
    if(polling){schedulePoll(250);return;}
    if(pollTimer!==null){clearTimeout(pollTimer);pollTimer=null;}polling=true;const id=pending.id;
    try{const value=await api('jobs/'+id);if(stopped||pending?.id!==id)return;notFoundId=null;unknownReview.hidden=true;unknownReview.replaceChildren();renderTask(value);
      if(['succeeded','failed','interrupted'].includes(value.status)){
        const kind=pending.kind;pending=null;persist();task.hidden=true;controls();
        if(value.status==='succeeded'){
          const acceptance=value.result?.acceptance||{},labels={verified:'验收通过',inactive:'目标未启用',staged:'暂存，未执行真实验收'};
          const checks=[['services','服务状态'],['core','转发核心'],['api','观测 API']].map(([key,label])=>label+'：'+(labels[acceptance[key]]||'结果待核对')).join('；');
          notice((kinds[kind]||'管理操作')+'已完成。'+checks+'。','success');
          if(value.result?.settings_worker_restart_required)status.append(el('p',{text:'确认任务已完成后，在网关终端执行以下命令，以加载恢复后的管理程序，然后刷新页面：'}),el('code',{text:'sudo systemctl restart paopaogw-settings'}));
          if(value.result?.settings_worker_state_preserved)status.append(el('p',{text:'管理任务服务的运行与开机启用状态已保留当前值，未恢复为备份记录的状态；重启服务只会重新加载程序，不改变开机启用状态。'}));
        }
        else notice(message(value.error),'error');
        await refreshAll();await onComplete(value);
      }else schedulePoll(1500);
    }catch(error){if(!stopped&&pending?.id===id){notice((error.status===401?message('LOGIN_REQUIRED'):'任务结果暂未确认，任务号已保留。请继续查询此任务，勿重复提交。'));if(error.status===404){notFoundId=id;await reviewUnknownTask();}else if(error.status!==401)schedulePoll(3000);}}
    finally{polling=false;}
  }
  async function exportReport(){if(loading)return;try{const value=publicReport(await api('diagnostics'));if(stopped)return;report=value;renderReport(value);const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'})),link=el('a',{href:url,download:'lovestgateway-diagnostics.json'});link.click();later(()=>URL.revokeObjectURL(url),1000);}catch(error){if(!stopped)notice(error.message,'error');}}
  function allowNavigation(){if(!pending)return true;notice('任务仍在执行或等待核对，请先查询任务结果。');return false;}
  if(pending){renderTask();pollJob();}else refreshAll();
  return {allowNavigation,refresh:()=>pending?pollJob():refreshAll(),dispose(){stopped=true;++request;lifetime.abort();if(pollTimer!==null)clearTimeout(pollTimer);for(const id of timers)clearTimeout(id);timers.clear();dialog.remove();}};
}
