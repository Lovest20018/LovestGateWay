const {sessionFixture}=require('./browser-session-fixture.cjs');
/* Source UI acceptance on a non-secure HTTP synthetic origin; no live gateway. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),output=path.resolve(process.argv[2]||path.join(root,'frontend/dist'));
const origin='http://fixture-gateway.invalid',password='Synthetic管理密码8';
const secret=require('node:crypto').createHash('sha256').update(password).digest('hex');
function python(code,value){return JSON.parse(execFileSync(process.env.PPGW_PYTHON||'python',['-c',"import sys,json;sys.path.insert(0,'integration');"+code],{cwd:root,input:JSON.stringify(value),encoding:'utf8'}));}
const defaults=python("from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini'].update(mode='suburl',suburl='ppsub@http://127.0.0.1/ppsub.json');print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))",null);
const native={version:'PaoPaoGateWay 3.1',exported_at:'fixture',custom:{untouched:[1,{token:'hidden-fixture'}]},
  subs:[{name:'Airport',url:'https://example.com/sub',extension:{token:'hidden-provider'}}],
  'node-groups':[{name:'Main',subs:['all'],include:['N1'],keywords:['original'],exclude_keywords:[],hidden_feature:{nested:1},monitor_url:'https://example.com/keep-hidden'}],
  rules:[{priority:10,fixrule:['MATCH,Main'],unknown_rule:true},{priority:20,type:'rule-set',name:'Rules',url:'https://example.com/rules',behavior:'domain',format:'yaml',interval:3600,custom:{preserve:1}},{priority:30,url:'https://example.com/download',isforced:true}]};
async function exercise(browser){
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
  page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{if(!localStorage.getItem('PaoPaoGateWay'))localStorage.setItem('PaoPaoGateWay',JSON.stringify({favorite:'preserve-me',clashAPIConfigs:[{secret:'',label:'existing-profile'},{secret:'secondary-auth-fixture',label:'second-profile'}]}));});
  let config=structuredClone(native),operation=null,expired=false,posted=null,validations=0;
  const model={...structuredClone(defaults),revision:'a'.repeat(64),assets:[],source:{native_ppsub:true,local_policy:false},mihomo_installed:true};
  const sessions=sessionFixture({proof:()=>secret,initial:false,expired:()=>expired});
  await page.route(origin+'/**',async route=>{
    const request=route.request(),target=new URL(request.url()).pathname,reply=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if(target.startsWith('/sniffbox/')){
      if(!sessions.authorized(request)||expired)return reply({error:'LOGIN_REQUIRED'},401);
      if(target==='/sniffbox/info')return reply({mode:'suburl',ppsub:true,clash_version:'fixture'});
      if(target==='/sniffbox/settings/config')return reply(model);
      if(target==='/sniffbox/settings/status')return reply({management:{state:'loaded',recent:[]},running_mode:'suburl',source:model.source});
      if(target==='/sniffbox/subscription/status')return reply({edit_kind:'ppsub',base_revision:model.revision,ppsub_write_allowed:true,operation});
      if(target==='/sniffbox/subscription/providers')return reply({subscriptions:[]});
      if(target==='/sniffbox/subscription/config')return reply({edit_kind:'ppsub',config,base_revision:model.revision,settings_revision:model.revision,write_allowed:true,node_choices:['N1'],notice:'Synthetic source'});
      if(target==='/sniffbox/subscription/apply'){
        const body=request.postDataJSON(),submitted=JSON.parse(body.value);
        assert.equal(body.kind,'editor');
        assert.equal(body.edit_kind,'ppsub');
        assert.equal(Object.hasOwn(body,'snapshot_id'),false);
        const validation=python(`
from unified_editor import validate_ppsub, editor_error_details
submitted = json.load(sys.stdin)
try:
    validate_ppsub(submitted)
except ValueError as error:
    details = editor_error_details(error)
    if details is None:
        raise
    print(json.dumps({'failure': details}))
else:
    print('{}')
`,submitted);
        if(validation.failure)return reply(validation.failure,400);
        validations++;posted=submitted;
        if(body.dry_run)return reply({action:'restart',editor_changed:true,changes:{},application:{action:'restart',core_active:true,services:[]}});
        config=submitted;operation={status:'applied',message:'Synthetic editor saved'};return reply(operation,202);
      }
      return reply({});
    }
    try{return route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
  });
  try{
    await sessions.install(page);
    await page.goto(origin+'/ui/#/subscriptions');
    assert.equal(await page.evaluate(()=>isSecureContext),false,'must exercise HTTP LAN password hashing');
    await page.getByLabel('管理密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();
    const ready=()=>page.waitForFunction(()=>document.querySelector('.source-editor')?.dataset.editorState==='loaded');await ready();
    const confirmation=(title,name)=>page.getByRole('dialog',{name:title,exact:true}).getByRole('button',{name,exact:true});
    for(const width of [320,375]){
      await page.setViewportSize({width,height:700});
      assert.equal(await page.locator('#shell-page-title').textContent(),'订阅');
      const sizes=await page.locator('.source-editor .phead').evaluate(header=>[...header.querySelectorAll('button')].filter(b=>b.getClientRects().length).map(b=>{const r=b.getBoundingClientRect();return{top:r.top,width:r.width,height:r.height};}));
      assert.ok(sizes.length>=3&&sizes.every(size=>size.height>=40&&Math.abs(size.width-sizes[0].width)<1));
      assert.ok(Math.abs(sizes[1].top-sizes[0].top)<1);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    }
    await page.setViewportSize({width:1440,height:950});
    {const privateProvider=page.locator('.subs .item').first(),privateUrl=privateProvider.locator('[data-field=url]');assert.equal(await privateUrl.inputValue(),'');assert.equal((await privateProvider.innerText()).includes(config.subs[0].url),false);await privateProvider.getByRole('button',{name:'显示地址',exact:true}).click();assert.equal(await privateUrl.inputValue(),config.subs[0].url);await privateProvider.getByRole('button',{name:'隐藏地址',exact:true}).click();assert.equal(await privateUrl.inputValue(),'');}
    {
      await page.evaluate(()=>{const original=File.prototype.text;window.delayedImports={};File.prototype.text=function(){if(this.name.startsWith('delayed-import-')){const name=this.name;return new Promise(resolve=>{window.delayedImports[name]=()=>original.call(this).then(resolve);});}return original.apply(this,arguments);};});
      const chooseDelayed=async(name,label,confirm=false)=>{const value=structuredClone(native);value['node-groups'][0].name=label;await page.locator('.source-editor input[type=file]').setInputFiles({name,mimeType:'application/json',buffer:Buffer.from(JSON.stringify(value))});if(confirm)await confirmation('导入 LovestSub','替换并导入').click();await page.waitForFunction(name=>typeof window.delayedImports[name]==='function',name);};
      const release=async name=>{await page.evaluate(name=>window.delayedImports[name](),name);await page.waitForTimeout(50);};
      const groupName=page.locator('.groups .item').first().locator('[data-field="name"]');
      await chooseDelayed('delayed-import-edit.json','Late import must not replace typed draft');
      await groupName.fill('Typed after file chosen');await release('delayed-import-edit.json');
      assert.equal(await groupName.inputValue(),'Typed after file chosen','late file read must not overwrite newer form input');
      assert.match(await page.locator('[data-import-status]').textContent(),/已取消迟到的导入/);
      await chooseDelayed('delayed-import-reload.json','Late import must not replace reloaded document',true);
      await page.getByRole('button',{name:'重新读取',exact:true}).click();await confirmation('重新读取配置','放弃并重新读取').click();await ready();await release('delayed-import-reload.json');
      assert.equal(await groupName.inputValue(),'Main','late import must not overwrite a newer server reload');
      await chooseDelayed('delayed-import-older.json','Older import');
      const latest=structuredClone(native);latest['node-groups'][0].name='Latest import';await page.locator('.source-editor input[type=file]').setInputFiles({name:'latest-import.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(latest))});await page.waitForFunction(()=>document.querySelector('.groups [data-field="name"]')?.value==='Latest import');await release('delayed-import-older.json');
      assert.equal(await groupName.inputValue(),'Latest import','an older file must not replace a newer import');
      await chooseDelayed('delayed-import-unmount.json','Late import must not reach next editor',true);
      await page.locator('#shell-nav [data-route="overview"]').click();await confirmation('离开当前页面','确认离开').click();await page.locator('.source-editor').waitFor({state:'detached'});await release('delayed-import-unmount.json');
      // Visit the maintained connection page and verify shared storage preferences.
      await page.locator('#shell-nav [data-route="connections"]').click();await page.locator('#connections-page').waitFor();
      const storageCheck=await page.locator('#connections-page').evaluate(async(_,secret)=>{
        const manifest=await (await fetch('/ui/manifest.json')).json();const {setSessionContext,sessionContext}=await import('/ui/'+manifest.assets['auth.js']);
        const contextBefore=sessionContext(),verified=JSON.parse(localStorage.getItem('PaoPaoGateWay'));
        localStorage.setItem('PaoPaoGateWay',JSON.stringify({favorite:'changed-in-legacy',clashAPIConfigs:[{secret,label:'updated-legacy-profile'},{secret:'secondary-auth-fixture',label:'second-profile'}],legacyOption:'new-value'}));
        const changed=JSON.parse(localStorage.getItem('PaoPaoGateWay'));
        localStorage.setItem('ppgw-theme','dark');setSessionContext('');const loggedOut=JSON.parse(localStorage.getItem('PaoPaoGateWay'));
        localStorage.setItem('PaoPaoGateWay',JSON.stringify({clashAPIConfigs:[{secret,label:'explicit-single-profile'}],favorite:'explicit-settings-update'}));const explicit=JSON.parse(localStorage.getItem('PaoPaoGateWay'));
        localStorage.setItem('PaoPaoGateWay',JSON.stringify({favorite:'preserve-me',clashAPIConfigs:[{secret,label:'existing-profile'},{secret:'secondary-auth-fixture',label:'second-profile'}]}));
        setSessionContext(contextBefore);
        return{verified,changed,loggedOut,explicit,theme:localStorage.getItem('ppgw-theme')};
      },secret);
      assert.equal(storageCheck.verified.clashAPIConfigs.length,2);assert.equal(storageCheck.verified.clashAPIConfigs[1].label,'second-profile');assert.equal(storageCheck.verified.clashAPIConfigs[1].secret,'');
      assert.equal(storageCheck.changed.favorite,'changed-in-legacy');assert.equal(storageCheck.changed.clashAPIConfigs[0].label,'updated-legacy-profile');
      assert.equal(storageCheck.loggedOut.favorite,'changed-in-legacy');assert.equal(storageCheck.loggedOut.legacyOption,'new-value');assert.equal(storageCheck.loggedOut.clashAPIConfigs[0].secret,'');assert.equal(storageCheck.loggedOut.clashAPIConfigs[1].label,'second-profile');assert.equal(storageCheck.loggedOut.clashAPIConfigs[1].secret,'');assert.equal(storageCheck.theme,'dark');
      assert.equal(storageCheck.explicit.favorite,'explicit-settings-update');assert.equal(storageCheck.explicit.clashAPIConfigs.length,1,'explicit profile removal remains possible');assert.equal(storageCheck.explicit.clashAPIConfigs[0].label,'explicit-single-profile');
      await page.locator('#shell-nav [data-route="subscriptions"]').click();await ready();assert.equal(await groupName.inputValue(),'Main','disposed import must not write into a newly mounted editor');
      const advanced=page.locator('.editor-body > .editor-advanced');await advanced.locator('summary').click();await page.waitForFunction(()=>document.querySelector('.editor-body > .editor-advanced textarea')?.value);
      const value=JSON.parse(await advanced.locator('textarea').inputValue());value.advanced_added={preserve:true};
      await advanced.locator('textarea').fill(JSON.stringify(value));await advanced.getByRole('button',{name:'应用 JSON 修改',exact:true}).click();
      await page.locator('#shell-nav [data-route="overview"]').click();await confirmation('离开当前页面','取消').click();assert.equal(new URL(page.url()).hash,'#/subscriptions','JSON edits participate in draft navigation protection');
    }
    const fill=async(scope,key,value)=>{if(await scope.evaluate(node=>node.tagName==='DETAILS'&&!node.open))await scope.locator(':scope > summary').click();const control=scope.locator('[data-field="'+key+'"]');if(key==='url'&&await control.getAttribute('readonly')!==null&&await scope.getByRole('button',{name:'修改地址',exact:true}).count())await scope.getByRole('button',{name:'修改地址',exact:true}).click();await control.fill(value);};
    const provider=page.locator('.subs .item').first(),group=page.locator('.groups .item').first();
    await fill(provider,'url','https://example.com/edited-provider');
    await fill(provider,'name','Edited airport');await provider.locator('[data-field="isforced"]').check();await page.getByLabel('Edited airport 更新方式',{exact:true}).selectOption('auto');await page.getByLabel('Edited airport 自动更新间隔',{exact:true}).fill('7h');
    const save=page.getByRole('button',{name:'保存并应用',exact:true}),preview=page.locator('#preview-dialog');
    const modes=group.getByRole('radiogroup',{name:'节点组模式'}),failover=group.getByRole('checkbox',{name:'故障转移',exact:true});
    assert.deepEqual(await modes.getByRole('radio').evaluateAll(nodes=>nodes.map(node=>node.value)),['manual','speedtest','smart']);
    assert.equal(await modes.getByRole('radio',{name:'手动选择'}).isChecked(),true);
    assert.equal(await failover.isChecked(),false);
    assert.equal(await group.locator('[data-field=monitor_url]').count(),0);
    await failover.check();
    await group.getByText('平时可手动切换节点；当前节点连续检测失败时自动切换到延迟最低的可用节点。',{exact:true}).waitFor();
    assert.equal(await group.locator('[data-field=monitor_url]').inputValue(),'https://example.com/keep-hidden');
    assert.equal(await group.locator('[data-field=expected_status]').inputValue(),'0');
    assert.equal(await group.locator('[data-field=retries]').inputValue(),'3');
    await fill(group,'monitor_url','ftp://example.com/health');
    await save.click();
    await group.locator('[data-field=monitor_url][aria-invalid=true]').waitFor();
    assert.match(await group.locator('[data-editor-issue]').textContent(),/HTTP 或 HTTPS/);
    await fill(group,'monitor_url','https://example.com/health');
    await fill(group,'retries','101');
    await save.click();
    await group.locator('[data-field=retries][aria-invalid=true]').waitFor();
    assert.match(await group.locator('[data-editor-issue]').textContent(),/重试次数须为 0 至 100 的整数/);
    assert.equal(await preview.isVisible(),false);
    await fill(group,'monitor_url','ftp://example.com/health');
    await failover.uncheck();
    assert.equal(await group.locator('[data-field=monitor_url],[data-field=expected_status],[data-field=retries]').count(),0);
    await save.click();
    await preview.waitFor({state:'visible'});
    assert.equal(Object.hasOwn(posted['node-groups'][0],'failover'),false);
    assert.equal(posted['node-groups'][0].monitor_url,'ftp://example.com/health');
    assert.equal(posted['node-groups'][0].retries,101);
    await page.locator('#cancel-preview').click();
    await failover.check();
    await fill(group,'monitor_url','https://example.com/health');
    await fill(group,'expected_status','204');
    await fill(group,'retries','0');
    for(const mode of ['智能测速','自动测速']){
      await modes.getByRole('radio',{name:mode,exact:true}).check();
      assert.equal(await failover.count(),0);
      assert.equal(await group.locator('[data-field=monitor_url]').count(),0);
      assert.equal(await group.locator('[data-field=speedtest_url]').isVisible(),true);
      assert.equal(await group.locator('[data-field=interval]').count(),mode==='自动测速'?1:0);
      await modes.getByRole('radio',{name:'手动选择',exact:true}).check();
      assert.equal(await failover.isChecked(),false);
      await failover.check();
      assert.equal(await group.locator('[data-field=retries]').inputValue(),'0');
    }
    const tokens=async(key,label,values)=>{const row=group.getByRole('group',{name:label,exact:true});while(await row.locator('.editor-token button').count())await row.locator('.editor-token button').first().click();for(const value of values){await row.locator(':scope > .btn').click();const entry=row.locator('[data-field="'+key+'"]');await entry.fill(value);await entry.press(key==='keywords'?'Tab':'Enter');}};
    await fill(group,'name','Edited Main');await tokens('keywords','包含关键词',['exp#HK|JP','keyword']);await tokens('exclude_keywords','排除关键词',['expired']);await tokens('subs','订阅来源',['all']);await tokens('include','固定节点 / 组',['N1','DIRECT']);
    await group.locator('[data-field="use_pre_proxy"]').check();await fill(group,'pre_proxy_group','DIRECT');
    const fixed=group.getByRole('group',{name:'固定节点 / 组',exact:true});await fixed.locator(':scope > .btn').click();await fixed.locator('[data-field=include]').fill('REJECT');await fixed.locator('[data-field=include]').press('Enter');
    await group.getByRole('button',{name:'复制',exact:true}).click();assert.equal(await page.locator('.groups .item').count(),2);
    await page.locator('.groups .item').nth(1).getByRole('button',{name:'上移',exact:true}).click();assert.equal(await page.locator('.groups .item').first().locator('[data-field="name"]').inputValue(),'Edited Main 副本');
    await page.locator('.groups .item').first().getByRole('button',{name:'删除',exact:true}).click();await confirmation('删除节点组','确认删除').click();
    await page.getByRole('button',{name:'添加节点组',exact:true}).click();
    const speedtest=page.locator('.groups .item').last();
    await fill(speedtest,'name','Automated backup');
    await speedtest.getByRole('radio',{name:'自动测速',exact:true}).check();
    await fill(speedtest,'speedtest_url','https://example.com/speedtest');
    await fill(speedtest,'interval','900');
    await page.getByRole('button',{name:'添加规则',exact:true}).click();
    const newRule=page.locator('.rules .item').last();
    await newRule.getByLabel('规则类型',{exact:true}).selectOption('rule-set');
    assert.equal(await newRule.locator('[data-field=behavior]').inputValue(),'classical');
    assert.equal(await newRule.locator('[data-field=format]').inputValue(),'auto');
    assert.deepEqual(await newRule.locator('[data-field=format] option').allTextContents(),['auto','yaml','text','mrs']);
    assert.equal(await newRule.locator('[data-field=interval]').inputValue(),'86400');
    await fill(newRule,'name','Rules');
    await fill(newRule,'url','https://example.com/new-rule-set');
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    const issue=await page.locator('[data-editor-issue]').textContent();
    assert.match(issue,/规则集名称重复/);
    assert.doesNotMatch(issue,/DUPLICATE_RULE_PROVIDER/);
    assert.equal(await newRule.locator('[data-field=name]').getAttribute('aria-invalid'),'true');
    assert.equal(await page.locator('#preview-dialog').isVisible(),false);
    await newRule.getByRole('button',{name:'删除',exact:true}).click();
    await confirmation('删除规则','确认删除').click();
    const ruleProvider=page.locator('.rules .item').filter({has:page.locator('[data-field="behavior"]')});await fill(ruleProvider,'name','EditedRules');await fill(ruleProvider,'url','https://example.com/edited-rules');await ruleProvider.locator('[data-field="behavior"]').selectOption('classical');await ruleProvider.locator('[data-field="format"]').selectOption('text');await fill(ruleProvider,'interval','7200');await fill(ruleProvider,'proxy','DIRECT');
    const manual=page.locator('.rules .item').filter({has:page.locator('[data-field="fixrule"]')}).first();await fill(manual,'remark','Edited manual rules');await fill(manual,'fixrule','DOMAIN,edited.example,Edited Main\nMATCH,Edited Main');
    const urlRules=page.locator('.rules .item').last();await fill(urlRules,'url','https://example.com/edited-download');await urlRules.locator('[data-field="isforced"]').uncheck();
    for(const index of [0,1,2])await page.locator('.monitor fieldset').nth(index).locator('[data-field="enable"]').check();
    await fill(page.locator('.monitor fieldset').nth(0),'url','https://example.com/global');await fill(page.locator('.monitor fieldset').nth(0),'retries','3');await fill(page.locator('.monitor fieldset').nth(0),'expected_status','204');
    assert.equal(await page.locator('.monitor fieldset').nth(1).locator('[data-field=cipher]').inputValue(),'aes-128-gcm');
    assert.equal(await page.locator('.source-editor select').evaluateAll(controls=>controls.filter(control=>control.selectedIndex===-1).length),0);
    await fill(page.locator('.monitor fieldset').nth(1),'password','fixture-password-123456');await page.locator('.monitor fieldset').nth(1).locator('[data-field="cipher"]').selectOption('aes-128-gcm');await fill(page.locator('.monitor fieldset').nth(2),'hour','5');
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.locator('#preview-dialog').waitFor({state:'visible'});
    await page.locator('.groups .item').first().locator('[data-field="name"]').evaluate(node=>{node.value='Edited after preview';node.dispatchEvent(new Event('input',{bubbles:true}));});await page.locator('#apply').click();
    await page.getByText('编辑内容已变化，请重新预览后再确认应用。',{exact:true}).waitFor();assert.equal(operation,null,'a stale modal preview cannot apply an older editor document');
    await fill(page.locator('.groups .item').first(),'name','Edited Main');
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.locator('#preview-dialog').waitFor({state:'visible'});await page.locator('#apply').click();await page.getByText('Synthetic editor saved',{exact:true}).waitFor();await ready();
    const [manualGroup,automaticGroup]=config['node-groups'];
    assert.ok(validations>=2);assert.equal(manualGroup.failover,true);assert.equal(manualGroup.monitor_url,'https://example.com/health');
    assert.equal(manualGroup.retries,0);assert.equal(manualGroup.expected_status,'204');assert.equal(Object.hasOwn(manualGroup,'interval'),false);
    assert.equal(automaticGroup.interval,900);assert.equal(Object.hasOwn(automaticGroup,'failover'),false);
    assert.equal(posted.global_monitor.expected_status,'204');assert.equal(posted.restart_cron.hour,5);
    assert.deepEqual(manualGroup.keywords,['exp#HK|JP','keyword']);assert.deepEqual(manualGroup.exclude_keywords,['expired']);assert.deepEqual(manualGroup.include,['N1','DIRECT','REJECT']);
    assert.deepEqual(posted.custom,native.custom);assert.deepEqual(posted.advanced_added,{preserve:true});assert.deepEqual(posted.subs[0].extension,native.subs[0].extension);assert.deepEqual(manualGroup.hidden_feature,{nested:1});assert.equal(posted.subs[0].update_interval,'7h');assert.equal(posted.rules[0].unknown_rule,true);
    assert.equal(await group.getByRole('checkbox',{name:'故障转移',exact:true}).isChecked(),true);
    await page.evaluate(()=>window.ppgwFixtureDocument='original');
    await page.getByRole('button',{name:'查看代理',exact:true}).click();await page.locator('#proxies-page').waitFor();
    assert.equal(await page.evaluate(()=>window.ppgwFixtureDocument),'original');
    await page.locator('#shell-nav [data-route="subscriptions"]').click();await ready();
    await fill(page.locator('.groups .item').first(),'name','Draft survives reauthentication');expired=true;
    await page.waitForFunction(()=>document.querySelector('#shell-status')?.textContent.includes('重新登录'),null,{timeout:45000});expired=false;
    await page.getByRole('button',{name:'重新登录',exact:true}).click();await page.getByLabel('管理密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('#reauth-panel').waitFor({state:'detached'});
    assert.equal(await page.locator('.groups .item').first().locator('[data-field="name"]').inputValue(),'Draft survives reauthentication');
    const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('PaoPaoGateWay')));assert.equal(saved.favorite,'preserve-me');assert.equal(saved.clashAPIConfigs[0].label,'existing-profile');assert.equal(saved.clashAPIConfigs[0].secret,'');assert.equal(saved.clashAPIConfigs[1].label,'second-profile');assert.equal(saved.clashAPIConfigs[1].secret,'');
    await page.evaluate(()=>window.postMessage({type:'ppgw:route',hash:'#/overview'},location.origin));assert.equal(new URL(page.url()).hash,'#/subscriptions','messages from an untrusted source are ignored');
    await page.locator('#shell-nav').getByRole('button',{name:'退出登录',exact:true}).click();await confirmation('离开当前页面','确认离开').click();await page.getByLabel('管理密码',{exact:true}).waitFor();const loggedOut=await page.evaluate(()=>JSON.parse(localStorage.getItem('PaoPaoGateWay')));assert.equal(loggedOut.favorite,'preserve-me');assert.equal(loggedOut.clashAPIConfigs[0].label,'existing-profile');assert.equal(loggedOut.clashAPIConfigs[0].secret,'');
    assert.deepEqual(errors,[]);return{kind:'ppsub',nonSecureHttpLogin:true,actualFormsValidatedByBackend:true,manualFailoverValidatedAndSaved:true,hiddenFieldsPreserved:true,copyMoveDelete:true,mobileToolbarAligned:true,proxyNavigationWithoutReload:true,reauthenticationPreservesDraft:true,storedPreferencesPreserved:true,foreignMessageIgnored:true,lateImportPreservesNewInput:true,reloadInvalidatesImport:true,newerImportWins:true,unmountedImportIgnored:true,validations,pageErrors:errors};
  }finally{await context.close();}
}
(async()=>{const browser=await chromium.launch({channel:'chrome',headless:true});try{const results=[await exercise(browser)];await fs.mkdir(path.join(root,'dist'),{recursive:true});await fs.writeFile(path.join(root,'dist/source-editor-browser-evidence.json'),JSON.stringify({syntheticOnly:true,results},null,2)+'\n');console.log(JSON.stringify({syntheticOnly:true,results}));}finally{await browser.close();}})().then(()=>process.exit(0),error=>{console.error(error);process.exit(1);});
