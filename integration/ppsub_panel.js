/* Debian persistence for the existing PPsub editor; no separate form or login. */
(() => {
  let current = null;

  function secret() {
    try {
      const saved = JSON.parse(localStorage.getItem('PaoPaoGateWay') || '{}');
      return saved.clashAPIConfigs?.[0]?.secret || saved.secret || '';
    } catch { return ''; }
  }

  async function api(path, body, signal) {
    const token = secret();
    if (!token) throw Error('登录已失效，请刷新面板后重试。');
    const response = await fetch('/sniffbox/subscription/' + path, {
      method: body ? 'POST' : 'GET',
      headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
      signal: AbortSignal.any([AbortSignal.timeout(15000), ...(signal ? [signal] : [])]),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = Error(response.status === 401 ? '登录已失效，请刷新面板后重试。' :
        ({CONFIG_REVISION_CONFLICT:'配置已变化，请重新读取后再保存。',SOURCE_CHANGE_REQUIRES_SWITCH:'请通过上方“切换配置来源”完成来源更换。',MANAGEMENT_BUSY:'其他管理操作正在进行，请稍后重试。',CONFIG_CHANGED:'运行配置或快照已变化，请重新读取后再保存。'}[data.error] || data.error) || '暂时无法连接网关，请稍后重试。');
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function mount(editor) {
    const state = { ...editor, available: false, writeAllowed: false, revision: '', editKind: 'ppsub',
      snapshotId: null, settingsRevision: '', syntheticSource:false, nodeChoices: [], active: false, stopped: false, timer: null, providerTimer: null, locked: [],
      loaded: false, loading: false, settingsBusy: false, editingLocked: false, baseline: null };
    state.needsReload = false;
    const lifetime = new AbortController();
    const request = (path, body) => api(path, body, lifetime.signal);
    current = state;
    const apply = document.createElement('button');
    apply.type = 'button';
    apply.className = 'btn btn-brand';
    apply.textContent = '保存并应用';
    apply.dataset.ppsubApply = '';
    apply.disabled = true;
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
    view.hidden = true;
    view.style.display = 'none';
    view.onclick = () => { location.hash = '#/proxies'; location.reload(); };
    const explanation = document.createElement('details');
    explanation.className = 'ppgw-read-note';
    explanation.hidden = true;
    const summary = document.createElement('summary');
    summary.textContent = '配置说明';
    const explanationText = document.createElement('p');
    explanation.append(summary, explanationText);
    notice.append(text, view, explanation);
    const backup = document.createElement('button');
    backup.type='button'; backup.className='btn'; backup.textContent='备份运行 YAML'; backup.hidden=true;
    backup.dataset.fullBackup='';
    backup.onclick=async()=>{
      try {
        const data=await request('apply',{kind:'export_yaml',snapshot_id:state.snapshotId,base_revision:state.revision});
        const url=URL.createObjectURL(new Blob([data.content],{type:'application/yaml'}));
        const link=document.createElement('a');link.href=url;link.download=data.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch(error){show(error.message,true);}
    };
    const exportNote=document.createElement('p');exportNote.className='ppgw-export-note';
    exportNote.textContent='“导出编辑内容”仅保存 PPsub 编辑数据；完整配置请使用“备份运行 YAML”。备份包含当前运行配置与凭据，不包含尚未保存的修改。';
    exportNote.hidden=true;
    const refreshNodes = document.createElement('button');
    refreshNodes.type='button'; refreshNodes.className='btn'; refreshNodes.textContent='更新全部订阅';
    refreshNodes.dataset.refreshNodes=''; refreshNodes.disabled=true;
    const cacheNote=document.createElement('p'); cacheNote.className='ppgw-export-note'; cacheNote.dataset.nodeCache=''; cacheNote.hidden=true;
    let providerStates = [];
    function showCache(data) {
      const rows=data?.subscriptions||[];
      providerStates = rows;
      cacheNote.hidden=!rows.length;
      cacheNote.textContent=rows.map(row=>row.nodes?`${row.name}：已保存 ${row.nodes} 个节点${row.update_failed?'（获取失败，沿用已保存节点）':''}`:`${row.name}：尚无节点，请手动更新`).join('；');
      providerControls();
    }
    async function pollProviders() {
      try {
        if (!state.active && !state.loading) {
          const data = await request('providers');
          if (!state.stopped) showCache(data);
        }
      } catch { /* Keep saved provider evidence and every unsaved editor field. */ }
      finally { if (!state.stopped) state.providerTimer = setTimeout(pollProviders, 5000); }
    }
    async function refreshProviders(names) {
      if(state.loading||state.active||state.settingsBusy||!state.loaded)return;
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
    editor.header.append(backup,refreshNodes,apply);
    editor.header.after(cacheNote);
    editor.header.after(notice,exportNote);
    const optionsHost = document.createElement('div');
    optionsHost.className = 'ppgw-global-settings';
    const optionsSection=document.createElement('details');optionsSection.className='editor-schedule';
    const optionsTitle=document.createElement('summary');optionsTitle.textContent='订阅更新计划';
    optionsSection.append(optionsTitle,optionsHost);
    editor.header.parentElement.querySelector('.monitor')?.append(optionsSection);
    const detachOptions = window.ppgwGateway?.attachOptions(optionsHost);
    state.setSettingsBusy = busy => { state.settingsBusy = busy; syncEditing(); updateApply(); };
    function show(message, failed = false, loadedNotice = false) {
      if (state.stopped) return;
      const detailed = loadedNotice && String(message || '').length > 64;
      text.textContent = detailed ? '已读取当前配置，修改后保存应用。' : message;
      explanation.hidden = !detailed;
      explanation.open = false;
      explanationText.textContent = detailed ? message : '';
      notice.dataset.state = failed ? 'error' : state.loading || state.active ? 'working' : 'ready';
      text.style.color = failed ? 'var(--err-text)' : 'var(--text-2)';
    }

    function lockEditing(busy) {
      if (busy) {
        state.editingLocked = true;
        const unlocked = Array.from(editor.header.parentElement.querySelectorAll('input,select,textarea,button'))
          .filter(element => element !== apply && !element.disabled);
        unlocked.forEach(element => { element.disabled = true; });
        state.locked.push(...unlocked);
      } else {
        state.locked.forEach(element => { element.disabled = false; });
        state.locked = [];
        state.editingLocked = false;
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
        controls.hidden=state.editKind!=='ppsub';
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

    function restrictCapabilities() {
      providerControls();
      const full=state.editKind==='clash_patch';
      const root=editor.header.parentElement;
      root.dataset.editKind=state.editKind;
      root.dataset.syntheticSource=String(state.syntheticSource);
      backup.hidden=!full; backup.disabled=state.loading||state.active||state.settingsBusy||!state.loaded;
      exportNote.hidden=!full;
      const menus=editor.header.querySelectorAll('.menuwrap');
      const exportButton=menus[0]?.querySelector('button');
      if(exportButton){exportButton.dataset.nativeLabel ||= exportButton.textContent;const label=full?'导出编辑内容':exportButton.dataset.nativeLabel;if(exportButton.textContent!==label)exportButton.textContent=label;}
      const importButton=menus[1]?.querySelector('button');
      if(importButton){menus[1].hidden=full;importButton.disabled=full||state.loading||state.active||state.settingsBusy;importButton.title=full?'更换 PPsub 来源请使用上方“切换配置来源”。':'';}
      const providerTitle=root.querySelector('.subs .editor-section-heading > h3, .subs > h3');
      if(providerTitle){providerTitle.dataset.nativeLabel ||= providerTitle.textContent;const label=full?'配置内节点提供商':providerTitle.dataset.nativeLabel;if(providerTitle.textContent!==label)providerTitle.textContent=label;}
      root.querySelectorAll('.rules .rd').forEach(label=>{ const radio=label.querySelector('input'); label.hidden=full&&(radio?.value==='url'||/URL\s*(下载|Download)/i.test(label.textContent)); });
      root.querySelectorAll('.subs .item').forEach(item=>{
        const name=item.querySelector('.row input'); if(name) name.readOnly=full;
      });
    }
    const capabilityObserver=new MutationObserver(restrictCapabilities);
    capabilityObserver.observe(editor.header.parentElement,{childList:true,subtree:true,characterData:true});
    editor.header.parentElement.addEventListener('contextmenu',event=>{if(state.editKind==='clash_patch'&&event.target.closest('.subs')){event.preventDefault();event.stopPropagation();}},{capture:true,signal:lifetime.signal});
    function syncEditing() { lockEditing(state.loading || state.active || state.settingsBusy || (state.loaded && !state.writeAllowed)); restrictCapabilities(); window.ppgwGateway?.setEditorBusy(state.loading||state.active); }

    function fingerprint() {
      const config = editor.exportConfig();
      delete config.exported_at;
      return JSON.stringify(config);
    }

    function hasChanges() { return state.loaded && fingerprint() !== state.baseline; }

    function updateApply() {
      refreshNodes.hidden=state.editKind!=='ppsub';
      refreshNodes.disabled=state.stopped||state.loading||state.active||state.settingsBusy||!state.loaded||!state.writeAllowed;
      apply.disabled = state.stopped || state.loading || state.active || state.settingsBusy || !state.loaded || !state.writeAllowed ||
        (state.editKind === 'clash_patch' && !state.snapshotId);
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
        state.editKind = data.edit_kind || 'ppsub';
        updateApply();
        if (operation) {
          const stages={fetching:'获取订阅',validating:'校验配置',saving:'保存配置',applying:'应用配置',verifying:'验证运行',rolling_back:'恢复原配置'};
          show((stages[operation.stage]?stages[operation.stage]+'…\n':'')+operation.message, ['failed','interrupted'].includes(operation.status));
          view.hidden = !['applied', 'unchanged'].includes(operation.status);
          view.style.display = view.hidden ? 'none' : 'inline-flex';
          if (operation.status !== 'applying') {
            state.revision = data.base_revision;
            syncEditing();
            if (['applied', 'unchanged'].includes(operation.status) || !state.loaded) {
              const message = operation.message;
              if (await state.loadCurrent(editor.importConfig, false)) show(message, operation.status === 'failed');
              if (operation.status !== 'failed') window.ppgwGateway?.editorApplied();
            }
          }
        }
        if (state.active) state.timer = setTimeout(refresh, 1500);
      } catch (error) {
        if (state.active && error.status !== 401) {
          show('正在重启网关并加载 PPsub 配置…');
          if (!state.stopped) state.timer = setTimeout(refresh, 1500);
        } else { show(error.message, true); }
      }
    }

    state.loadCurrent = async (importConfig, confirmChanges = true) => {
      if (state.loading || state.active || state.stopped) return false;
      if (confirmChanges && (hasChanges() || window.ppgwGateway?.editorOptionsChanged()) && !confirm('重新读取会放弃未保存的编辑内容和更新计划，继续吗？')) return false;
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
        state.editKind = data.edit_kind || 'ppsub';
        state.snapshotId = data.snapshot_id || null;
        state.settingsRevision=data.settings_revision || '';
        await window.ppgwGateway?.syncEditor(data);
        if (state.stopped) return false;
        state.syntheticSource=Boolean(data.synthetic_source);
        state.nodeChoices = data.node_choices || [];
        importConfig(data.config);
        showCache(data.node_cache);
        state.loaded = true;
        providerControls();
        state.needsReload = false;
        state.baseline = fingerprint();
        view.hidden = true;
        view.style.display = 'none';
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
      if (state.loading || state.active || state.settingsBusy || !state.loaded || !state.writeAllowed || (state.editKind === 'clash_patch' && !state.snapshotId)) return;
      try {
        const config=editor.exportConfig();
        if (state.editKind!=='clash_patch'&&(!config.subs?.length||config.subs.some(item=>!item.name?.trim()||!item.url?.trim()))) throw Error('请先填写订阅提供商的名称和订阅链接。');
        if(state.editKind==='ppsub'&&config.subs?.some(row=>row.update_mode==='auto'&&!/^[1-9][0-9]{0,3}[mhd]$/.test(row.update_interval||'')))throw Error('机场自动更新间隔请填写 30m、6h、1d 等有效值。');
        const settingsPatch=window.ppgwGateway?.editorPatch(state.settingsRevision)||{};
        const body={kind:'editor',edit_kind:state.editKind,value:JSON.stringify(config),settings_patch:settingsPatch,
          settings_revision:state.settingsRevision,base_revision:state.revision,
          ...(state.editKind==='clash_patch'?{snapshot_id:state.snapshotId}:{}),
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
            state.active=true;syncEditing();updateApply();view.hidden=true;view.style.display='none';
            show('正在统一保存并验证配置，网关可能短暂重启…');
            await request('apply',body);
            await refresh();
          }catch(error){state.active=false;syncEditing();updateApply();show(error.message,true);}
        });
        show('请在预览中确认保存；当前修改尚未生效。');
      }catch(error){state.loading=false;state.active=false;syncEditing();updateApply();show(error.message,true);}
    };

    lockEditing(true);
    state.providerTimer = setTimeout(pollProviders, 5000);
    show('正在读取本机当前配置…');
    request('status').then(async data => {
      if (state.stopped) return;
      state.available = true;
      state.revision = data.base_revision;
      state.writeAllowed = data.ppsub_write_allowed !== false;
      state.editKind = data.edit_kind || 'ppsub';
      if (data.operation?.status === 'applying') { state.active = true; await refresh(); }
      else { await state.loadCurrent(editor.importConfig, false); if(data.operation)show(data.operation.message,['failed','interrupted'].includes(data.operation.status)); }
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
      capabilityObserver.disconnect(); editor.header.parentElement.querySelectorAll('[data-provider-plan]').forEach(element=>element.remove()); backup.remove(); exportNote.remove(); refreshNodes.remove(); cacheNote.remove();
      detachOptions?.(); optionsHost.remove();
      apply.remove();
      notice.remove();
      if (current === state) current = null;
    }
    destroy.allowNavigation = (url, type) => {
      if (url && url.origin === location.origin && url.pathname === location.pathname && url.hash === '#/ppsub') return true;
      if (state.active) { show('正在保存并应用，请等待任务完成后再离开。'); return false; }
      if (!hasChanges()) return true;
      return type !== 'leave' && confirm('离开会放弃未保存的修改，继续吗？');
    };
    return destroy;
  }

  window.ppgwPpsub = {
    mount,
    isAvailable: () => Boolean(current?.available),
    clashEditing: () => current?.editKind === 'clash_patch',
    nodeChoices: () => (current?.nodeChoices || []).map(name => ({ name, type: 'group' })),
    loadCurrent: importConfig => current?.loadCurrent(importConfig),
    hasChanges: () => Boolean(current?.loaded && (() => {
      const value = current.exportConfig(); delete value.exported_at;
      return JSON.stringify(value) !== current.baseline;
    })()),
    isBusy: () => Boolean(current?.active),
    setSettingsBusy: busy => current?.setSettingsBusy?.(busy),
    invalidate: () => { if (current) current.needsReload = true; },
    refreshIfNeeded: () => {
      if (current?.needsReload && !current.loading && !current.active) current.loadCurrent(current.importConfig, false);
    },
  };
})();
