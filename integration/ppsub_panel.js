/* Debian persistence for the existing PPsub editor; no separate form or login. */
(() => {
  let current = null;
  const loginRequired = '登录已失效，请重新登录；任务与未保存的修改仍保留。';

  const editorErrors = {
    CONFIG_REVISION_CONFLICT: '配置已变化，请重新读取后再保存。',
    SOURCE_CHANGE_REQUIRES_SWITCH: '请在“配置来源”切换到 LovestSub。',
    MANAGEMENT_BUSY: '其他管理操作正在进行，请稍后重试。',
    CONFIG_CHANGED: '配置已变化，请重新读取后再保存。',
    RECOVERY_REQUIRED: '网关有待恢复的配置，请先检查诊断页面。',
    INVALID_EDITOR_REQUEST: '编辑请求有误，请重新读取配置后再试。',
    INVALID_EDITOR_CONFIG: '编辑内容格式无效，请检查配置结构。',
    INVALID_PPSUB: 'LovestSub 配置应包含订阅和正确的列表结构。',
    INVALID_PPSUB_ROW: '此条配置必须是对象。',
    INVALID_PPSUB_NAME: '名称不能为空或包含无效字符。',
    DUPLICATE_PPSUB_NAME: '名称重复，请改用其他名称。',
    INVALID_SUBSCRIPTION_SCHEDULE: '更新计划无效，请检查更新方式和间隔。',
    INVALID_EDITOR_LIST: '列表中应逐项填写文本。',
    INVALID_EDITOR_GROUP: '重试次数须为 0 至 100 的整数。',
    INVALID_EDITOR_RULE: '规则条目格式无效。',
    INVALID_EDITOR_ID: '条目标识已失效，请重新读取配置。',
    INVALID_GROUP_NAME: '节点组名称为空或包含无效字符。',
    DUPLICATE_GROUP_NAME: '节点组名称重复。',
    UNKNOWN_PROXY_PROVIDER: '找不到节点组使用的订阅提供商。',
    INVALID_TEST_INTERVAL: '测速间隔应在 30 至 86400 秒之间。',
    CLASH_YAML_EDITING_UNSUPPORTED: '本版本仅支持通过 LovestSub 编辑器维护配置，请先在“配置来源”切换到 LovestSub。',
    INVALID_URL: '请输入不含用户名、密码或片段的 HTTP 或 HTTPS 链接。',
    INVALID_RULE_KIND: '请选择有效的规则类型。',
    INVALID_RULE_PRIORITY: '规则优先级应为整数。',
    INVALID_RULE_PROVIDER_NAME: '规则集名称为空或包含无效字符。',
    DUPLICATE_RULE_PROVIDER: '规则集名称重复，请使用不同名称。',
    INVALID_RULE_PROVIDER_URL: '规则集链接无效，请填写 HTTP 或 HTTPS 链接。',
    INVALID_RULE_BEHAVIOR: '请选择有效的规则行为。',
    INVALID_RULE_FORMAT: '请选择与规则行为匹配的格式。',
    INVALID_RULE_PROVIDER_PROXY: '规则集下载代理的名称无效。',
    INVALID_POLICY_INTERVAL: '请填写不超过 604800 的整数。',
    UNSUPPORTED_RULE_PROVIDER_TYPE: '文件和内联规则集仅支持重命名。',
    INVALID_RULE_LINE: '规则格式无效，请检查类型、匹配内容和目标。',
    UNKNOWN_RULE_TARGET: '找不到此行指定的目标代理组。请创建该组或改用已有目标。',
    UNKNOWN_RULE_PROVIDER: '找不到此行引用的规则集。',
    INVALID_PRE_PROXY: '前置代理组不存在或引用了自身。',
    INVALID_RESTART_HOUR: '定时重启的小时应为 0 至 23 的整数。',
    INVALID_SS_CONFIG: 'Shadowsocks 加密方式或密码无效，请检查填写内容。',
    RESERVED_PROVIDER_NAME: '提供商名称与系统生成的名称冲突。',
    MIHOMO_POLICY_INVALID: 'Mihomo 未接受此配置。',
    SYSTEM_OPERATION_FAILED: '暂时无法运行配置校验，请检查网关核心状态后重试。',
  };
  function knownEditorError(data) {
    const mapped = Object.hasOwn(editorErrors, data?.error) ? editorErrors[data.error] : '';
    if (!mapped) return '';
    const sections = {subs:'订阅提供商', 'node-groups':'节点组', rules:'规则'};
    const issue = data.issue;
    const section = Object.hasOwn(sections, issue?.section) ? sections[issue.section] : '';
    const location = section && Number.isSafeInteger(issue.index) && issue.index >= 0
      ? `${section}第 ${issue.index + 1} 项${Number.isSafeInteger(issue.line) && issue.line >= 0 ? `第 ${issue.line + 1} 行` : ''}：`
      : '';
    return location + mapped + (data.error === 'MIHOMO_POLICY_INVALID' && typeof data.detail === 'string' ? ' ' + data.detail : '');
  }
  function localizedMessage(value) {
    return typeof value === 'string' && /[\u3400-\u9fff]/.test(value) ? value : '';
  }

  function secret() { return window.ppgwAuth?.active() ? 'session' : ''; }
  async function api(path, body, signal) {
    const token = secret();
    if (!token) { window.ppgwAuth?.expired(); throw Object.assign(Error(loginRequired), {status:401}); }
    const session = window.ppgwAuth?.identity();
    const response = await fetch('/sniffbox/subscription/' + path, {
      method: body ? 'POST' : 'GET',
      headers: { ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
      signal: AbortSignal.any([AbortSignal.timeout(15000), ...(signal ? [signal] : [])]),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const staleSession = response.status === 401 && window.ppgwAuth?.active() && session !== window.ppgwAuth?.identity();
      if (staleSession && body === undefined) return api(path, body, signal);
      if (response.status === 401 && !staleSession) window.ppgwAuth?.expired();
      const error = Error(staleSession ? '登录状态已更新，请重新预览后再提交。' : response.status === 401 ? loginRequired :
        knownEditorError(data) || localizedMessage(data.message) || localizedMessage(data.error) || '请求未完成，请核对配置后重试。');
      error.issue = data.issue;
      error.status = response.status;
      error.staleSession = staleSession;
      throw error;
    }
    return data;
  }

  function mount(editor) {
    const state = { ...editor, available: false, writeAllowed: false, revision: '',
      settingsRevision: '', nodeChoices: [], active: false, stopped: false, timer: null, providerTimer: null, locked: [],
      loaded: false, loading: false, settingsBusy: false, baseline: null };
    state.needsReload = false;
    const lifetime = new AbortController();
    state.authPaused = false;
    const request = async (path, body) => {
      if (state.authPaused) throw Object.assign(Error(loginRequired), {status:401});
      try { return await api(path, body, lifetime.signal); }
      catch (error) { if (error.status === 401 && !error.staleSession) { state.authPaused = true; clearTimeout(state.providerTimer); } throw error; }
    };
    current = state;
    const apply = document.createElement('button');
    apply.type = 'button';
    apply.className = 'btn btn-brand';
    apply.textContent = '保存并应用';
    apply.dataset.ppsubApply = '';
    apply.disabled = true;
    const dirtyState=document.createElement('span');dirtyState.className='editor-dirty-state';dirtyState.dataset.editorDirty='';
    editor.setLoading?.(true,false);
    const notice = document.createElement('div');
    notice.className = 'ppgw-editor-status';
    notice.setAttribute('role', 'status');
    notice.style.whiteSpace = 'pre-wrap';
    notice.dataset.ppsubStatus = '';
    const text = document.createElement('span');
    const view = document.createElement('button');
    view.type = 'button';
    view.className = 'btn';
    view.textContent = '查看代理';
    function showProxyLink(visible) {
      view.hidden=!visible;
      view.style.display=visible?'inline-flex':'none';
    }
    showProxyLink(false);
    view.onclick = () => editor.navigate('#/proxies');
    const explanation = document.createElement('details');
    explanation.className = 'ppgw-read-note';
    explanation.hidden = true;
    const summary = document.createElement('summary');
    summary.textContent = '配置说明';
    const explanationText = document.createElement('p');
    explanation.append(summary, explanationText);
    notice.append(text, view, explanation);
    const refreshNodes = document.createElement('button');
    refreshNodes.type='button'; refreshNodes.className='btn'; refreshNodes.textContent='更新全部订阅';
    refreshNodes.dataset.refreshNodes=''; refreshNodes.disabled=true;
    const cacheNote=document.createElement('p'); cacheNote.className='ppgw-node-cache-note'; cacheNote.dataset.nodeCache=''; cacheNote.hidden=true;
    let providerStates = [];
    function showCache(data) {
      const rows=data?.subscriptions||[];
      providerStates = rows;
      cacheNote.hidden=!rows.length;
      cacheNote.textContent=rows.map(row=>row.nodes?`${row.name}：已保存 ${row.nodes} 个节点${row.update_failed?'（获取失败，沿用已保存节点）':''}`:`${row.name}：尚无节点，请手动更新`).join('；');
      providerControls();
    }
    async function pollProviders() {
      clearTimeout(state.providerTimer);
      if (state.stopped || state.authPaused || state.providerPolling) return;
      state.providerPolling = true;
      try {
        if (!state.active && !state.loading) {
          const data = await request('providers');
          if (!state.stopped) showCache(data);
        }
      } catch { /* Keep saved provider evidence and every unsaved editor field. */ }
      finally { state.providerPolling = false; if (!state.stopped && !state.authPaused) state.providerTimer = setTimeout(pollProviders, 5000); }
    }
    async function refreshProviders(names) {
      if(state.loading||state.active||state.settingsBusy||!state.loaded||!state.writeAllowed)return;
      if(hasChanges()||window.ppgwGateway?.editorOptionsChanged()){show('请先保存或放弃当前编辑，再更新订阅节点。',true);return;}
      try{
        state.active=true;syncEditing();updateApply();show('正在手动获取订阅节点；更新失败时保留原节点…');
        await request('apply',{kind:'refresh_nodes',base_revision:state.revision,settings_revision:state.settingsRevision,
          ...(names?{providers:names}:{}),operation_id:'refresh_'+Date.now()+'_'+Math.random().toString(36).slice(2)});
        await refresh();
      }catch(error){state.active=false;syncEditing();updateApply();show(error.message,true);}
    }
    refreshNodes.onclick=()=>refreshProviders();
    refreshNodes.title='立即更新全部订阅，包括仅手动更新的机场';
    const commitbar=document.createElement('div');commitbar.className='editor-commitbar glass';commitbar.hidden=true;commitbar.append(dirtyState,apply);
    const feedback=document.createElement('div');feedback.className='layout-feedback editor-feedback';feedback.append(notice,cacheNote);
    const importStatus=editor.header.parentElement.querySelector('[data-import-status]');if(importStatus)feedback.append(importStatus);
    editor.header.append(refreshNodes);editor.header.after(feedback);
    editor.header.parentElement.insertBefore(commitbar,editor.header.parentElement.querySelector('.editor-body'));
    const optionsHost = document.createElement('div');
    optionsHost.className = 'ppgw-global-settings';
    const optionsSection=document.createElement('details');optionsSection.className='editor-schedule';
    const optionsTitle=document.createElement('summary');optionsTitle.textContent='订阅更新计划';
    optionsSection.append(optionsTitle,optionsHost);
    (editor.header.parentElement.querySelector('.monitor')||editor.header.parentElement.querySelector('.editor-body'))?.append(optionsSection);
    const detachOptions = window.ppgwGateway?.attachOptions(optionsHost);
    state.setSettingsBusy = busy => { state.settingsBusy = busy; syncEditing(); updateApply(); };
    function show(message, failed = false, loadedNotice = false) {
      if (state.stopped) return;
      const detailed = loadedNotice && state.writeAllowed && String(message || '').length > 64;
      text.textContent = detailed ? '已读取当前配置，修改后保存应用。' : message;
      explanation.hidden = !detailed;
      explanation.open = false;
      explanationText.textContent = detailed ? message : '';
      notice.dataset.state = failed ? 'error' : state.loading || state.active ? 'working' : 'ready';
      text.style.color = failed ? 'var(--err-text)' : 'var(--text-2)';
      notice.querySelector('[data-reauth]')?.remove();
      if (message === loginRequired) {
        const login = document.createElement('button');login.type='button';login.className='btn';login.dataset.reauth='';login.textContent='重新登录并保留编辑';
        login.onclick=()=>window.ppgwAuth?.requestLogin();notice.append(login);
      }
    }

    function showEditorError(error) {
      show(error.message, true);
      if (error.issue) editor.showIssue?.(error.issue, error.message);
    }
    function showOperation(operation, prefix = '') {
      const failed = ['failed', 'interrupted'].includes(operation.status);
      const message = failed && knownEditorError(operation) || operation.message;
      show(prefix + message, failed);
      if (failed && operation.issue) editor.showIssue?.(operation.issue, message);
    }

    function lockEditing(busy) {
      if (busy) {
        const unlocked = Array.from(editor.header.parentElement.querySelectorAll('input,select,textarea,button'))
          .filter(element => element !== apply && !element.hasAttribute('data-reauth') && !element.disabled);
        unlocked.forEach(element => { element.disabled = true; });
        state.locked.push(...unlocked);
      } else {
        state.locked.forEach(element => { element.disabled = false; });
        state.locked = [];
      }
    }

    function providerControls() {
      const rows=editor.providers?.()||[];
      editor.header.parentElement.querySelectorAll('.subs .item').forEach((item,index)=>{
        const row=rows[index];
        if(!row)return;
        let controls=item.querySelector('[data-provider-plan]');
        if(!controls){
          controls=document.createElement('div');controls.className='ppgw-provider-plan';controls.dataset.providerPlan='';
          const mode=document.createElement('select');mode.className='input';mode.dataset.providerMode='';
          for(const [value,label] of [['inherit','跟随全局计划'],['manual','仅手动更新'],['auto','自动更新']]){
            const option=document.createElement('option');option.value=value;option.textContent=label;mode.append(option);
          }
          const interval=document.createElement('input');interval.className='input';interval.dataset.providerInterval='';interval.placeholder='如 6h、1d';
          const update=document.createElement('button');update.type='button';update.className='btn';update.textContent='更新此订阅';update.dataset.providerRefresh='';
          const status=document.createElement('p');status.className='ppgw-provider-status';status.dataset.providerStatus='';status.setAttribute('aria-live','polite');
          controls.append(mode,interval,update,status);item.append(controls);
          mode.onchange=()=>{
            const row=editor.providers()[Array.from(item.parentElement.querySelectorAll(':scope > .item')).indexOf(item)];
            row.update_mode=mode.value;row.update_interval ||= '1d';providerControls();
          };
          interval.oninput=()=>{
            const row=editor.providers()[Array.from(item.parentElement.querySelectorAll(':scope > .item')).indexOf(item)];
            row.update_interval=interval.value;
          };
          update.onclick=()=>refreshProviders([item.querySelector('.row input').value]);
        }
        const saved=providerStates.find(value=>value.name===row.name);
        const when=value=>value?new Date(value*1000).toLocaleString():'尚无记录';
        const results={updated:'已更新',unchanged:'内容未变，无需重新加载',retained:'获取失败，沿用旧节点',unavailable:'尚无可用节点',failed:'更新失败，保留原配置',saved:'已保存节点'};
        const next=saved?.next_update_at?when(saved.next_update_at):saved?.update_mode==='manual'?'仅手动':saved?.update_mode==='inherit'?'全局自动更新未启用或计划尚待确认':'计划尚待确认';
        const label=saved?`${saved.nodes} 个节点 · ${results[saved.result]||'尚无结果'}\n最近尝试：${when(saved.attempted_at)} · 最近成功：${when(saved.succeeded_at)}\n下次更新：${next}${saved.error_category?' · '+saved.error_category:''}${saved.node_delta!=null?' · 节点数变化 '+(saved.node_delta>0?'+':'')+saved.node_delta:''}`:'尚无已保存的更新记录';
        const status=controls.querySelector('[data-provider-status]');
        if(status.textContent!==label)status.textContent=label;
        const mode=controls.querySelector('select'),interval=controls.querySelector('input'),update=controls.querySelector('button');
        mode.setAttribute('aria-label',row.name+' 更新方式');interval.setAttribute('aria-label',row.name+' 自动更新间隔');
        update.setAttribute('aria-label','更新此订阅：'+row.name);
        if(mode.value!==(row.update_mode||'inherit'))mode.value=row.update_mode||'inherit';
        if(document.activeElement!==interval)interval.value=row.update_interval||'1d';
        interval.hidden=mode.value!=='auto';
        const busy=state.loading||state.active||state.settingsBusy||!state.loaded||!state.writeAllowed;
        mode.disabled=busy;interval.disabled=busy;update.disabled=busy;
      });
    }

    editor.header.parentElement.addEventListener('ppgw:editor-render',syncEditing,{signal:lifetime.signal});
    editor.header.parentElement.addEventListener('input',event=>{
      if(event.target.matches('.subs [data-field="name"]'))providerControls();
    },{signal:lifetime.signal});
    function syncEditing() {
      lockEditing(state.loading || state.active || state.settingsBusy || (state.loaded && !state.writeAllowed));
      providerControls();
      window.ppgwGateway?.setEditorBusy(state.loading||state.active);
    }

    function fingerprint() {
      const config = editor.exportConfig();
      delete config.exported_at;
      return JSON.stringify(config);
    }

    function hasChanges() { return state.loaded && fingerprint() !== state.baseline; }

    for(const type of ['input','change','click'])editor.header.parentElement.addEventListener(type,()=>updateApply(),{signal:lifetime.signal});

    function updateApply() {
      editor.setLoading?.(state.loading,state.loaded);
      const changed=hasChanges()||window.ppgwGateway?.editorOptionsChanged();
      const dirtyText=state.loaded?(changed?'有未保存的修改':'没有未保存的修改'):'';
      commitbar.hidden=!state.loaded;
      if(dirtyState.textContent!==dirtyText)dirtyState.textContent=dirtyText;
      refreshNodes.disabled=state.stopped||state.loading||state.active||state.settingsBusy||!state.loaded||!state.writeAllowed;
      apply.disabled = state.stopped || state.loading || state.active || state.settingsBusy || !state.loaded || !state.writeAllowed || !changed;
    }

    async function refresh() {
      clearTimeout(state.timer);
      try {
        const data = await request('status');
        if (state.stopped) return;
        state.revision ||= data.base_revision;
        const operation = data.operation;
        showCache(data.node_cache);
        state.active = operation?.status === 'applying';
        state.writeAllowed = data.ppsub_write_allowed !== false;
        updateApply();
        if (operation) {
          const stages={fetching:'获取订阅',validating:'校验配置',saving:'保存配置',applying:'应用配置',verifying:'验证运行',rolling_back:'恢复原配置'};
          showOperation(operation, stages[operation.stage] ? stages[operation.stage]+'…\n' : '');
          const applied=['applied','unchanged'].includes(operation.status);
          showProxyLink(false);
          if (operation.status !== 'applying') {
            state.revision = data.base_revision;
            syncEditing();
            if (applied || !state.loaded) {
              const message = operation.message;
              if (await state.loadCurrent(editor.importConfig, false) && state.writeAllowed) show(message, operation.status === 'failed');
              if(!state.stopped)showProxyLink(applied);
              if (operation.status !== 'failed') window.ppgwGateway?.editorApplied();
            }
          }
        }
        if (state.active) state.timer = setTimeout(refresh, 1500);
      } catch (error) {
        if (state.active && error.status !== 401) {
          show('正在重启网关并加载 LovestSub 配置…');
          if (!state.stopped) state.timer = setTimeout(refresh, 1500);
        } else { show(error.message, true); }
      }
    }

    state.loadCurrent = async (importConfig, confirmChanges = true) => {
      if (state.loading || state.active || state.stopped) return false;
      if (confirmChanges && (hasChanges() || window.ppgwGateway?.editorOptionsChanged()) && !await editor.confirmAction('重新读取会放弃未保存的编辑内容和更新计划，继续吗？',{title:'重新读取配置',accept:'放弃并重新读取'})) return false;
      if (state.loading || state.active || state.stopped) return false;
      if(confirmChanges) window.ppgwGateway?.discardEditorOptions();
      state.loading = true;
      updateApply();
      lockEditing(true);
      show('正在读取本机当前配置…');
      try {
        const data = await request('config');
        if (state.stopped) return false;
        state.revision = data.base_revision;
        state.writeAllowed = data.write_allowed !== false;
        state.settingsRevision=data.settings_revision || '';
        await window.ppgwGateway?.syncEditor(data);
        if (state.stopped) return false;
        state.nodeChoices = data.node_choices || [];
        importConfig(data.config);
        showCache(data.node_cache);
        state.loaded = true;
        providerControls();
        state.needsReload = false;
        state.baseline = fingerprint();
        showProxyLink(false);
        show(data.notice, false, true);
        return true;
      } catch (error) {
        state.loaded = false;
        show(error.status === 401 ? error.message : '读取当前配置失败，请点击“重新读取”后再保存。', true);
        return false;
      } finally {
        state.loading = false;
        if (!state.stopped) { syncEditing(); updateApply(); }
      }
    };

    apply.onclick = async () => {
      if (state.loading || state.active || state.settingsBusy || !state.loaded || !state.writeAllowed || !(hasChanges()||window.ppgwGateway?.editorOptionsChanged())) return;
      try {
        const config=editor.exportConfig();
        if (!config.subs?.length||config.subs.some(item=>!item.name?.trim()||!item.url?.trim())) throw Error('请先填写订阅提供商的名称和订阅链接。');
        if(config.subs.some(row=>row.update_mode==='auto'&&!/^[1-9][0-9]{0,3}[mhd]$/.test(row.update_interval||'')))throw Error('机场自动更新间隔请填写 30m、6h、1d 等有效值。');
        const settingsPatch=window.ppgwGateway?.editorPatch(state.settingsRevision)||{};
        const body={kind:'editor',edit_kind:'ppsub',value:JSON.stringify(config),settings_patch:settingsPatch,
          settings_revision:state.settingsRevision,base_revision:state.revision,
          operation_id:'editor_'+Date.now()+'_'+Math.random().toString(36).slice(2)};
        state.loading=true;syncEditing();updateApply();show('正在预览统一保存的变更…');
        const previewFingerprint=fingerprint();
        const preview=await request('apply',{...body,dry_run:true});
        state.loading=false;syncEditing();updateApply();
        if(preview.action==='none'&&!Object.keys(preview.changes||{}).length&&!preview.editor_changed){show('配置没有变化，无需应用。');return;}
        window.ppgwGateway.previewEditor(preview,async()=>{
          if(state.stopped||state.active)return;
          if(previewFingerprint!==fingerprint()||JSON.stringify(body.settings_patch)!==JSON.stringify(window.ppgwGateway?.editorPatch(state.settingsRevision)||{}))throw Error('编辑内容已变化，请重新预览后再确认应用。');
          try {
            state.active=true;syncEditing();updateApply();showProxyLink(false);
            show('正在统一保存并验证配置，网关可能短暂重启…');
            await request('apply',body);
            await refresh();
          }catch(error){state.active=false;syncEditing();updateApply();showEditorError(error);}
        });
        show('请在预览中确认保存；当前修改尚未生效。');
      }catch(error){state.loading=false;state.active=false;syncEditing();updateApply();showEditorError(error);}
    };

    state.resumeAfterLogin = async () => {
      if (state.stopped) return;
      state.authPaused = false;
      if (state.active) await refresh();
      else if (!state.loaded) await state.loadCurrent(editor.importConfig, false);
      else if (text.textContent === loginRequired) show('已重新登录，未保存的修改仍保留。');
      if (!state.stopped && !state.authPaused) { syncEditing(); updateApply(); pollProviders(); }
    };

    lockEditing(true);
    state.providerTimer = setTimeout(pollProviders, 5000);
    show('正在读取本机当前配置…');
    request('status').then(async data => {
      if (state.stopped) return;
      state.available = true;
      state.revision = data.base_revision;
      state.writeAllowed = data.ppsub_write_allowed !== false;
      if (data.operation?.status === 'applying') { state.active = true; await refresh(); }
      else { await state.loadCurrent(editor.importConfig, false); if(data.operation&&state.writeAllowed)showOperation(data.operation); }
    }).catch(error => {
      if (state.stopped) return;
      // Keep the explicit read button available so a temporary failure is retryable.
      state.available = true;
      syncEditing();
      updateApply();
      show(error.status === 401 ? error.message : '读取当前配置失败，请点击“重新读取”后再保存。', true);
    });

    function destroy() {
      state.stopped = true;
      lifetime.abort();
      clearTimeout(state.timer);
      clearTimeout(state.providerTimer);
      syncEditing();
      window.ppgwGateway?.setEditorBusy(false);
      editor.header.parentElement.querySelectorAll('[data-provider-plan]').forEach(element=>element.remove()); refreshNodes.remove();
      detachOptions?.(); optionsHost.remove();
      dirtyState.remove();apply.remove();commitbar.remove();feedback.remove();
      notice.remove();
      if (current === state) current = null;
    }
    return destroy;
  }

  window.ppgwPpsub = {
    mount,
    isAvailable: () => Boolean(current?.available),
    nodeChoices: () => (current?.nodeChoices || []).map(name => ({ name, type: 'group' })),
    loadCurrent: importConfig => current?.loadCurrent(importConfig),
    hasChanges: () => Boolean(current?.loaded && (() => {
      const value = current.exportConfig(); delete value.exported_at;
      return JSON.stringify(value) !== current.baseline;
    })()),
    isBusy: () => Boolean(current?.active),
    resumeAfterLogin: () => current?.resumeAfterLogin(),
    setSettingsBusy: busy => current?.setSettingsBusy?.(busy),
    invalidate: () => { if (current) current.needsReload = true; },
    refreshIfNeeded: () => {
      if (current?.needsReload && !current.loading && !current.active) current.loadCurrent(current.importConfig, false);
    },
  };
})();
