const {sessionFixture}=require('./browser-session-fixture.cjs');
/* Subscription operation model: isolated browser fixture, no live gateway writes. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),output=path.resolve(process.argv[2]||path.join(root,'dist/subscription-flow-ui'));
const origin='http://127.0.0.1:18886';
function python(code,value){return JSON.parse(execFileSync(process.env.PPGW_PYTHON||'python',['-c',"import sys,json;sys.path.insert(0,'integration');"+code],{cwd:root,input:JSON.stringify(value),encoding:'utf8'}));}
const defaults=python("from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini'].update(mode='suburl',yamlfile='custom.yaml',suburl='https://example.com/main-config');print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))",null);
let document={proxies:[{name:'N1',type:'socks5',server:'127.0.0.1',port:9}],
  'proxy-providers':{Airport:{type:'http',url:'https://example.com/nested-provider',path:'./providers/airport.yaml'}},
  'proxy-groups':[{name:'Main',type:'select',proxies:['N1','DIRECT']}],rules:['MATCH,Main']};
let projected=python('from clash_ppsub import editor_config;print(json.dumps(editor_config(json.load(sys.stdin))[0]))',document);
const native={exported_at:'fixture',subs:[{name:'Native provider',url:'https://example.com/native'}],'node-groups':[{name:'Native group',subs:['all']}],rules:[]};
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:980},reducedMotion:'reduce'}),page=await context.newPage(),errors=[],requests=[];
    page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>localStorage.setItem('PaoPaoGateWay',JSON.stringify({clashAPIConfigs:[{secret:'fixture-token'}]})));
    const model={...defaults,revision:'a'.repeat(64),assets:['custom.yaml','second.yaml'],source:{url:'https://example.com/main-config',native_ppsub:false,local_policy:true},mihomo_installed:true};
    let operation=null,job=null,savedEditor=null,sourceSwitched=0,mismatchSettings=false;
    const sourceDetails={kind:'remote_yaml',url:'https://example.com/main-config',local_policy:{present:true,applies:true,sections:['proxy-groups','rules']}};
    let previewPlan={action:'restart',core_action:'restart',core_active:true,pending_load:false,services:[]};
    const changeSummary={schema:1,changed:true,subscriptions:{counts:{before:1,after:1,added:0,removed:0,modified:1},changed:true,order_changed:false,names:{modified:['Airport']}},
      node_groups:{counts:{before:1,after:2,added:1,removed:0,modified:0},changed:true,order_changed:true,names:{added:['<img src=x onerror=alert(1)>']},names_truncated:true},
      rule_providers:{counts:{before:0,after:0,added:0,removed:0,modified:0},changed:false},rules:{counts:{before:1,after:2,added:1,removed:0,modified:0},changed:true,unit:'lines'},features:{changed:true,modified:1,changed_keys:['global_monitor']}};
    let providerRows=[],providerPolls=0,editorConfigReads=0;
    const kind=()=>model.source.native_ppsub?'ppsub':'clash_patch';
    const sessions=sessionFixture({proof:()=> 'fixture-token'});
    await page.route(origin+'/**',async route=>{
      const request=route.request(),target=new URL(request.url()).pathname,body=request.method()==='POST'?request.postDataJSON():null;
      const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if(target.startsWith('/sniffbox/')){
        if(target==='/sniffbox/settings/config')return reply(mismatchSettings?{...model,revision:'f'.repeat(64)}:model);
        if(target==='/sniffbox/settings/status')return reply({management:{state:'unknown',recent:[]},runtime:{},running_mode:model.settings.ini.mode,source:model.source});
        if(target==='/sniffbox/subscription/providers'){providerPolls++;return reply({subscriptions:providerRows});}
        if(target==='/sniffbox/subscription/status')return reply({ready:true,ppsub_write_allowed:true,edit_kind:kind(),base_revision:model.revision,operation});
        if(target==='/sniffbox/subscription/config'){editorConfigReads++;return reply({config:model.source.native_ppsub?native:projected,write_allowed:true,edit_kind:kind(),base_revision:model.revision,settings_revision:model.revision,source_details:sourceDetails,snapshot_id:'snapshot',node_choices:['N1'],notice:'Fixture source: '+model.settings.ini.mode});}
        if(target==='/sniffbox/settings/preview'){
          requests.push({target,body});
          if(model.source.local_policy&&Object.keys(body.patch.ini||{}).some(key=>['mode','suburl','yamlfile'].includes(key))&&!body.reset_source)return reply({error:'SOURCE_CONFLICT'},409);
          return reply({action:'restart',changes:body.patch,effects:{core:'restart',source_reset:model.source.local_policy,services:[]}});
        }
        if(target==='/sniffbox/settings/apply'){
          requests.push({target,body});job=body;
          assert.equal(body.kind,'config');
          const patch=body.payload.patch.ini;
          Object.assign(model.settings.ini,patch);
          if(patch.suburl)model.source.url=patch.suburl;
          if(Object.keys(patch).some(key=>['mode','suburl','yamlfile'].includes(key))){model.source.local_policy=false;model.source.native_ppsub=!!body.payload.source_ppsub;}
          if(body.payload.source_ppsub)assert.deepEqual(JSON.parse(body.payload.source_ppsub),native);
          model.revision=(BigInt('0x'+model.revision)+1n).toString(16).padStart(64,'0');operation=null;sourceSwitched++;
          return reply({id:body.id,status:'queued'},202);
        }
        if(target.startsWith('/sniffbox/settings/jobs/'))return reply({id:job.id,status:'succeeded',result:{runtime:{status:'running'}}});
        if(target==='/sniffbox/subscription/apply'){
          requests.push({target,body});
          if(body.dry_run)return reply({action:'restart',changes:body.settings_patch,editor_changed:true,save_target:'local_policy',change_summary:changeSummary,application:previewPlan,source_details:{...sourceDetails,url:undefined}});
          savedEditor=body;
          projected=JSON.parse(body.value);model.settings.ini.subtime=body.settings_patch.ini.subtime;model.source.local_policy=true;
          model.revision=(BigInt('0x'+model.revision)+1n).toString(16).padStart(64,'0');operation={id:body.operation_id,status:'applied',message:'Combined fixture saved'};
          return reply(operation,202);
        }
        return reply({mode:model.settings.ini.mode,clash_version:'fixture',ppsub:model.source.native_ppsub});
      }
      try{return route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
    });
    await sessions.install(page);
    await page.goto(origin+'/ui/#/subscriptions');
    const ready=()=>page.waitForFunction(()=>document.querySelector('.source-editor')?.dataset.editorState==='loaded');
    await ready();
    const health=page.locator('.monitor [data-field="enable"]').first();
    const save=page.locator('[data-ppsub-apply]');assert.equal(await save.isDisabled(),true);
    const groupName=page.locator('.groups .item [data-field="name"]').first(),originalName=await groupName.inputValue();
    await groupName.fill(originalName+' edited');assert.equal(await save.isEnabled(),true);
    await groupName.fill(originalName);assert.equal(await save.isDisabled(),true);

    await page.locator('.editor-schedule > summary').click();
    const interval=page.getByLabel('订阅更新间隔',{exact:true});
    const navigation=page.locator('#shell-nav');
    const settings=()=>navigation.locator('a[href="#/settings"]').click();
    const subscriptions=async()=>{await navigation.locator('a[href="#/subscriptions"]').click();await page.getByRole('tab',{name:'策略编辑',exact:true}).click();};
    const reload=async(confirm=false)=>{if(confirm)page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'重新读取',exact:true}).click();await ready();};
    model.revision='b'.repeat(64);model.settings.ini.subtime='8h';model.settings.ini.max_rec=6000;
    await reload();assert.equal(await interval.inputValue(),'8h');
    await health.check();await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});
    assert.equal(requests.at(-1).body.settings_revision,model.revision);
    assert.match(await page.locator('.source-override').first().textContent(),/节点组、规则/);
    assert.match(await page.locator('#preview-content .source-override').textContent(),/此次将使用本地覆盖/);
    await page.getByRole('tab',{name:'配置来源',exact:true}).click();assert.match(await page.locator('.source-card .source-override').textContent(),/已保存与当前来源绑定的本地覆盖/);await page.getByRole('tab',{name:'策略编辑',exact:true}).click();
    assert.match(await page.locator('[data-change-section="subscriptions"]').textContent(),/修改 1.*修改：Airport/s);
    assert.match(await page.locator('[data-change-section="node_groups"]').textContent(),/新增 1.*顺序已调整/s);
    assert.match(await page.locator('[data-change-section="rules"]').textContent(),/1 → 2/);
    assert.equal(await page.locator('#preview-content img').count(),0,'names must render as text');
    assert.doesNotMatch(await page.locator('#preview-content').textContent(),/https:\/\//);
    await page.screenshot({path:path.join(root,'dist/change-preview-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('#preview-dialog').scrollWidth<=document.querySelector('#preview-dialog').clientWidth);
    assert.equal(await page.locator('#preview-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth),true);
    await page.screenshot({path:path.join(root,'dist/change-preview-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:980});
    await page.locator('#cancel-preview').click();await reload(true);
    for(const plan of [{action:'reload',core_active:true,pending_load:false,services:[]},{action:'auxiliary',core_active:true,pending_load:false,services:['api']},{action:'auxiliary',core_active:false,pending_load:false,services:['api']},{action:'auxiliary',core_active:null,pending_load:false,services:['api']},{action:'none',core_active:false,pending_load:true,services:[]}]){
      previewPlan=plan;await health.check();await page.getByRole('button',{name:'保存并应用',exact:true}).click();
      await page.locator('#preview-dialog').waitFor({state:'visible'});
      const text=await page.locator('#preview-content').textContent();
      assert.match(text,plan.pending_load?/仅保存配置/:plan.action==='reload'?/热更新转发核心/:/仅调整辅助服务/);
      if(plan.pending_load)assert.doesNotMatch(text,/将重启网关/);
      if(plan.action==='auxiliary'&&plan.core_active!==true){assert.doesNotMatch(text,/保持运行/);assert.match(text,plan.core_active===false?/此次不会启动/:/不改变转发核心的运行状态/);}
      await page.locator('#cancel-preview').click();await reload(true);
    }
    previewPlan={action:'restart',core_active:true,pending_load:false,services:[]};
    for(const action of ['reload','restart']){
      previewPlan={action,core_active:null,runtime_state:'unknown',pending_load:false,services:[],possible_services:['api'],services_uncertain:true};
      await health.check();await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.locator('#preview-dialog').waitFor({state:'visible'});
      const text=await page.locator('#preview-content').textContent();assert.match(text,/运行状态暂无法确认/);assert.match(text,/具体调整范围以执行时状态为准/);assert.doesNotMatch(text,/将重启网关|将热更新转发核心/);
      await page.locator('#cancel-preview').click();await reload(true);
    }
    previewPlan={action:'restart',core_active:true,pending_load:false,services:[]};
    mismatchSettings=true;
    await page.getByRole('button',{name:'重新读取',exact:true}).click();
    await page.getByText('读取当前配置失败，请点击“重新读取”后再保存。',{exact:true}).waitFor();
    assert.equal(await page.locator('[data-ppsub-apply]').isDisabled(),true);
    mismatchSettings=false;await reload();assert.equal(await interval.inputValue(),'8h');
    await health.check();await interval.fill('9h');await interval.press('Tab');
    await settings();
    assert.equal(await page.locator('#setting-ini-max_rec').isDisabled(),true);
    assert.equal(await page.locator('#setting-ini-max_rec').inputValue(),'6000');
    await page.getByRole('link',{name:'继续编辑订阅与策略',exact:true}).click();await ready();
    assert.equal(await health.isChecked(),true);assert.equal(await interval.inputValue(),'9h');
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});await page.locator('#apply').click();
    await page.getByText('Combined fixture saved',{exact:true}).waitFor();await ready();
    assert.equal(savedEditor.settings_patch.ini.subtime,'9h');
    assert.equal(JSON.parse(savedEditor.value).global_monitor.enable,true);
    await settings();await page.locator('#setting-ini-max_rec').waitFor({state:'visible'});
    await page.waitForFunction(()=>!document.querySelector('#setting-ini-max_rec').disabled);
    await page.locator('#setting-ini-max_rec').fill('7777');await page.locator('#setting-ini-max_rec').press('Tab');
    await subscriptions();
    assert.equal(await page.locator('[data-ppsub-apply]').isDisabled(),true);
    assert.equal(await health.isDisabled(),true);assert.equal(await interval.isDisabled(),true);
    await page.getByRole('link',{name:'继续编辑网关设置',exact:true}).click();
    assert.equal(await page.locator('#setting-ini-max_rec').inputValue(),'7777');
    await page.locator('#preview').click();await page.locator('#preview-dialog').waitFor({state:'visible'});
    assert.equal(requests.at(-1).body.patch.ini.max_rec,7777);
    await page.locator('#apply').click();await page.getByText('配置已应用，运行状态验收通过。',{exact:true}).waitFor();
    await subscriptions();await ready();assert.equal(await health.isDisabled(),false);
    await interval.fill('12h');await interval.press('Tab');await settings();
    assert.equal(await page.locator('#setting-ini-max_rec').isDisabled(),true);
    await subscriptions();await reload(true);await settings();
    assert.equal(await page.locator('#setting-ini-max_rec').isDisabled(),false);
    await subscriptions();await ready();
    await page.getByRole('tab',{name:'配置来源',exact:true}).click();await page.getByRole('button',{name:'切换配置来源',exact:true}).click();await page.locator('.source-switch').getByRole('button',{name:'修改地址',exact:true}).click();await page.locator('#source-url').fill('https://example.com/pending-source');
    await settings();assert.equal(await page.locator('#setting-ini-max_rec').isDisabled(),true);
    await subscriptions();await page.getByRole('tab',{name:'配置来源',exact:true}).click();assert.equal(await page.locator('#source-url').inputValue(),'');await page.locator('.source-switch').getByRole('button',{name:'显示地址',exact:true}).click();assert.equal(await page.locator('#source-url').inputValue(),'https://example.com/pending-source');await page.getByRole('button',{name:'取消切换',exact:true}).click();await settings();
    assert.equal(await page.locator('#setting-ini-max_rec').isDisabled(),false);
    // A browser left open receives another browser/scheduler's provider result,
    // while preserving its unsaved source and provider schedule drafts.
    model.source.native_ppsub=true;model.source.local_policy=false;operation=null;
    await subscriptions();await reload();
    const mode=page.getByLabel('Native provider 更新方式',{exact:true});
    await mode.selectOption('auto');
    const providerInterval=page.getByLabel('Native provider 自动更新间隔',{exact:true});
    await providerInterval.fill('7h');
    const readsBefore=editorConfigReads;
    providerRows=[{name:'Native provider',nodes:13,result:'updated',attempted_at:1700000000,succeeded_at:1700000000,update_mode:'auto',next_update_at:1700020000}];
    await page.locator('[data-provider-status]').filter({hasText:'13 个节点'}).waitFor({timeout:15000});
    assert.equal(await providerInterval.inputValue(),'7h');
    assert.equal(await mode.inputValue(),'auto');
    assert.equal(editorConfigReads,readsBefore,'idle provider polls must not import configuration');
    assert.ok(providerPolls>0);
    assert.deepEqual(errors,[]);
    const result={syntheticOnly:true,reloadSynchronizesRevisionAndValues:true,mixedSnapshotsRejectedAndRetryable:true,
      editorDraftBlocksSecondSettingsDraft:true,settingsDraftBlocksSecondEditorDraft:true,bothOriginalSavePathsComplete:true,
      scheduleDraftProtected:true,sourceDraftProtected:true,draftsPreservedAcrossRoutes:true,idleProviderUpdatePreservesDraft:true,
      structuredPreview:true,sourceOverridesVisible:true,safeTextNames:true,applicationPlans:true,mobilePreviewFits:true,pageErrors:errors};
    await fs.writeFile(path.join(root,'dist/subscription-drafts-browser-evidence.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result));
  }finally{for(const context of browser.contexts())for(const tab of context.pages())await tab.unrouteAll({behavior:'ignoreErrors'});await browser.close();}
})().then(()=>process.exit(0),error=>{console.error(error);process.exit(1);});
