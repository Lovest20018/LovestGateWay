/* Subscription operation model: isolated browser fixture, no live gateway writes. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),output=path.resolve(process.argv[2]||path.join(root,'dist/subscription-flow-ui'));
const origin='http://127.0.0.1:18886';
function python(code,value){return JSON.parse(execFileSync(process.env.PPGW_PYTHON||'python',['-c',"import sys,json;sys.path.insert(0,'integration');"+code],{cwd:root,input:JSON.stringify(value),encoding:'utf8'}));}
const defaults=python("from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini'].update(mode='yaml',yamlfile='custom.yaml',suburl='https://example.com/main-config');print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))",null);
let document={proxies:[{name:'N1',type:'socks5',server:'127.0.0.1',port:9}],
  'proxy-providers':{Airport:{type:'http',url:'https://example.com/nested-provider',path:'./providers/airport.yaml'}},
  'proxy-groups':[{name:'Main',type:'select',proxies:['N1','DIRECT']}],rules:['MATCH,Main']};
let projected=python('from clash_ppsub import editor_config;print(json.dumps(editor_config(json.load(sys.stdin))[0]))',document);
const native={exported_at:'fixture',subs:[{name:'Native provider',url:'https://example.com/native'},{name:'ProviderB',url:'https://example.com/k'},{name:'ProviderC',url:'https://example.com/d'}],'node-groups':[{name:'Native group',subs:['all']}],rules:[]};
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:980},reducedMotion:'reduce'}),page=await context.newPage(),errors=[],requests=[];
    page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>localStorage.setItem('PaoPaoGateWay',JSON.stringify({clashAPIConfigs:[{secret:'fixture-token'}]})));
    const model={...defaults,revision:'a'.repeat(64),assets:['custom.yaml','second.yaml'],source:{url:'https://example.com/main-config',native_ppsub:false,local_policy:true},mihomo_installed:true};
    let operation=null,job=null,savedEditor=null,sourceSwitched=0,manualRefreshes=0;
    const nodeCache={subscriptions:[{name:"Native provider",nodes:3,source:"cache",update_failed:true}]};
    const kind=()=>model.source.native_ppsub?'ppsub':'clash_patch';
    await page.route(origin+'/**',async route=>{
      const request=route.request(),target=new URL(request.url()).pathname,body=request.method()==='POST'?request.postDataJSON():null;
      const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if(target.startsWith('/sniffbox/')){
        if(target==='/sniffbox/settings/config')return reply(model);
        if(target==='/sniffbox/subscription/status')return reply({ready:true,ppsub_write_allowed:true,edit_kind:kind(),base_revision:model.revision,operation,node_cache:nodeCache});
        if(target==='/sniffbox/subscription/config')return reply({config:model.source.native_ppsub?native:projected,write_allowed:true,edit_kind:kind(),base_revision:model.revision,settings_revision:model.revision,snapshot_id:'snapshot',node_choices:['N1'],notice:'Fixture source: '+model.settings.ini.mode,node_cache:nodeCache});
        if(target==='/sniffbox/settings/preview'){
          requests.push({target,body});
          if(model.source.local_policy&&!body.reset_source)return reply({error:'SOURCE_CONFLICT'},409);
          return reply({action:'restart',changes:body.patch,effects:{core:'restart',source_reset:model.source.local_policy,services:[]}});
        }
        if(target==='/sniffbox/settings/apply'){
          requests.push({target,body});job=body;
          assert.equal(body.kind,'config');
          const patch=body.payload.patch.ini;
          Object.assign(model.settings.ini,patch);
          if(patch.suburl)model.source.url=patch.suburl;
          model.source.local_policy=false;model.source.native_ppsub=!!body.payload.source_ppsub;
          if(body.payload.source_ppsub)assert.deepEqual(JSON.parse(body.payload.source_ppsub),native);
          operation=null;sourceSwitched++;
          return reply({id:body.id,status:'queued'},202);
        }
        if(target.startsWith('/sniffbox/settings/jobs/'))return reply({id:job.id,status:'succeeded',result:{runtime:{status:'running'}}});
        if(target==='/sniffbox/subscription/apply'){
          requests.push({target,body});
          if(body.kind==='refresh_nodes'){
            assert.equal(body.settings_revision,model.revision);manualRefreshes++;
            operation={id:body.operation_id,status:'applied',kind:'refresh_nodes',message:'获取失败，已保留原节点'};
            return reply(operation,202);
          }
          if(body.dry_run)return reply({action:'restart',changes:body.settings_patch,editor_changed:true,save_target:'local_policy'});
          savedEditor=body;
          projected=JSON.parse(body.value);if(model.source.native_ppsub)native.subs=projected.subs;Object.assign(model.settings.ini,body.settings_patch.ini||{});model.source.local_policy=true;
          operation={id:body.operation_id,status:'applied',message:'Combined fixture saved'};
          return reply(operation,202);
        }
        return reply({mode:model.settings.ini.mode,clash_version:'fixture',ppsub:model.source.native_ppsub});
      }
      try{return route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
    });
    await page.goto(origin+'/ui/#/subscriptions');
    const ready=()=>page.waitForFunction(()=>document.querySelector('[data-ppsub-apply]')&&!document.querySelector('[data-ppsub-apply]').disabled);
    await ready();
    assert.equal(await page.getByLabel('订阅更新间隔',{exact:true}).count(),0);
    assert.equal(await page.getByRole('heading',{name:'当前配置来源',exact:true}).count(),1);
    assert.equal(await page.locator('.source-location').textContent(),'custom.yaml');
    const providerName=page.locator('.subs .item .row input').first();
    assert.equal(await providerName.getAttribute('readonly'),'');
    assert.equal(await page.locator('.subs .sfoot').isVisible(),false);
    assert.equal(await page.locator('.subs .ihead button').first().isVisible(),false);
    const providerUrl=page.locator('.subs .item .row input').nth(1);
    assert.equal(await providerUrl.inputValue(),'https://example.com/nested-provider');
    const health=page.locator('.monitor > label.ck input').first();await health.check();
    await page.locator('.source-switch > summary').click();
    await page.locator('#source-type').selectOption('remote');await page.locator('#source-url').fill('https://example.com/new-main');
    await page.getByRole('button',{name:'预览来源切换',exact:true}).click();
    await page.getByText('请先保存或放弃当前编辑，再切换配置来源。',{exact:true}).waitFor();
    assert.equal(requests.length,0);
    page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'重新读取',exact:true}).click();await ready();
    await page.getByRole('button',{name:'预览来源切换',exact:true}).click();
    await page.getByText('更换来源与现有本地策略或 PPsub 冲突。确认清除选项后重新预览。',{exact:true}).waitFor();
    await page.locator('#reset-source').check();
    await page.getByRole('button',{name:'预览来源切换',exact:true}).click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});
    await page.getByText('旧本地 PPsub / 策略将清除，并包含在此次备份中。',{exact:true}).waitFor();
    assert.equal(sourceSwitched,0);await page.locator('#apply').click();
    await page.waitForFunction(()=>document.querySelector('.source-kind')?.textContent==='远程完整配置');await ready();
    assert.equal(await page.locator('.source-location').textContent(),'https://example.com/new-main');
    assert.equal(await providerUrl.inputValue(),'https://example.com/nested-provider');
    await health.check();const interval=page.getByLabel('订阅更新间隔',{exact:true});await interval.fill('7h');await interval.press('Tab');
    await providerUrl.fill('https://example.com/nested-updated');
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.locator('#preview-dialog').waitFor({state:'visible'});
    assert.equal(savedEditor,null);await page.locator('#apply').click();await page.getByText('Combined fixture saved',{exact:true}).waitFor();await ready();
    assert.equal(savedEditor.settings_patch.ini.subtime,'7h');assert.equal(JSON.parse(savedEditor.value).global_monitor.enable,true);
    assert.equal(JSON.parse(savedEditor.value).subs[0].url,'https://example.com/nested-updated');
    await page.locator('.source-switch > summary').click();await page.locator('#source-type').selectOption('yaml');
    assert.equal(await page.locator('#reset-source').count(),1);await page.locator('#source-yaml').selectOption('second.yaml');await page.locator('#reset-source').check();
    await page.getByRole('button',{name:'预览来源切换',exact:true}).click();await page.locator('#apply').click();
    await page.waitForFunction(()=>document.querySelector('.source-location')?.textContent==='second.yaml');await ready();
    assert.equal(await page.getByLabel('订阅更新间隔',{exact:true}).count(),0);
    await page.screenshot({path:path.join(root,'dist/subscription-flow-yaml-desktop.png'),fullPage:true,animations:'disabled'});
    await page.locator('.source-switch > summary').click();await page.locator('#source-type').selectOption('ppsub');
    await page.locator('#source-ppsub').setInputFiles({name:'source.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(native))});
    await page.getByText('PPsub 文件已选中，预览并确认后才会切换来源。',{exact:true}).waitFor();
    await page.getByRole('button',{name:'预览来源切换',exact:true}).click();await page.locator('#apply').click();
    await page.waitForFunction(()=>document.querySelector('.source-kind')?.textContent==='原生 PPsub');await ready();
    assert.equal(await page.locator('.subs .sfoot').isVisible(),true);
    assert.equal(await page.locator('.subs .item .row input').first().getAttribute('readonly'),null);
    assert.equal(await page.getByRole('button',{name:'导入配置',exact:true}).isVisible(),true);
    assert.equal(await page.getByRole('button',{name:'导出配置',exact:true}).isVisible(),true);
    assert.equal(sourceSwitched,3);
    const manual=page.getByLabel('全局默认仅手动更新',{exact:true});
    await manual.check();
    assert.equal(await page.getByLabel('订阅更新间隔',{exact:true}).count(),0);
    await page.getByRole('button',{name:'更新全部订阅',exact:true}).click();
    await page.getByText('请先保存或放弃当前编辑，再更新订阅节点。',{exact:true}).waitFor();
    assert.equal(manualRefreshes,0);
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});await page.locator('#apply').click();
    await page.getByText('Combined fixture saved',{exact:true}).waitFor();await ready();
    assert.equal(savedEditor.settings_patch.ini.subtime,'manual');
    assert.equal(await manual.isChecked(),true);
    await page.getByRole('button',{name:'更新全部订阅',exact:true}).click();
    await page.getByText('获取失败，已保留原节点',{exact:true}).waitFor();await ready();
    assert.equal(manualRefreshes,1);
    assert.match(await page.locator('[data-node-cache]').textContent(),/已保存 3 个节点.*获取失败/);
    const firstMode=page.getByLabel('Native provider 更新方式',{exact:true});
    await firstMode.selectOption('manual');
    await page.getByLabel('ProviderB 更新方式',{exact:true}).selectOption('auto');
    await page.getByLabel('ProviderB 自动更新间隔',{exact:true}).fill('0d');
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.getByText('机场自动更新间隔请填写 30m、6h、1d 等有效值。',{exact:true}).waitFor();
    await page.getByLabel('ProviderB 自动更新间隔',{exact:true}).fill('1d');
    await page.getByLabel('ProviderC 更新方式',{exact:true}).selectOption('auto');
    await page.getByLabel('ProviderC 自动更新间隔',{exact:true}).fill('1d');
    await page.getByRole('button',{name:'更新此订阅：Native provider',exact:true}).click();
    await page.getByText('请先保存或放弃当前编辑，再更新订阅节点。',{exact:true}).waitFor();
    assert.equal(manualRefreshes,1);
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});await page.locator('#apply').click();
    await page.getByText('Combined fixture saved',{exact:true}).waitFor();await ready();
    assert.equal(JSON.parse(savedEditor.value).subs[0].update_mode,'manual');
    assert.equal(JSON.parse(savedEditor.value).subs[1].update_interval,'1d');
    assert.equal(await firstMode.inputValue(),'manual');
    assert.equal(await page.getByLabel('ProviderB 更新方式',{exact:true}).inputValue(),'auto');
    await page.locator('.subs .item').nth(1).locator('.ihead button').nth(0).click();
    await page.waitForFunction(()=>document.querySelector('.subs .item .row input')?.value==='ProviderB');
    assert.equal(await page.locator('.subs .item [data-provider-mode]').first().inputValue(),'auto');
    assert.equal(await page.locator('.subs .item [data-provider-mode]').nth(1).inputValue(),'manual');
    await page.locator('.subs .item').nth(0).locator('.ihead button').nth(1).click();
    await page.waitForFunction(()=>document.querySelector('.subs .item .row input')?.value==='Native provider');
    await page.getByRole('button',{name:'更新此订阅：Native provider',exact:true}).click();
    await page.getByText('获取失败，已保留原节点',{exact:true}).waitFor();await ready();
    assert.equal(manualRefreshes,2);
    assert.deepEqual(requests.filter(row=>row.body.kind==='refresh_nodes').at(-1).body.providers,['Native provider']);
    await page.screenshot({path:path.join(root,'dist/manual-subscriptions-desktop.png'),fullPage:true,animations:'disabled'});

    await page.setViewportSize({width:390,height:844});
    await page.addStyleTag({content:'* { content-visibility: visible !important; }'});
    await page.screenshot({path:path.join(root,'dist/manual-subscriptions-mobile.png'),fullPage:true,animations:'disabled'});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.deepEqual(errors,[]);
    const evidence={syntheticOnly:true,yamlScheduleHidden:true,providerStructureRestricted:true,providerUrlDistinctFromMain:true,dirtySourceSwitchGuard:true,bidirectionalSourceSwitch:true,backupResetPreview:true,combinedEditorAndSchedule:true,ppsubSourceImport:true,capabilitiesAfterSourceChange:true,manualScheduleSaved:true,perProviderPlansSaved:true,invalidIntervalRejected:true,reorderedPlansBoundToProvider:true,targetedRefresh:true,manualRefreshDraftGuard:true,failedRefreshRetainsNodeStatus:true,sourceSwitches:sourceSwitched,pageErrors:errors};
    await fs.writeFile(path.join(root,'dist/subscription-flow-browser-evidence.json'),JSON.stringify(evidence,null,2)+'\n');console.log(JSON.stringify(evidence));
    await context.close();
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
