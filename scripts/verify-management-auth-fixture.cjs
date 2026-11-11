/* Authentication recovery against intercepted synthetic APIs only. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const {createHash}=require('node:crypto');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const {sessionFixture}=require('./browser-session-fixture.cjs');
const root=path.resolve(__dirname,'..'),ui=path.resolve(process.argv[2]||'frontend/dist'),evidence=path.join(root,'dist/management-auth-evidence');
const origin='http://management-auth.invalid',password='SyntheticRecovery8';
const defaults=JSON.parse(execFileSync(process.env.PPGW_PYTHON||'python',['-c',"import sys,json;sys.path.insert(0,'integration');from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini'].update(mode='suburl',suburl='ppsub@http://127.0.0.1/ppsub.json');print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))"],{cwd:root,encoding:'utf8'}));
(async()=>{
  await fs.mkdir(evidence,{recursive:true});
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
    await context.tracing.start({screenshots:true,snapshots:true});
    page.setDefaultTimeout(12000);
    const errors=[],writes=[],jobReads=[];let denied='',job=null,jobState='running',configReads=0,editorReads=0,subscriptionOperation=null;
    let missingSubmit=false,diagnostics={schema:1,runtime:{state:'running'},recovery:{required:false},recent:[]},diagnosticsReads=0,coreGate=null,coreOperation=null,coreSerial=0;
    const delayedResponses=new Map();
    const delayUnauthorized=target=>{
      let enter,release;
      const entered=new Promise(resolve=>{enter=resolve;}),gate=new Promise(resolve=>{release=resolve;});
      delayedResponses.set(target,{enter,gate});return {entered,release};
    };
    const model={...defaults,revision:'a'.repeat(64),assets:[],source:{native_ppsub:true,local_policy:false},mihomo_installed:true,management:{state:'loaded',recent:[]},running_mode:'suburl'};
    let config={version:'PaoPaoGateWay 3.1',subs:[{name:'Synthetic provider',url:'https://example.com/sub'}],'node-groups':[{name:'Main',subs:['all'],include:['DIRECT']}],rules:[{priority:10,fixrule:['MATCH,Main']}]};
    const sessions=sessionFixture({proof:()=>createHash('sha256').update(password).digest('hex')});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url()),target=url.pathname;
      const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if(url.origin!==origin)return route.abort();
      if(target.startsWith('/sniffbox/')){
        const delayed=delayedResponses.get(target);
        if(delayed){delayedResponses.delete(target);delayed.enter();await delayed.gate;return reply({error:'LOGIN_REQUIRED'},401);}
        if(!sessions.authorized(request)||target===denied||denied==='jobs'&&target.includes('/jobs/'))return reply({error:'LOGIN_REQUIRED'},401);
        const body=request.method()==='POST'?request.postDataJSON():null;
        if(body)writes.push({target,body});
        if(target==='/sniffbox/info')return reply({mode:'suburl',ppsub:true,version:'fixture',clash_version:'fixture'});
        if(target==='/sniffbox/settings/config'){configReads++;return reply(model);}
        if(target==='/sniffbox/settings/status')return reply({management:model.management,running_mode:'suburl',source:model.source});
        if(target==='/sniffbox/settings/diagnostics'){diagnosticsReads++;return reply(diagnostics);}
        if(target==='/sniffbox/settings/preview')return reply({action:'reload',changes:body.patch,effects:{core:'reload',services:[]}});
        if(target==='/sniffbox/settings/apply'){if(missingSubmit)return route.abort('failed');job=body;return reply({id:body.id,status:'queued'},202);}
        if(target.startsWith('/sniffbox/settings/jobs/')){
          jobReads.push(target.split('/').at(-1));
          if(!job||target.split('/').at(-1)!==job.id)return reply({error:'JOB_NOT_FOUND'},404);
          if(jobState==='succeeded')for(const [ns,fields]of Object.entries(job.payload.patch))Object.assign(model.settings[ns],fields);
          return reply({id:job.id,status:jobState,result:{runtime:{status:'running'}}});
        }
        if(target==='/sniffbox/settings/gateway-update')return reply({installed:{version:'fixture'},operation:null});
        if(target.startsWith('/sniffbox/subscription/core/')){
          const operation={id:'mihomo_'+(++coreSerial),action:target.split('/').at(-1),status:'running'};
          coreOperation=operation;
          if(coreGate){const gate=coreGate;coreGate=null;gate.enter();await gate.wait;if(gate.reject){coreOperation=null;return reply({error:'LOGIN_REQUIRED'},401);}}
          return reply(operation,202);
        }
        if(target==='/sniffbox/subscription/maintenance')return reply({core:{installed_version:'fixture'},operation:coreOperation});
        if(target==='/sniffbox/subscription/status')return reply({edit_kind:'ppsub',base_revision:model.revision,ppsub_write_allowed:true,operation:subscriptionOperation});
        if(target==='/sniffbox/subscription/providers')return reply({subscriptions:[]});
        if(target==='/sniffbox/subscription/config'){editorReads++;return reply({edit_kind:'ppsub',config,base_revision:model.revision,settings_revision:model.revision,write_allowed:true,node_choices:['DIRECT']});}
        if(target==='/sniffbox/subscription/apply'){
          if(body.dry_run)return reply({action:'reload',changes:{},editor_changed:true});
          config=JSON.parse(body.value);subscriptionOperation={status:'applying',message:'Synthetic pending'};denied='/sniffbox/subscription/status';return reply(subscriptionOperation,202);
        }
        return reply({});
      }
      try{const name=target==='/ui/'?'index.html':path.basename(target);return route.fulfill({body:await fs.readFile(path.join(ui,name)),contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':name.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
    });
    await sessions.install(page);
    const reauthenticate=async()=>{
      await page.getByRole('button',{name:'重新登录',exact:true}).click();
      await page.locator('#reauth-panel input[type=password]').fill(password);
      await page.locator('#reauth-panel').getByRole('button',{name:'登录',exact:true}).click();
      await page.locator('#reauth-panel').waitFor({state:'detached'});
    };
    await page.goto(origin+'/ui/#/settings');
    const field=page.getByLabel('流量记录上限',{exact:true});await field.fill('12345');await field.press('Tab');
    const delayedStatus=delayUnauthorized('/sniffbox/settings/status');await delayedStatus.entered;
    const reads=configReads;denied='/sniffbox/settings/preview';await page.locator('#preview').click();
    await page.getByRole('button',{name:'重新登录',exact:true}).waitFor();assert.equal(await page.locator('#notice a').count(),0);
    denied='';await reauthenticate();
    const statusRecovered=page.waitForResponse(response=>response.url().endsWith('/sniffbox/settings/status')&&response.status()===200);
    delayedStatus.release();await statusRecovered;
    assert.equal(await page.getByRole('button',{name:'重新登录',exact:true}).count(),0,'old-session 401 must not expire the new login');
    assert.equal(await field.inputValue(),'12345');assert.equal(configReads,reads);assert.equal(writes.length,0);
    assert.equal(await page.getByRole('tab',{name:'访问与统计',exact:true}).getAttribute('aria-selected'),'true');
    await page.locator('#preview').click();await page.locator('#preview-dialog').waitFor({state:'visible'});denied='jobs';await page.locator('#apply').click();
    await page.getByRole('button',{name:'重新登录',exact:true}).waitFor();
    const pending=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('lovestgateway-job')));
    assert.equal(await field.isDisabled(),true);assert.equal(await page.locator('#preview').isDisabled(),true);
    await page.locator('#shell-nav [data-route=overview]').click();assert.ok(page.url().endsWith('#/settings'));
    denied='';await reauthenticate();await page.waitForFunction(()=>document.querySelector('#notice')?.textContent.includes('已重新登录'));
    await page.waitForTimeout(1700);assert.ok(jobReads.length>0);assert.ok(jobReads.every(id=>id===pending.id));assert.equal(await field.isDisabled(),true);
    jobState='succeeded';await page.getByText('配置已应用，运行状态验收通过。',{exact:true}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('#setting-ini-max_rec').disabled);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-job')),null);
    assert.equal(writes.filter(row=>row.target.endsWith('/settings/apply')).length,1);

    // A definite submit-401 must keep the new draft and must not restart the old task.
    await field.fill('12346');await field.press('Tab');await page.locator('#preview').click();await page.locator('#preview-dialog').waitFor({state:'visible'});
    denied='/sniffbox/settings/apply';await page.locator('#apply').click();await page.getByRole('button',{name:'重新登录',exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-job')),null);denied='';await reauthenticate();
    assert.equal(await field.inputValue(),'12346');assert.equal(writes.filter(row=>row.target.endsWith('/settings/apply')).length,1);await page.locator('#discard').click();

    // A write rejected by the old session is never replayed after another request triggers login.
    await field.fill('12347');await field.press('Tab');await page.locator('#preview').click();await page.locator('#preview-dialog').waitFor({state:'visible'});
    const delayedApply=delayUnauthorized('/sniffbox/settings/apply');await page.locator('#apply').click();await delayedApply.entered;
    denied='/sniffbox/settings/status';await page.getByRole('button',{name:'重新登录',exact:true}).waitFor();denied='';await reauthenticate();
    delayedApply.release();await page.getByText('登录状态已更新，请重新预览后再提交。',{exact:true}).waitFor();
    assert.equal(await field.inputValue(),'12347');assert.equal(await field.isDisabled(),false);
    assert.equal(await page.getByRole('button',{name:'重新登录',exact:true}).count(),0);
    assert.equal(writes.filter(row=>row.target.endsWith('/settings/apply')).length,1);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-job')),null);await page.locator('#discard').click();

    // An unaccepted request remains locked until read-only diagnostics and explicit acknowledgement.
    missingSubmit=true;await field.fill('12348');await field.press('Tab');await page.locator('#preview').click();await page.locator('#preview-dialog').waitFor({state:'visible'});await page.locator('#apply').click();
    const review=page.locator('[data-unknown-settings-job]');await review.getByRole('button',{name:'核对任务状态',exact:true}).waitFor();
    const missingRecord=await page.evaluate(()=>sessionStorage.getItem('lovestgateway-job')),writesBeforeReview=writes.length;
    await page.locator('#shell-nav [data-route=overview]').click();assert.ok(page.url().endsWith('#/settings'));
    assert.equal(await review.isVisible(),true,'navigation feedback must not hide the recovery controls');
    await page.reload();await review.getByRole('button',{name:'核对任务状态',exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-job')),missingRecord);assert.equal(await field.isDisabled(),true);
    diagnostics.recovery.required=true;await review.getByRole('button',{name:'核对任务状态',exact:true}).click();await review.getByText(/仍存在待恢复事务/).waitFor();
    assert.equal(await review.getByRole('button',{name:'仅本浏览器停止追踪',exact:true}).count(),0);
    diagnostics={schema:1};await review.getByRole('button',{name:'重新核对任务状态',exact:true}).click();await review.getByText(/诊断状态无法确认/).waitFor();
    diagnostics={schema:1,runtime:{state:'running'},recovery:{required:false},recent:[{source:'settings',status:'running'}]};
    await review.getByRole('button',{name:'重新核对任务状态',exact:true}).click();await review.getByText(/仍有请求或后台任务正在处理/).waitFor();
    diagnostics.recent=[];await review.getByRole('button',{name:'重新核对任务状态',exact:true}).click();
    const release=review.getByRole('button',{name:'仅本浏览器停止追踪',exact:true}),ack=review.getByRole('checkbox');await release.waitFor();assert.equal(await release.isDisabled(),true);
    assert.doesNotMatch(await review.textContent(),/null|undefined/);
    await page.setViewportSize({width:320,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'unknown task review must fit a narrow screen');
    await review.screenshot({path:path.join(evidence,'unknown-task-mobile.png')});await page.setViewportSize({width:1440,height:1000});
    await ack.check();const readsBeforeRelease=diagnosticsReads;diagnostics.recovery.required=true;await release.click();await review.getByText(/仍存在待恢复事务/).waitFor();
    assert.equal(diagnosticsReads,readsBeforeRelease+1,'release must recheck the server at the moment of confirmation');
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-job')),missingRecord);assert.equal(await field.isDisabled(),true);
    diagnostics.recovery.required=false;await review.getByRole('button',{name:'重新核对任务状态',exact:true}).click();await release.waitFor();await ack.check();await release.click();
    await page.waitForFunction(()=>!sessionStorage.getItem('lovestgateway-job')&&!document.querySelector('#setting-ini-max_rec').disabled);
    assert.equal(await review.isVisible(),false);assert.equal(await page.locator('#reload').isDisabled(),false);assert.equal(writes.length,writesBeforeReview,'review and release are strictly read-only');
    missingSubmit=false;

    await page.locator('#shell-nav [data-route=subscriptions]').click();
    const group=page.locator('.groups .item [data-field=name]').first();await group.fill('Draft remains');await group.press('Tab');
    const delayedProviders=delayUnauthorized('/sniffbox/subscription/providers');await delayedProviders.entered;
    const beforeEditor=editorReads;denied='/sniffbox/subscription/apply';await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.getByRole('button',{name:'重新登录',exact:true}).waitFor();denied='';await reauthenticate();
    const providersRecovered=page.waitForResponse(response=>response.url().endsWith('/sniffbox/subscription/providers')&&response.status()===200);
    delayedProviders.release();await providersRecovered;
    assert.equal(await page.getByRole('button',{name:'重新登录',exact:true}).count(),0);
    assert.equal(await group.inputValue(),'Draft remains');assert.equal(editorReads,beforeEditor);
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.locator('#preview-dialog').waitFor({state:'visible'});await page.locator('#apply').click();
    await page.getByRole('button',{name:'重新登录',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'保存并应用',exact:true}).isDisabled(),true);
    subscriptionOperation={status:'applied',message:'Synthetic completed'};denied='';await reauthenticate();
    await page.getByText('Synthetic completed',{exact:true}).waitFor();assert.equal(await group.inputValue(),'Draft remains');
    assert.equal(writes.filter(row=>row.target.endsWith('/subscription/apply')&&!row.body.dry_run).length,1);

    // Keep the local submission lock before a Mihomo task ID has returned, including rejected writes.
    await page.locator('#shell-nav [data-route=settings]').click();
    const maintenanceReady=page.waitForResponse(response=>response.url().endsWith('/subscription/maintenance')&&response.status()===200);
    await page.getByRole('tab',{name:'维护与 API',exact:true}).click();await maintenanceReady;
    const rollback=page.getByRole('button',{name:'回退上一版本',exact:true}),check=page.getByRole('button',{name:'检查稳定版',exact:true});
    const coreWritesBefore=writes.filter(row=>row.target.includes('/core/')).length;
    for(const reject of [false,true]){
      let enter,release;
      const entered=new Promise(resolve=>{enter=resolve;}),wait=new Promise(resolve=>{release=resolve;});coreGate={enter,wait,reject};
      await rollback.click();await entered;assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-core-job')),null);
      denied='/sniffbox/settings/status';await page.getByRole('button',{name:'重新登录',exact:true}).waitFor();denied='';
      const resumed=page.waitForResponse(response=>response.url().endsWith('/settings/status')&&response.status()===200);await reauthenticate();await resumed;
      assert.equal(await rollback.isDisabled(),true);assert.equal(await check.isDisabled(),true);
      await page.locator('#shell-nav [data-route=overview]').click();assert.ok(page.url().endsWith('#/settings'));
      assert.equal(writes.filter(row=>row.target.includes('/core/')).length,coreWritesBefore+(reject?2:1));
      release();
      if(reject){await page.getByText('登录状态已更新，请重新预览后再提交。',{exact:true}).waitFor();}
      else{
        await page.waitForFunction(()=>!!sessionStorage.getItem('lovestgateway-core-job'));
        assert.equal(JSON.parse(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-core-job'))).id,coreOperation.id);
        coreOperation.status='complete';await page.getByText('核心维护已完成。',{exact:true}).waitFor();
      }
      await page.waitForFunction(()=>[...document.querySelectorAll('#content button')].some(button=>button.textContent==='检查稳定版'&&!button.disabled));
      assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-core-job')),null);
      assert.equal(await page.getByRole('button',{name:'重新登录',exact:true}).count(),0);
    }
    assert.deepEqual(errors,[]);
    await page.screenshot({path:path.join(evidence,'recovered.png')});
    const report={syntheticOnly:true,immediateReauth:true,settingsDraftPreserved:true,sameTaskResumed:true,pendingTaskLocked:true,definiteRejectionKeepsDraft:true,subscriptionDraftPreserved:true,subscriptionTaskResumed:true,late401KeepsNewSession:true,unknownTaskExplicitRelease:true,freshDiagnosticsBeforeRelease:true,recoveryAndActiveTaskBlockRelease:true,coreSubmissionLockedAcrossLogin:true,coreRejectedSubmissionUnlocks:true,noMutationReplay:true,pageErrors:errors};
    await fs.writeFile(path.join(evidence,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
    await context.tracing.stop();await context.close();
  }catch(error){
    const context=browser.contexts()[0],page=context?.pages()[0];
    await page?.screenshot({path:path.join(evidence,'failure.png'),fullPage:true}).catch(()=>{});
    await context?.tracing.stop({path:path.join(evidence,'failure-trace.zip')}).catch(()=>{});
    throw error;
  }finally{await browser.close();}
})().then(()=>process.exit(0),error=>{console.error(error);process.exit(1);});
