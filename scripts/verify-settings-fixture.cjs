/* Browser acceptance against synthetic APIs; never connects to a live gateway. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PPGW_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, 'dist/settings-ui'));
const origin = 'http://127.0.0.1:18885';
function python(code, value) {
  return JSON.parse(execFileSync(process.env.PPGW_PYTHON || 'python', ['-c',
    "import sys,json;sys.path.insert(0,'integration');" + code], {cwd: root, input: JSON.stringify(value), encoding: 'utf8'}));
}
const defaults = python("from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini'].update(mode='suburl',suburl='fixture-private-source',net_rec=True);s['service']['PPGW_INTERFACE']='ens33';print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))", null);
const raw = {
  proxies: [{name: 'N1', type: 'socks5', server: '127.0.0.1', port: 9, password: 'private-node-credential'}],
  dns: {nameserver: ['1.1.1.1']}, secret: 'private-core-credential',
  'proxy-groups': [{name: 'Main', type: 'select', proxies: ['N1', 'DIRECT'], hidden: true}],
  'rule-providers': {Local: {type: 'inline', behavior: 'domain', payload: ['example.com']}},
  rules: ['DOMAIN-KEYWORD,foo#bar,DIRECT', 'MATCH,Main'],
};
const editorConfig = python("from clash_ppsub import editor_config;d=json.load(sys.stdin);print(json.dumps(editor_config(d,'https://example.com/sub')[0]))", raw);

async function main() {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
    const page = await context.newPage(), errors = [], submitted = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem('PaoPaoGateWay', JSON.stringify({clashAPIConfigs: [{secret: 'fixture-token'}]}));
      if (!localStorage.getItem('ppgw-theme')) localStorage.setItem('ppgw-theme', 'light');
    });
    const model = {...defaults, revision: 'a'.repeat(64), assets: [], source: {native_ppsub: false, local_policy: true}, mihomo_installed: true};
    let stale = false, fail = false, expireLogin = false, runningJob, polls = 0, configDelay = 0, coreFailed = true, coreLimited = false, localUpload = null, localInstalled = false, localOperations = [];
    let managementOffline=false, managementPolls=0, configReads=0, rechecks=0;
    let coreSerial=1, coreId='mihomo_1', coreRestart=false, coreReads=0, coreMutations=0, coreGate=false, coreExpired=false, coreSuperseded=false;
    const management={state:'unknown',policy_state:'saved_unconfirmed',recent:[],latest:null};
    model.management=management;
    model.latest_job={id:'f'.repeat(32),kind:'config',status:'failed',error:'MANAGEMENT_BUSY',created_at:1700000000,finished_at:1700000001};
    await page.route(origin + '/**', async route => {
      const req = route.request(), target = new URL(req.url()).pathname;
      if (target === '/sniffbox/subscription/core/update') {
        assert.equal(req.headers().authorization, 'Bearer fixture-token');
        const body = req.postDataJSON(); localOperations.push(body.operation);
        if (body.operation === 'begin') { localUpload = {...body, bytes: Buffer.alloc(0)}; return route.fulfill({contentType:'application/json',body:JSON.stringify({received:0,chunk_bytes:16384})}); }
        assert.equal(body.id, localUpload.id);
        if (body.operation === 'chunk') { assert.equal(body.offset, localUpload.bytes.length);localUpload.bytes=Buffer.concat([localUpload.bytes,Buffer.from(body.data,'base64')]); return route.fulfill({contentType:'application/json',body:JSON.stringify({received:localUpload.bytes.length})}); }
        assert.equal(body.operation,'commit');assert.equal(localUpload.bytes.length,localUpload.size);
        assert.equal(crypto.createHash('sha256').update(localUpload.bytes).digest('hex'),localUpload.sha256);
        localInstalled=true;coreId='mihomo_'+(++coreSerial);return route.fulfill({contentType:'application/json',body:JSON.stringify({id:coreId,status:'running',action:'install-local'})});
      }
      if (target === '/sniffbox/subscription/core/check' || target === '/sniffbox/subscription/core/rollback' || target === '/sniffbox/subscription/maintenance') {
        assert.equal(req.headers().authorization, 'Bearer fixture-token');
        if (!target.endsWith('/maintenance')) { coreId='mihomo_'+(++coreSerial);coreMutations++;coreReads=0; }
        if (target.endsWith('/maintenance') && coreRestart) {
          coreReads++;
          if (coreExpired) return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({error:'LOGIN_REQUIRED'})});
          if (coreReads<=2) return route.abort('failed');
          if (coreReads===3) return route.fulfill({status:503,contentType:'application/json',body:'{}'});
        }
        const data = !target.endsWith('/maintenance') ? {id:coreId,status: 'running', action: target.endsWith('/rollback')?'rollback':'check'} : {
          core: {installed_version: localInstalled ? localUpload.version : null, latest_version: coreFailed ? null : 'v1.2.3',
            selection:model.settings.maintenance.core_variant, architecture:'amd64-'+(model.settings.maintenance.core_variant==='auto'?'v3':model.settings.maintenance.core_variant), installed_architecture:localInstalled?'amd64-v1':null,
            cpu_detection:'glibc-hwcaps', selection_supported:true, variant_change_available:!coreFailed},
          operation: coreRestart ? {id:coreSuperseded?'mihomo_99999':coreId,status:coreGate?'running':'complete',action:'rollback'} : localInstalled ? {id:coreId,status:'complete',action:'install-local'} : coreLimited ? {id:coreId,status: 'failed', error: 'RELEASE_RATE_LIMITED', retry_at: 2000000000, message: '限流'} : coreFailed ? {id:coreId,status: 'failed', error: 'RELEASE_NETWORK_TIMEOUT',
            message: '连接 GitHub 超时，请检查这台 Debian 的外网连接后重试。'} : {id:coreId,status: 'complete', action: 'check'},
        };
        return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
      }
      if (target === '/sniffbox/subscription/status') return route.fulfill({contentType: 'application/json', body: JSON.stringify({ready: true, edit_kind: 'clash_patch', ppsub_write_allowed: true, base_revision: model.revision})});
      if (target === '/sniffbox/subscription/config') return route.fulfill({contentType: 'application/json', body: JSON.stringify({config: editorConfig, edit_kind: 'clash_patch', snapshot_id: 'fixture-snapshot', base_revision: model.revision, settings_revision: model.revision, write_allowed: true, node_choices: ['N1'], notice: 'Synthetic editor'})});
      if (target.startsWith('/sniffbox/settings/')) {
        assert.equal(req.headers().authorization, 'Bearer fixture-token');
        const body = req.method() === 'POST' ? req.postDataJSON() : undefined;
        let data, status = 200;
        if (target.endsWith('/recheck')) {
          assert.deepEqual(JSON.parse(route.request().postData()),{});rechecks++;
          management.state='loaded';management.verification={current:true,result:'verified',checked_at:1793863200,checks:['core_ini','service_environment','management_services']};
          data=management.verification;
        } else if (target.endsWith('/status')) {
          managementPolls++;
          if(managementOffline)return route.abort('failed');
          data={management:coreSuperseded?{...management,recent:[{id:coreId,source:'mihomo',status:coreGate?'running':'complete',action:'rollback'}]}:management,runtime:model.runtime||{status:'staged'},running_mode:model.running_mode||null,source:{local_policy:true}};
        }
        else if (target.endsWith('/config')) { configReads++; if (configDelay) await new Promise(resolve => setTimeout(resolve, configDelay)); data = model; }
        else if (target.endsWith('/preview')) {
          if (stale) { status = 409; data = {error: 'CONFIG_REVISION_CONFLICT'}; }
          else {
            const action = body.restart || body.patch.service || body.patch.ini?.mode ? 'restart' : body.patch.ini ? 'reload' : 'none';
            data = {action, changes: structuredClone(body.patch), effects: {core: action, services: [],
              web_listen: body.patch.service?.PPGW_WEB_LISTEN, access_changed: !!body.patch.ini?.clash_web_password}};
          }
        } else if (target.endsWith('/apply')) {
          submitted.push(body); runningJob = body; polls = 0; status = 202; data = {id: body.id, status: 'queued'};
        } else if (target.includes('/jobs/')) {
          if (expireLogin) { status = 401; data = {error: 'LOGIN_REQUIRED'}; }
          else if (++polls === 1) { status = 503; data = {error: 'OPERATION_FAILED'}; }
          else if (fail) data = {id: runningJob.id, status: 'failed', error: 'APPLY_FAILED_ROLLED_BACK'};
          else {
            data = {id: runningJob.id, status: 'succeeded', result: {runtime: {status: 'running'}}};
            if (runningJob.kind === 'config') for (const [ns, fields] of Object.entries(runningJob.payload.patch)) {
              for (const [key, value] of Object.entries(fields)) model.settings[ns][key] = model.schema[ns][key].secret ? '********' : value;
            }
            if (runningJob.kind === 'asset') model.assets.push(runningJob.payload.name);
          }
        } else throw Error('Unexpected API ' + target);
        return route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)});
      }
      // The shared authenticated shell also reads its runtime capability state.
      if (target.startsWith('/sniffbox/')) return route.fulfill({contentType: 'application/json',
        body: JSON.stringify({mode: 'suburl', clash_version: 'fixture', ppsub: false})});
      const filename = path.join(output, target === '/ui/' || target === '/ui' ? 'index.html' : path.basename(target));
      try {
        const body = await fs.readFile(filename);
        return route.fulfill({contentType: target.endsWith('.js') ? 'application/javascript' : target.endsWith('.css') ? 'text/css' : target.endsWith('.svg') ? 'image/svg+xml' : 'text/html', body});
      } catch { return route.fulfill({status: 404, body: ''}); }
    });
    const blur = async (locator, value) => { await locator.fill(value); await locator.press('Tab'); };
    const apply = async () => { await page.locator('#preview').click(); await page.locator('#preview-dialog').waitFor({state: 'visible'}); await page.locator('#apply').click(); };
    const saved = () => page.getByText('配置已应用，运行状态验收通过。', {exact: true}).waitFor();
    await page.goto(origin + '/ui/gateway.html#/settings');
    await page.locator('.ppgw-management [role=status]').waitFor();
    assert.equal(await page.locator('#notice').isVisible(),false,'a historical failure must not become a current busy alert');
    const originalHeading=await page.locator('.ppgw-management [role=status]').textContent();
    const initialReads=configReads;
    managementOffline=true;
    await page.getByText('运行状态暂时无法读取，正在重新连接…',{exact:true}).waitFor({timeout:15000});
    managementOffline=false;
    await page.waitForFunction(text=>document.querySelector('.ppgw-management [role=status]')?.textContent===text,originalHeading,{timeout:15000});
    assert.equal(configReads,initialReads,'status polls must not read full config');
    assert.ok(managementPolls>=2);
    const pastFailure={...model.latest_job,source:'settings',phase:'complete',updated_at:1700000001};
    management.state='saved_pending';management.recent=[pastFailure];
    model.runtime={configuration_state:'loaded',last_load_result:'success'};
    await page.locator('#reload').click();
    await page.waitForFunction(()=>document.querySelector('.ppgw-management [role=status]')?.textContent.includes('核心配置已加载'));
    assert.equal(await page.locator('#notice').isVisible(),false);
    assert.match(await page.locator('.ppgw-management [role=status]').textContent(),/核心配置已加载.*保存版本与运行状态待核对/);
    assert.doesNotMatch(await page.locator('.ppgw-management [role=status]').textContent(),/尚未确认加载|已核对当前运行配置/);
    const readsBeforeCheck=configReads;
    const aclInput=page.getByLabel('管理访问范围',{exact:true}),aclWarning=page.locator('[data-admin-acl-warning]'),savedAcl=await aclInput.inputValue();
    await aclInput.fill('');assert.equal(await aclWarning.isVisible(),true);
    await aclInput.fill('192.0.2.0/24');assert.equal(await aclWarning.isVisible(),false);
    await aclInput.fill(savedAcl);
    const draftInput=page.getByLabel('流量记录上限',{exact:true}),savedInput=await draftInput.inputValue();
    await draftInput.fill('12345');
    await page.getByRole('button',{name:'重新核对',exact:true}).click();
    await page.getByText(/最近核对：.*通过.*网关配置、启动环境、管理服务/).waitFor();
    assert.equal(rechecks,1);assert.equal(configReads,readsBeforeCheck,'recheck must preserve the current form and drafts');
    assert.equal(await draftInput.inputValue(),'12345');await draftInput.fill(savedInput);
    assert.match(await page.locator('.ppgw-management [role=status]').textContent(),/已核对当前运行配置与管理服务/);
    management.verification={...management.verification,current:false};
    await page.waitForFunction(()=>document.querySelector('.ppgw-management')?.textContent.includes('运行状态已变化，等待重新核对'),null,{timeout:15000});
    await page.screenshot({path:path.join(root,'dist/historical-alert-20261105/current-state.png')});
    model.recovery_required=true;await page.locator('#reload').click();
    await page.getByText('上一次事务需要恢复。请在宿主运行 sudo ppgw-admin recover --yes，再刷新页面。',{exact:true}).waitFor();
    model.recovery_required=false;const rechecked=page.waitForResponse(response=>new URL(response.url()).pathname==='/sniffbox/settings/config');await page.locator('#reload').click();await rechecked;
    assert.equal(await page.locator('#notice').isVisible(),false,'an actual recovery warning clears only after a new read confirms no recovery is required');
    await page.getByRole('tab',{name:'最近操作',exact:true}).click();
    assert.equal(await page.locator('.management-history').isVisible(),true);
    assert.equal(await page.locator('.management-history details').count(),0);
    await page.getByText(/该次操作因其他管理任务占用未执行/).waitFor();
    await page.screenshot({path:path.join(root,'dist/settings-recent-desktop.png')});
    await page.getByRole('tab',{name:'访问与统计',exact:true}).click();
    await page.getByLabel('流量记录上限', {exact: true}).waitFor();
    assert.equal(new URL(page.url()).pathname, '/ui/');
    await page.getByRole('heading', {name:'修改管理密码',exact:true}).waitFor();
    await page.screenshot({path:path.join(root,'dist/password-settings-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844}); await page.waitForTimeout(400);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(root,'dist/password-settings-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000}); await page.waitForTimeout(400);
    let shell = await page.locator('#shell-nav').elementHandle();
    const navigation = page.locator('#shell-nav');
    assert.equal(await navigation.count(), 1);
    assert.equal(await navigation.locator('a[href="#/settings"].active').count(), 1);
    await page.getByRole('tab', {name: '维护与 API', exact: true}).click();
    const variant = page.getByLabel('Mihomo CPU 型号',{exact:true});
    assert.equal(await variant.inputValue(),'auto');
    await variant.selectOption('v1');
    await page.getByRole('button', {name: '检查稳定版', exact: true}).click();
    assert.equal(await page.locator('#notice').innerText(),'请先完成当前配置操作。');
    await page.locator('#preview').click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});
    assert.match(await page.locator('#preview-content').innerText(),/无需重启/);
    await page.locator('#apply').click();await saved();
    assert.equal(submitted.at(-1).payload.patch.maintenance.core_variant,'v1');
    submitted.length=0;
    await page.getByRole('tab', {name: '维护与 API', exact: true}).click();
    assert.equal(await page.getByLabel('Mihomo CPU 型号',{exact:true}).inputValue(),'v1');
    await page.getByRole('button', {name: '检查稳定版', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#notice')?.textContent.includes('RELEASE_NETWORK_TIMEOUT'));
    assert.match(await page.locator('#maintenance-state').innerText(), /未取得版本信息.*连接 GitHub 超时/);
    assert.doesNotMatch(await page.locator('#maintenance-state').innerText(), /待检查/);
    await page.waitForFunction(() => document.querySelector('#notice')?.textContent.includes('连接 GitHub 超时'));
    assert.match(await page.locator('#notice').innerText(), /连接 GitHub 超时/);
    coreLimited = true;
    await page.getByRole('button', {name: '检查稳定版', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#maintenance-state')?.textContent.includes('查询受限'));
    assert.match(await page.locator('#notice').innerText(), /已安装的 Mihomo 可继续使用/);
    assert.match(await page.locator('#notice').innerText(), /后重试/);
    coreLimited = false;
    coreFailed = false;
    await page.getByRole('button', {name: '检查稳定版', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#maintenance-state')?.textContent.includes('v1.2.3'));
    assert.equal(await page.locator('#notice').innerText(), '核心维护已完成。');
    assert.match(await page.locator('#maintenance-state').innerText(),/指定目标 amd64-v1.*可手动切换同版本 CPU 型号/);
    await page.getByText('本地文件更新（手动）', {exact:true}).click();
    const localArchive=Buffer.alloc(1024*1024+17,123), localSha=crypto.createHash('sha256').update(localArchive).digest('hex');
    await page.getByLabel('Mihomo 官方 .gz 文件',{exact:true}).setInputFiles({name:'mihomo-linux-amd64-compatible-v1.2.4.gz',mimeType:'application/gzip',buffer:localArchive});
    assert.equal(await page.getByLabel('核心版本',{exact:true}).inputValue(),'v1.2.4');
    await page.getByLabel('官方 SHA256',{exact:true}).fill(localSha);
    await page.getByRole('button',{name:'上传并安装 Mihomo',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#maintenance-state')?.textContent.includes('当前 Mihomo v1.2.4'));
    assert.deepEqual(localOperations,['begin',...Array(Math.ceil(localArchive.length/16384)).fill('chunk'),'commit']);
    assert.equal(await page.locator('#notice').innerText(),'核心维护已完成。');
    assert.equal(await page.getByRole('button',{name:'上传并安装 Mihomo',exact:true}).isEnabled(),true);
    // Core restarts close the same HTTP listener used by maintenance polling.
    // Recover GET failures without repeating the mutation or losing its identity.
    coreRestart=true;coreGate=true;
    const mutationsBefore=coreMutations;
    await page.getByRole('button',{name:'回退上一版本',exact:true}).click();
    await page.getByText('网关正在重启或暂时断连，正在重新连接并查询维护结果；请勿重复安装。',{exact:true}).waitFor();
    const coreRecord=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('lovestgateway-core-job')));
    assert.deepEqual(coreRecord,{id:coreId,action:'rollback'});
    assert.equal(await page.getByRole('button',{name:'回退上一版本',exact:true}).isDisabled(),true);
    assert.doesNotMatch(await page.locator('#notice').innerText(),/Failed to fetch/);
    await page.waitForFunction(()=>document.querySelector('#notice')?.textContent.includes('维护任务正在后台执行'),{},{timeout:20000});
    assert.ok(coreReads>=4);
    // Reload after the worker finished must still query the saved task.
    coreGate=false;coreSuperseded=true;
    page.once('dialog',dialog=>dialog.accept());await page.reload();
    await page.getByText('核心维护已完成。',{exact:true}).waitFor();
    shell=await page.locator('#shell-nav').elementHandle();
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-core-job')),null);
    assert.equal(coreMutations,mutationsBefore+1);
    await page.getByRole('tab',{name:'维护与 API',exact:true}).click();
    // Authentication can expire during reconnect. Relogin/read recovery never resubmits.
    coreReads=0;coreGate=true;coreSuperseded=false;coreExpired=true;
    await page.getByRole('button',{name:'回退上一版本',exact:true}).click();
    await page.getByText('登录已失效，请先返回管理面板重新登录。',{exact:true}).waitFor();
    assert.ok(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-core-job')));
    coreExpired=false;coreGate=false;
    await page.getByText('核心维护已完成。',{exact:true}).waitFor({timeout:20000});
    assert.equal(coreMutations,mutationsBefore+2);
    coreRestart=false;coreSuperseded=false;
    await page.screenshot({path:path.join(root,'dist/mihomo-manual-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(root,'dist/mihomo-manual-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.waitForTimeout(400);
    await page.getByRole('tab', {name: '访问与统计', exact: true}).click();
    await blur(page.getByLabel('流量记录上限', {exact: true}), '8999');
    await navigation.getByRole('link', {name: '订阅与策略', exact: true}).click();
    await page.getByRole('button', {name: '保存并应用', exact: true}).waitFor();
    assert.equal(await page.locator('#setting-ini-suburl').count(), 0);
    assert.equal(await shell.evaluate(node => node === document.querySelector('#shell-nav')), true);
    assert.equal(await navigation.locator('a[href="#/subscriptions"].active').count(), 1);
    await navigation.getByRole('link', {name: '网关设置', exact: true}).click();
    assert.equal(await page.getByLabel('流量记录上限', {exact: true}).inputValue(), '8999');
    page.once('dialog', dialog => dialog.dismiss());
    await navigation.getByRole('link', {name: '概览', exact: true}).click();
    assert.equal(new URL(page.url()).hash, '#/settings');
    assert.equal(await page.getByLabel('流量记录上限', {exact: true}).inputValue(), '8999');
    await page.locator('#discard').click();
    await blur(page.getByLabel('流量记录上限', {exact: true}), '9000');
    await blur(page.getByLabel('管理密码', {exact: true}), 'seven77');
    await page.locator('#preview').click();
    await page.getByText('管理密码至少需要 8 个字符。', {exact: true}).waitFor();
    assert.equal(await page.locator('#preview-dialog').isVisible(), false);
    await blur(page.getByLabel('管理密码', {exact: true}), 'Fx8_pass');
    await page.getByRole('heading', {name: '修改管理密码', exact: true}).waitFor();
    await blur(page.getByLabel('确认新管理密码', {exact: true}), 'fixture-mismatching-password');
    await page.locator('#preview').click();
    await page.getByText('两次输入的管理密码不一致，请重新确认。', {exact: true}).waitFor();
    assert.equal(await page.locator('#preview-dialog').isVisible(), false);
    await blur(page.getByLabel('确认新管理密码', {exact: true}), 'Fx8_pass');
    await page.locator('#preview').click();
    await page.locator('#preview-dialog').waitFor({state: 'visible'});
    assert.match(await page.locator('#preview-content').textContent(), /热更新/);
    assert.doesNotMatch(await page.locator('#preview-content').textContent(), /Fx8_pass/);
    await page.locator('#apply').click(); await saved();
    assert.equal(submitted[0].payload.patch.ini.max_rec, 9000);
    assert.equal(await page.getByLabel('管理密码', {exact: true}).inputValue(), '');
    assert.equal(await page.getByLabel('确认新管理密码', {exact: true}).inputValue(), '');
    assert.equal(await page.getByLabel('流量记录上限', {exact: true}).isEnabled(), true);
    assert.doesNotMatch(await page.evaluate(() => JSON.stringify(sessionStorage)), /Fx8_pass/);
    stale = true;
    await blur(page.getByLabel('流量记录上限', {exact: true}), '9001');
    await page.locator('#preview').click(); await page.getByText('配置已被其他操作修改。请刷新配置后重新编辑。', {exact: true}).waitFor();
    assert.equal(await page.locator('#preview-dialog').isVisible(), false);
    stale = false; await page.locator('#discard').click();
    await page.getByRole('tab', {name: '维护与 API', exact: true}).click();
    await page.getByLabel('API 实例名称', {exact: true}).waitFor();
    for (const label of ['检查稳定版', '安装 / 更新 Mihomo', '回退上一版本']) {
      assert.equal(await page.getByRole('button', {name: label, exact: true}).isVisible(), true);
    }
    assert.doesNotMatch(await page.locator('.ppgw-gateway').innerText(), /HomeNet|HNM/i);
    for (const title of ['网络与出站', '高级选项', '访问与统计']) await page.getByRole('tab', {name: title, exact: true}).click();
    await page.evaluate(() => scrollTo(0, 0));
    const appearance = navigation.getByRole('group', {name:'外观',exact:true});
    const themeSettled = color => page.waitForFunction(expected =>
      getComputedStyle(document.querySelector('.toggle-field input:checked')).backgroundColor === expected, color);
    await appearance.getByRole('button',{name:'暗色',exact:true}).click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    assert.equal(await page.locator('.ppgw-gateway').evaluate(node => getComputedStyle(node).color === getComputedStyle(document.body).color), true);
    await themeSettled('rgb(249, 131, 42)');
    await page.screenshot({path: path.join(root, 'dist/settings-unified-dark.png'), fullPage: true});
    await appearance.getByRole('button',{name:'亮色',exact:true}).click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await themeSettled('rgb(249, 131, 42)');
    await page.screenshot({path: path.join(root, 'dist/settings-unified-light.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForFunction(() => document.querySelector('#shell-nav').getBoundingClientRect().right <= 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: path.join(root, 'dist/settings-unified-mobile.png'), fullPage: true});
    await page.locator('#shell-menu').click();
    await navigation.getByRole('link', {name: '订阅与策略', exact: true}).click();
    await page.getByRole('button', {name: '保存并应用', exact: true}).waitFor();
    assert.equal(await page.locator('#setting-ini-suburl').count(), 0);
    await page.waitForFunction(() => document.querySelector('#shell-nav').getBoundingClientRect().right <= 0);
    await page.locator('#shell-menu').click();
    await navigation.getByRole('link', {name: '网关设置', exact: true}).click();
    await page.getByLabel('流量记录上限', {exact: true}).waitFor();
    await page.setViewportSize({width: 320, height: 740});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await blur(page.getByLabel('流量记录上限', {exact: true}), '8998');
    await page.locator('#savebar').scrollIntoViewIfNeeded();
    assert.equal(await page.locator('#savebar').evaluate(node => {const box = node.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight;}), true);
    await page.locator('#discard').click();
    await page.setViewportSize({width: 1440, height: 1000});
    await page.getByRole('link', {name: '订阅与策略', exact: true}).click();
    await page.getByRole('button', {name: '保存并应用', exact: true}).waitFor();
    await page.getByRole('button', {name: '恢复订阅原策略', exact: true}).click();
    await page.locator('#preview-dialog').waitFor({state: 'visible'});
    await page.locator('#apply').click(); await saved();
    assert.equal(submitted[1].kind, 'policy_reset');
    await page.getByRole('tab',{name:'配置来源',exact:true}).click();
    await page.getByRole('button',{name:'切换配置来源',exact:true}).click();await page.locator('#source-type').selectOption('yaml');
    await page.locator('#asset-file').setInputFiles({name: 'fixture.yaml', mimeType: 'text/yaml', buffer: Buffer.from('proxies: []\nproxy-groups: []\nrules: [MATCH,DIRECT]\n')});
    await page.locator('.source-card').getByRole('button', {name: '导入文件', exact: true}).click();
    await page.getByText('文件已保存。请预览并确认切换到导入的配置；同名资源也可通过“重启应用资源”生效。', {exact: true}).waitFor();
    assert.equal(submitted[2].kind, 'asset');
    await page.getByRole('button',{name:'预览来源切换',exact:true}).click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});
    await page.locator('#apply').click();
    assert.equal(await page.locator('[data-ppsub-apply]').isDisabled(), true);
    await saved();
    assert.equal(submitted[3].payload.patch.ini.mode, 'yaml');
    assert.equal(await page.getByLabel('订阅更新间隔',{exact:true}).count(),0);
    assert.equal(await page.locator('.source-kind').textContent(),'本地 YAML');
    await page.getByRole('link', {name: '网关设置', exact: true}).click();
    fail = true;
    await blur(page.getByLabel('流量记录上限', {exact: true}), '9002');
    await apply(); await page.getByText('应用验收失败，已恢复原配置。请检查网卡、监听端口和资源。', {exact: true}).waitFor();
    assert.equal(await page.getByLabel('流量记录上限', {exact: true}).isEnabled(), true);
    fail = false; expireLogin = true;
    await blur(page.getByLabel('流量记录上限', {exact: true}), '9003');
    await apply(); await page.getByText('登录已失效，请先返回管理面板重新登录。', {exact: true}).waitFor();
    const pending = await page.evaluate(() => JSON.parse(sessionStorage.getItem('lovestgateway-job')));
    assert.match(pending.id, /^[0-9a-f]{32}$/); assert.equal(Object.keys(pending).length, 3);
    expireLogin = false; await page.reload(); await saved();
    assert.equal(await page.evaluate(() => sessionStorage.getItem('lovestgateway-job')), null);
    await page.getByRole('tab', {name: '网络与出站', exact: true}).click();
    await blur(page.getByLabel('管理页面监听', {exact: true}), '0.0.0.0:19080');
    await apply(); await page.locator('#reconnect a').waitFor();
    const reconnect = new URL(await page.locator('#reconnect a').getAttribute('href'));
    assert.equal(reconnect.origin, 'http://127.0.0.1:19080');
    assert.equal(reconnect.pathname, '/ui/');
    assert.match(reconnect.searchParams.get('job'), /^[0-9a-f]{32}$/);
    assert.equal(reconnect.hash, '#/settings');
    // A new listener has separate origin storage. Carry only the public job ID.
    const transferred = reconnect.searchParams.get('job');
    await page.evaluate(() => sessionStorage.clear());
    await page.goto(origin + '/ui/gateway.html?job=' + transferred + '#/settings');
    await saved();
    assert.equal(new URL(page.url()).searchParams.has('job'), false);
    await navigation.getByRole('link', {name: '概览', exact: true}).click();
    await page.locator('.ppgw-gateway').waitFor({state: 'detached'});
    await navigation.getByRole('link', {name: '网关设置', exact: true}).click();
    await page.getByLabel('流量记录上限', {exact: true}).waitFor();
    await blur(page.getByLabel('流量记录上限', {exact: true}), '9010');
    page.once('dialog', dialog => dialog.dismiss());
    await page.goBack();
    await page.waitForURL('**/#/settings');
    assert.equal(new URL(page.url()).hash, '#/settings');
    assert.equal(await page.getByLabel('流量记录上限', {exact: true}).inputValue(), '9010');
    page.once('dialog', dialog => dialog.accept());
    await page.goBack();
    await page.locator('.ppgw-gateway').waitFor({state: 'detached'});
    // Aborting an in-flight config read must not write into the next route.
    configDelay = 500;
    await navigation.getByRole('link', {name: '网关设置', exact: true}).click();
    await page.locator('.ppgw-gateway').waitFor();
    await navigation.getByRole('link', {name: '概览', exact: true}).click();
    await page.locator('.ppgw-gateway').waitFor({state: 'detached'});
    await page.waitForTimeout(600);
    assert.deepEqual(errors, []);
    for(const tab of context.pages())await tab.unrouteAll({behavior:'ignoreErrors'});await context.close();
    console.log(JSON.stringify({syntheticOnly: true, nativeSettings: true, policyReset: true,
      uploadThenPreview: true, retryAndRelogin: true, desktopMobile: true, staleAndRollback: true, listenerReconnect: true,
      sharedShell: true, draftAcrossRoutes: true, backNavigationGuard: true, appearanceSelection: true, mobileMenu: true,
      unmountDuringFetch: true, observerApiSettings: true, recheckPreservesDraft:true, staleCheckDistinguished:true, coreNetworkFailureAndRetry: true, coreRestartReconnect:true, coreTaskSurvivesReload:true, coreSupersededTaskIsolation:true, coreAuthenticationReconnect:true, coreMutationNeverReplayed:true, rateLimitRetryTime: true, manualCoreChunkUpload: true, passwordConfirmation: true, eightCharacterPassword: true, sevenCharacterPasswordRejected: true, pageErrors: errors}));
  } finally { for(const context of browser.contexts())for(const tab of context.pages())await tab.unrouteAll({behavior:'ignoreErrors'});await browser.close(); }
}
main().then(()=>process.stdout.write('',()=>process.exit(0)),error=>{console.error(error);process.stderr.write('',()=>process.exit(1));});
