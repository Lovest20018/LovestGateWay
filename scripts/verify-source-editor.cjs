/* Source UI acceptance on a non-secure HTTP synthetic origin; no live gateway. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),output=path.resolve(process.argv[2]||path.join(root,'frontend/dist'));
const origin='http://fixture-gateway.invalid',password='Synthetic管理密码8';
const secret=require('node:crypto').createHash('sha256').update(password).digest('hex');
function python(code,value){return JSON.parse(execFileSync(process.env.PPGW_PYTHON||'python',['-c',"import sys,json;sys.path.insert(0,'integration');"+code],{cwd:root,input:JSON.stringify(value),encoding:'utf8'}));}
const defaults=python("from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini'].update(mode='suburl',suburl='ppsub@http://127.0.0.1/ppsub.json');print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))",null);
const raw={secret:'server-only-secret',dns:{nameserver:['1.1.1.1']},proxies:[{name:'N1',type:'socks5',server:'127.0.0.1',port:9,password:'original-node-secret'}],
  'proxy-providers':{Airport:{type:'http',url:'https://example.com/sub',path:'./saved.yaml',header:{Authorization:['fixture-provider-secret']}}},
  'proxy-groups':[{name:'Main',type:'select',use:['Airport'],proxies:['N1','DIRECT'],hidden:true,lazy:true}],
  'rule-providers':{Rules:{type:'http',url:'https://example.com/rules',behavior:'domain',format:'yaml',interval:3600,path:'./rules.yaml',custom:{preserve:1}}},rules:['DOMAIN,example.com,Main','MATCH,Main']};
const baseline=python('from clash_ppsub import editor_config;print(json.dumps(editor_config(json.load(sys.stdin))[0]))',raw);
const native={version:'PaoPaoGateWay 3.1',exported_at:'fixture',custom:{untouched:[1,{token:'hidden-fixture'}]},
  subs:[{name:'Airport',url:'https://example.com/sub',extension:{token:'hidden-provider'}}],
  'node-groups':[{name:'Main',subs:['all'],include:['N1'],keywords:['original'],exclude_keywords:[],hidden_feature:{nested:1},monitor_url:'https://example.com/keep-hidden'}],
  rules:[{priority:10,fixrule:['MATCH,Main'],unknown_rule:true},{priority:20,type:'rule-set',name:'Rules',url:'https://example.com/rules',behavior:'domain',format:'yaml',interval:3600,custom:{preserve:1}},{priority:30,url:'https://example.com/download',isforced:true}]};
async function exercise(browser,kind){
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
  page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{if(!localStorage.getItem('PaoPaoGateWay'))localStorage.setItem('PaoPaoGateWay',JSON.stringify({favorite:'preserve-me',clashAPIConfigs:[{secret:'',label:'existing-profile'},{secret:'secondary-auth-fixture',label:'second-profile'}]}));});
  let config=structuredClone(kind==='ppsub'?native:baseline),operation=null,expired=false,posted=null,merged=null,validations=0;
  const model={...structuredClone(defaults),revision:'a'.repeat(64),assets:[],source:{native_ppsub:kind==='ppsub',local_policy:kind==='clash_patch'},mihomo_installed:true};
  await page.route(origin+'/**',async route=>{
    const request=route.request(),target=new URL(request.url()).pathname,reply=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if(target.startsWith('/sniffbox/')){
      if(request.headers().authorization!=='Bearer '+secret||expired)return reply({error:'LOGIN_REQUIRED'},401);
      if(target==='/sniffbox/info')return reply({mode:'suburl',ppsub:kind==='ppsub',clash_version:'fixture'});
      if(target==='/sniffbox/settings/config')return reply(model);
      if(target==='/sniffbox/settings/status')return reply({management:{state:'loaded',recent:[]},running_mode:'suburl',source:model.source});
      if(target==='/sniffbox/subscription/status')return reply({edit_kind:kind,base_revision:model.revision,ppsub_write_allowed:true,operation});
      if(target==='/sniffbox/subscription/providers')return reply({subscriptions:[]});
      if(target==='/sniffbox/subscription/config')return reply({edit_kind:kind,config,base_revision:model.revision,settings_revision:model.revision,snapshot_id:'fixture-snapshot',write_allowed:true,node_choices:['N1'],notice:'Synthetic source'});
      if(target==='/sniffbox/subscription/apply'){
        const body=request.postDataJSON(),submitted=JSON.parse(body.value);
        if(kind==='ppsub')python("from unified_editor import validate_ppsub;v=json.load(sys.stdin);validate_ppsub(v);print(json.dumps({'valid':True}))",submitted);
        else merged=python("from clash_ppsub import merge_editor,feature_options;v=json.load(sys.stdin);feature_options(v['submitted']);print(json.dumps(merge_editor(v['raw'],v['baseline'],v['submitted'])[0]))",{raw,baseline,submitted});
        validations++;posted=submitted;
        if(body.dry_run)return reply({action:'restart',editor_changed:true,changes:{},application:{action:'restart',core_active:true,services:[]}});
        config=submitted;operation={status:'applied',message:'Synthetic editor saved'};return reply(operation,202);
      }
      return reply({});
    }
    try{return route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
  });
  try{
    await page.goto(origin+'/ui/#/subscriptions');
    assert.equal(await page.evaluate(()=>isSecureContext),false,'must exercise HTTP LAN password hashing');
    await page.getByLabel('管理密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();
    const ready=()=>page.waitForFunction(()=>document.querySelector('[data-ppsub-apply]')&&!document.querySelector('[data-ppsub-apply]').disabled);await ready();
    if(kind==='ppsub'){
      await page.evaluate(()=>{const original=File.prototype.text;window.delayedImports={};File.prototype.text=function(){if(this.name.startsWith('delayed-import-')){const name=this.name;return new Promise(resolve=>{window.delayedImports[name]=()=>original.call(this).then(resolve);});}return original.apply(this,arguments);};});
      const chooseDelayed=async(name,label,confirm=false)=>{if(confirm)page.once('dialog',dialog=>dialog.accept());const value=structuredClone(native);value['node-groups'][0].name=label;await page.locator('.source-editor input[type=file]').setInputFiles({name,mimeType:'application/json',buffer:Buffer.from(JSON.stringify(value))});await page.waitForFunction(name=>typeof window.delayedImports[name]==='function',name);};
      const release=async name=>{await page.evaluate(name=>window.delayedImports[name](),name);await page.waitForTimeout(50);};
      const groupName=page.locator('.groups .item').first().locator('[data-field="name"]');
      await chooseDelayed('delayed-import-edit.json','Late import must not replace typed draft');
      await groupName.fill('Typed after file chosen');await release('delayed-import-edit.json');
      assert.equal(await groupName.inputValue(),'Typed after file chosen','late file read must not overwrite newer form input');
      assert.match(await page.locator('[data-import-status]').textContent(),/已取消迟到的导入/);
      await chooseDelayed('delayed-import-reload.json','Late import must not replace reloaded document',true);
      page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'重新读取',exact:true}).click();await ready();await release('delayed-import-reload.json');
      assert.equal(await groupName.inputValue(),'Main','late import must not overwrite a newer server reload');
      await chooseDelayed('delayed-import-older.json','Older import');
      const latest=structuredClone(native);latest['node-groups'][0].name='Latest import';await page.locator('.source-editor input[type=file]').setInputFiles({name:'latest-import.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(latest))});await page.waitForFunction(()=>document.querySelector('.groups [data-field="name"]')?.value==='Latest import');await release('delayed-import-older.json');
      assert.equal(await groupName.inputValue(),'Latest import','an older file must not replace a newer import');
      await chooseDelayed('delayed-import-unmount.json','Late import must not reach next editor',true);
      page.once('dialog',dialog=>dialog.accept());await page.locator('#shell-nav [data-route="overview"]').click();await page.locator('.source-editor').waitFor({state:'detached'});await release('delayed-import-unmount.json');
      const storageCheck=await page.frameLocator('.legacy-frame').locator('body').evaluate((_,secret)=>{
        const verified=JSON.parse(localStorage.getItem('PaoPaoGateWay'));
        localStorage.setItem('PaoPaoGateWay',JSON.stringify({favorite:'changed-in-legacy',clashAPIConfigs:[{secret,label:'updated-legacy-profile'},{secret:'secondary-auth-fixture',label:'second-profile'}],legacyOption:'new-value'}));
        const changed=JSON.parse(localStorage.getItem('PaoPaoGateWay'));
        localStorage.setItem('ppgw-theme','dark');localStorage.removeItem('PaoPaoGateWay');const loggedOut=JSON.parse(localStorage.getItem('PaoPaoGateWay'));
        localStorage.setItem('PaoPaoGateWay',JSON.stringify({clashAPIConfigs:[{secret,label:'explicit-single-profile'}],favorite:'explicit-settings-update'}));const explicit=JSON.parse(localStorage.getItem('PaoPaoGateWay'));
        localStorage.setItem('PaoPaoGateWay',JSON.stringify({favorite:'preserve-me',clashAPIConfigs:[{secret,label:'existing-profile'},{secret:'secondary-auth-fixture',label:'second-profile'}]}));
        return{verified,changed,loggedOut,explicit,theme:localStorage.getItem('ppgw-theme')};
      },secret);
      assert.equal(storageCheck.verified.clashAPIConfigs.length,2);assert.equal(storageCheck.verified.clashAPIConfigs[1].label,'second-profile');assert.equal(storageCheck.verified.clashAPIConfigs[1].secret,'secondary-auth-fixture');
      assert.equal(storageCheck.changed.favorite,'changed-in-legacy');assert.equal(storageCheck.changed.clashAPIConfigs[0].label,'updated-legacy-profile');
      assert.equal(storageCheck.loggedOut.favorite,'changed-in-legacy');assert.equal(storageCheck.loggedOut.legacyOption,'new-value');assert.equal(storageCheck.loggedOut.clashAPIConfigs[0].secret,'');assert.equal(storageCheck.loggedOut.clashAPIConfigs[1].label,'second-profile');assert.equal(storageCheck.loggedOut.clashAPIConfigs[1].secret,'');assert.equal(storageCheck.theme,'dark');
      assert.equal(storageCheck.explicit.favorite,'explicit-settings-update');assert.equal(storageCheck.explicit.clashAPIConfigs.length,1,'explicit profile removal remains possible');assert.equal(storageCheck.explicit.clashAPIConfigs[0].label,'explicit-single-profile');
      await page.locator('#shell-nav [data-route="subscriptions"]').click();await ready();assert.equal(await groupName.inputValue(),'Main','disposed import must not write into a newly mounted editor');
      const advanced=page.locator('.editor-body > .editor-advanced');await advanced.locator('summary').click();await page.waitForFunction(()=>document.querySelector('.editor-body > .editor-advanced textarea')?.value);
      const value=JSON.parse(await advanced.locator('textarea').inputValue());value.advanced_added={preserve:true};
      await advanced.locator('textarea').fill(JSON.stringify(value));await advanced.getByRole('button',{name:'应用 JSON 修改',exact:true}).click();
      page.once('dialog',dialog=>dialog.dismiss());await page.locator('#shell-nav [data-route="overview"]').click();assert.equal(new URL(page.url()).hash,'#/subscriptions','JSON edits participate in draft navigation protection');
    }
    const fill=async(scope,key,value)=>scope.locator('[data-field="'+key+'"]').fill(value);
    const provider=page.locator('.subs .item').first(),group=page.locator('.groups .item').first();
    await fill(provider,'url','https://example.com/edited-provider');
    if(kind==='ppsub'){await fill(provider,'name','Edited airport');await provider.locator('[data-field="isforced"]').check();await page.getByLabel('Edited airport 更新方式',{exact:true}).selectOption('auto');await page.getByLabel('Edited airport 自动更新间隔',{exact:true}).fill('7h');}
    else{assert.equal(await provider.locator('[data-field="name"]').isEditable(),false);assert.equal(await provider.getByRole('button',{name:'复制',exact:true}).isVisible(),false);}
    await fill(group,'name','Edited Main');await fill(group,'keywords','exp#HK|JP\nkeyword');await fill(group,'exclude_keywords','expired');await fill(group,'subs',kind==='ppsub'?'all':'Airport');await fill(group,'include','N1\nDIRECT');
    await group.getByLabel('节点组模式',{exact:true}).selectOption('fallback');await fill(group,'monitor_url','https://example.com/health');await fill(group,'expected_status','204');await fill(group,'retries','4');
    await group.getByLabel('节点组模式',{exact:true}).selectOption('speedtest');await fill(group,'speedtest_url','https://example.com/speedtest');await fill(group,'interval','900');
    await group.locator('[data-field="use_pre_proxy"]').check();await fill(group,'pre_proxy_group','DIRECT');
    await group.getByLabel('可选节点或代理组',{exact:true}).selectOption('REJECT');await group.getByRole('button',{name:'加入固定列表',exact:true}).click();
    await group.getByRole('button',{name:'复制',exact:true}).click();assert.equal(await page.locator('.groups .item').count(),2);
    await page.locator('.groups .item').nth(1).getByRole('button',{name:'上移',exact:true}).click();assert.equal(await page.locator('.groups .item').first().locator('[data-field="name"]').inputValue(),'Edited Main 副本');
    page.once('dialog',dialog=>dialog.accept());await page.locator('.groups .item').first().getByRole('button',{name:'删除',exact:true}).click();
    const ruleProvider=page.locator('.rules .item').filter({has:page.locator('[data-field="behavior"]')});await fill(ruleProvider,'name','EditedRules');await fill(ruleProvider,'url','https://example.com/edited-rules');await ruleProvider.locator('[data-field="behavior"]').selectOption('classical');await ruleProvider.locator('[data-field="format"]').selectOption('text');await fill(ruleProvider,'interval','7200');await fill(ruleProvider,'proxy','DIRECT');
    const manual=page.locator('.rules .item').filter({has:page.locator('[data-field="fixrule"]')}).first();await fill(manual,'remark','Edited manual rules');await fill(manual,'fixrule','DOMAIN,edited.example,Edited Main\nMATCH,Edited Main');
    if(kind==='clash_patch')assert.equal(await manual.getByLabel('规则类型',{exact:true}).isDisabled(),true);
    else{const urlRules=page.locator('.rules .item').last();await fill(urlRules,'url','https://example.com/edited-download');await urlRules.locator('[data-field="isforced"]').uncheck();}
    for(const index of [0,1,2])await page.locator('.monitor fieldset').nth(index).locator('[data-field="enable"]').check();
    await fill(page.locator('.monitor fieldset').nth(0),'url','https://example.com/global');await fill(page.locator('.monitor fieldset').nth(0),'retries','3');await fill(page.locator('.monitor fieldset').nth(0),'expected_status','204');
    await fill(page.locator('.monitor fieldset').nth(1),'password','fixture-password-123456');await page.locator('.monitor fieldset').nth(1).locator('[data-field="cipher"]').selectOption('aes-128-gcm');await fill(page.locator('.monitor fieldset').nth(2),'hour','5');
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.locator('#preview-dialog').waitFor({state:'visible'});await page.locator('#apply').click();await page.getByText('Synthetic editor saved',{exact:true}).waitFor();await ready();
    assert.ok(validations>=2);assert.equal(posted['node-groups'][0].monitor_url,'https://example.com/health');assert.equal(posted['node-groups'][0].interval,900);assert.equal(posted.global_monitor.expected_status,'204');assert.equal(posted.restart_cron.hour,5);
    if(kind==='ppsub'){assert.deepEqual(posted.custom,native.custom);assert.deepEqual(posted.advanced_added,{preserve:true});assert.deepEqual(posted.subs[0].extension,native.subs[0].extension);assert.deepEqual(posted['node-groups'][0].hidden_feature,{nested:1});assert.equal(posted.subs[0].update_interval,'7h');assert.equal(posted.rules[0].unknown_rule,true);}
    else{assert.equal(posted.subs[0]._clash_id,'s:0');assert.equal(posted['node-groups'][0]._clash_id,'g:0');assert.equal(merged.secret,raw.secret);assert.deepEqual(merged.dns,raw.dns);assert.deepEqual(merged.proxies,raw.proxies);assert.equal(merged['proxy-groups'][0].hidden,true);assert.equal(merged['proxy-groups'][0].lazy,true);assert.deepEqual(merged['proxy-providers'].Airport.header,raw['proxy-providers'].Airport.header);assert.deepEqual(merged['rule-providers'].EditedRules.custom,{preserve:1});assert.equal(merged['proxy-groups'][0].type,'url-test');assert.deepEqual(merged.rules,['DOMAIN,edited.example,Edited Main','MATCH,Edited Main']);}
    await fill(page.locator('.groups .item').first(),'name','Draft survives reauthentication');expired=true;
    await page.waitForFunction(()=>document.querySelector('#shell-status')?.textContent.includes('重新登录'),{timeout:15000});expired=false;
    await page.getByRole('button',{name:'重新登录',exact:true}).click();await page.getByLabel('管理密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('#reauth-dialog').waitFor({state:'detached'});
    assert.equal(await page.locator('.groups .item').first().locator('[data-field="name"]').inputValue(),'Draft survives reauthentication');
    const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('PaoPaoGateWay')));assert.equal(saved.favorite,'preserve-me');assert.equal(saved.clashAPIConfigs[0].label,'existing-profile');assert.equal(saved.clashAPIConfigs[0].secret,secret);assert.equal(saved.clashAPIConfigs[1].label,'second-profile');assert.equal(saved.clashAPIConfigs[1].secret,'secondary-auth-fixture');
    await page.evaluate(()=>window.postMessage({type:'ppgw:route',hash:'#/overview'},location.origin));assert.equal(new URL(page.url()).hash,'#/subscriptions','messages from an untrusted source are ignored');
    page.once('dialog',dialog=>dialog.accept());await page.locator('#shell-nav').getByRole('button',{name:'退出登录',exact:true}).click();await page.getByLabel('管理密码',{exact:true}).waitFor();const loggedOut=await page.evaluate(()=>JSON.parse(localStorage.getItem('PaoPaoGateWay')));assert.equal(loggedOut.favorite,'preserve-me');assert.equal(loggedOut.clashAPIConfigs[0].label,'existing-profile');assert.equal(loggedOut.clashAPIConfigs[0].secret,'');
    assert.deepEqual(errors,[]);return{kind,nonSecureHttpLogin:true,actualFormsValidatedByBackend:true,hiddenFieldsPreserved:true,copyMoveDelete:true,reauthenticationPreservesDraft:true,storedPreferencesPreserved:true,foreignMessageIgnored:true,...(kind==='ppsub'?{lateImportPreservesNewInput:true,reloadInvalidatesImport:true,newerImportWins:true,unmountedImportIgnored:true}:{}),validations,pageErrors:errors};
  }finally{await context.close();}
}
(async()=>{const browser=await chromium.launch({channel:'chrome',headless:true});try{const results=[];for(const kind of ['ppsub','clash_patch'])results.push(await exercise(browser,kind));await fs.mkdir(path.join(root,'dist'),{recursive:true});await fs.writeFile(path.join(root,'dist/source-editor-browser-evidence.json'),JSON.stringify({syntheticOnly:true,results},null,2)+'\n');console.log(JSON.stringify({syntheticOnly:true,results}));}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
