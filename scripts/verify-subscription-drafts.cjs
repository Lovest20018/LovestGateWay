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
    const kind=()=>model.source.native_ppsub?'ppsub':'clash_patch';
    await page.route(origin+'/**',async route=>{
      const request=route.request(),target=new URL(request.url()).pathname,body=request.method()==='POST'?request.postDataJSON():null;
      const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if(target.startsWith('/sniffbox/')){
        if(target==='/sniffbox/settings/config')return reply(mismatchSettings?{...model,revision:'f'.repeat(64)}:model);
        if(target==='/sniffbox/subscription/status')return reply({ready:true,ppsub_write_allowed:true,edit_kind:kind(),base_revision:model.revision,operation});
        if(target==='/sniffbox/subscription/config')return reply({config:model.source.native_ppsub?native:projected,write_allowed:true,edit_kind:kind(),base_revision:model.revision,settings_revision:model.revision,snapshot_id:'snapshot',node_choices:['N1'],notice:'Fixture source: '+model.settings.ini.mode});
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
          if(body.dry_run)return reply({action:'restart',changes:body.settings_patch,editor_changed:true,save_target:'local_policy'});
          savedEditor=body;
          projected=JSON.parse(body.value);model.settings.ini.subtime=body.settings_patch.ini.subtime;model.source.local_policy=true;
          model.revision=(BigInt('0x'+model.revision)+1n).toString(16).padStart(64,'0');operation={id:body.operation_id,status:'applied',message:'Combined fixture saved'};
          return reply(operation,202);
        }
        return reply({mode:model.settings.ini.mode,clash_version:'fixture',ppsub:model.source.native_ppsub});
      }
      try{return route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
    });
    await page.goto(origin+'/ui/#/subscriptions');
    const ready=()=>page.waitForFunction(()=>document.querySelector('[data-ppsub-apply]')&&!document.querySelector('[data-ppsub-apply]').disabled);
    await ready();
    const health=page.locator('.monitor > label.ck input').first();
    const interval=page.getByLabel('订阅更新间隔',{exact:true});
    const navigation=page.locator('aside');
    const settings=()=>navigation.locator('a[href="#/settings"]').click();
    const subscriptions=()=>navigation.locator('a[href="#/subscriptions"]').click();
    const reload=async(confirm=false)=>{if(confirm)page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'重新读取',exact:true}).click();await ready();};
    model.revision='b'.repeat(64);model.settings.ini.subtime='8h';model.settings.ini.max_rec=6000;
    await reload();assert.equal(await interval.inputValue(),'8h');
    await health.check();await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});
    assert.equal(requests.at(-1).body.settings_revision,model.revision);
    await page.locator('#cancel-preview').click();await reload(true);
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
    await page.locator('.source-switch > summary').click();await page.locator('#source-url').fill('https://example.com/pending-source');
    await settings();assert.equal(await page.locator('#setting-ini-max_rec').isDisabled(),true);
    await subscriptions();await page.getByRole('button',{name:'取消切换',exact:true}).click();await settings();
    assert.equal(await page.locator('#setting-ini-max_rec').isDisabled(),false);
    assert.deepEqual(errors,[]);
    const result={syntheticOnly:true,reloadSynchronizesRevisionAndValues:true,mixedSnapshotsRejectedAndRetryable:true,
      editorDraftBlocksSecondSettingsDraft:true,settingsDraftBlocksSecondEditorDraft:true,bothOriginalSavePathsComplete:true,
      scheduleDraftProtected:true,sourceDraftProtected:true,draftsPreservedAcrossRoutes:true,pageErrors:errors};
    await fs.writeFile(path.join(root,'dist/subscription-drafts-browser-evidence.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
