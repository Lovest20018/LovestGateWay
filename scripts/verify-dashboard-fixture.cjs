/* Exercise the source editor and opaque compatibility dashboard with synthetic data; no live Gateway. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PPGW_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, 'dist/merged-upstream-ui'));
const origin = 'http://127.0.0.1:18884';
const raw = {
  proxies: [{name: 'N1', type: 'socks5', server: '127.0.0.1', port: 9, password: 'fixture-only'}],
  secret: 'fixture-core', dns: {nameserver: ['1.1.1.1']},
  'proxy-groups': [
    {name: 'Main', type: 'select', proxies: ['Backup', 'DIRECT'], hidden: true},
    {name: 'Backup', type: 'fallback', proxies: ['N1', 'N1'], url: 'https://example.com/test', interval: 135, lazy: true},
  ],
  rules: ['PROCESS-NAME,fixture.exe,Main', 'DOMAIN-KEYWORD,foo#bar,DIRECT', 'MATCH,Main'],
};
function python(code, value) {
  return JSON.parse(execFileSync(process.env.PPGW_PYTHON || 'python', ['-c',
    "import sys,json;sys.path.insert(0,'integration');" + code],
    {cwd: root, input: JSON.stringify(value), encoding: 'utf8'}));
}
const projected = python("from clash_ppsub import editor_config;d=json.load(sys.stdin);print(json.dumps(editor_config(d)[0]))", raw);
const defaults = python("from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini'].update(mode='suburl',suburl='fixture-source',net_rec=True);print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))", null);
const native = {
  version: 'PaoPaoGateWay 3.1', exported_at: 'fixture',
  subs: [{name: 'Fixture provider', url: 'https://example.com/sub'}],
  'node-groups': [{name: 'Native Main', subs: ['all']}],
  rules: [{fixrule: ['MATCH,Native Main']}],
};

async function exercise(browser, kind) {
  const context = await browser.newContext({viewport: {width: 1440, height: 960}, reducedMotion: 'reduce'});
  try {
    const page = await context.newPage(), errors = [];
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => { errors.push(error.message); console.error('PAGE_ERROR', error.message); });
    await page.addInitScript(() => localStorage.setItem('PaoPaoGateWay', JSON.stringify({
      clashAPIConfigs: [{secret: 'fixture-token'}],
    })));
    let operation = null, saved = null, configReads = 0, expectedSource = null;
    const model = {...structuredClone(defaults), revision: 'fixture-revision', assets: [], source: {url: kind === 'ppsub' ? 'ppsub@http://127.0.0.1/ppsub.json' : 'https://example.com/sub', native_ppsub: kind === 'ppsub', local_policy: true}, mihomo_installed: true};
    let releaseInitialRead;
    const initialRead = new Promise(resolve => { releaseInitialRead = resolve; });
    const maintenanceRequests = [];
    await page.route(origin + '/**', async route => {
      const target = new URL(route.request().url()).pathname;
      if (target === '/sniffbox/settings/config') return route.fulfill({contentType: 'application/json', body: JSON.stringify(model)});
      if (target.startsWith('/sniffbox/')) {
        let body = {mode: 'suburl', clash_version: 'fixture', ppsub: kind === 'ppsub'};
        if (target.endsWith('/status')) body = {
          ready: true, edit_kind: kind, ppsub_write_allowed: true, base_revision: 'fixture-revision', operation,
        };
        if (target.endsWith('/config')) {
          if (++configReads === 1) {
            await Promise.race([initialRead, new Promise(resolve => setTimeout(resolve, 5000))]);
            if (kind === 'ppsub') return route.fulfill({status: 503, contentType: 'application/json', body: '{"error":"TEMPORARY_FAILURE"}'});
          }
          body = {
          config: saved || (kind === 'ppsub' ? native : projected), source: kind === 'ppsub' ? 'ppsub' : 'clash_runtime',
          edit_kind: kind, snapshot_id: 'fixture-snapshot', write_allowed: true,
          base_revision: 'fixture-revision', settings_revision:'fixture-revision', synthetic_source:kind==='clash_patch', node_choices: ['N1'], notice: 'Synthetic fixture',
          };
        }
        if (/\/subscription\/(maintenance|core\/|policy\/reset)/.test(target)) maintenanceRequests.push(target);
        if (target.endsWith('/apply')) {
          assert.equal(route.request().headers().authorization, 'Bearer fixture-token');
          const payload = route.request().postDataJSON();
          if(payload.kind==='export_yaml')return route.fulfill({contentType:'application/json',body:JSON.stringify({filename:'gateway-running.yaml',content:'proxies: []\ndns:\n  nameserver: [1.1.1.1]\n'})});
          assert.equal(payload.kind,'editor'); assert.equal(payload.edit_kind,kind);
          assert.equal(payload.base_revision,'fixture-revision');assert.equal(payload.settings_revision,'fixture-revision');
          const submitted=JSON.parse(payload.value);
          if(payload.dry_run)return route.fulfill({contentType:'application/json',body:JSON.stringify({action:'restart',editor_changed:true,changes:payload.settings_patch,save_target:kind==='clash_patch'?'local_policy':'ppsub'})});
          saved=submitted;
          if(kind==='clash_patch'){
            assert.equal(payload.snapshot_id,'fixture-snapshot');
            assert.deepEqual(saved.subs,[]);
          }else{
            assert.equal(saved.subs[0].name,'Edited native provider');
            assert.equal(saved.subs[0].url,'https://example.com/native-updated');
            assert.equal(payload.settings_patch.ini.subtime,'9h');
            model.settings.ini.subtime='9h';
          }
          operation = {id: payload.operation_id, status: 'applied', message: 'Fixture saved ' + kind};
          body = operation;
        }
        return route.fulfill({status: target.endsWith('/apply') ? 202 : 200, contentType: 'application/json', body: JSON.stringify(body)});
      }
      const filename = path.join(output, target === '/ui/' || target === '/ui' ? 'index.html' : path.basename(target));
      try {
        const body = await fs.readFile(filename);
        await route.fulfill({contentType: target.endsWith('.js') ? 'application/javascript' : target.endsWith('.css') ? 'text/css' : target.endsWith('.svg') ? 'image/svg+xml' : 'text/html', body});
      } catch { await route.fulfill({status: 404, body: ''}); }
    });
    await page.goto(origin + '/ui/#/ppsub');
    await page.evaluate(() => { window.fixtureDocument = 'unchanged'; });
    await page.waitForURL('**/#/subscriptions');
    await page.getByRole('heading', {name: '订阅与策略', exact: true}).waitFor();
    await page.locator(' #shell-nav a[href="#/subscriptions"]').waitFor();
    assert.equal(await page.locator(' #shell-nav a[href="#/ppsub"]').count(), 0);
    assert.equal(await page.locator(' #shell-nav a[href="#/subscriptions"]').count(), 1);
    await page.getByRole('heading', {name: '订阅与策略', exact: true}).waitFor();
    const apply = page.getByRole('button', {name: '保存并应用', exact: true});
    await apply.waitFor();
    for (const label of ['网关维护', '检查官方核心', '在线更新 Mihomo', '回退上一核心', '恢复订阅原策略']) {
      assert.equal(await page.locator('.ppgw-subscription-editor').getByText(label, {exact: true}).count(), 0);
    }
    assert.equal(await apply.isDisabled(), true);
    assert.equal(await page.locator('.ppgw-subscription-editor input').first().isDisabled(), true);
    releaseInitialRead();
    const reload = page.getByRole('button', {name: '重新读取', exact: true});
    if (kind === 'ppsub') {
      await page.getByText('读取当前配置失败，请点击“重新读取”后再保存。', {exact: true}).waitFor();
      assert.equal(await apply.isDisabled(), true);
      await reload.click();
    }
    await page.locator('.groups').getByRole('button', {name:'删除',exact:true}).last().waitFor();
    await page.waitForFunction(() => !document.querySelector('[data-ppsub-apply]').disabled);
    // A source-editor wrapper must never become an implicit second grid column.
    const layoutCheck=async()=>{
      const bounds=await page.evaluate(()=>{
        const box=selector=>{const b=document.querySelector(selector).getBoundingClientRect();return{x:b.x,y:b.y,width:b.width,bottom:b.bottom};};
        return{title:box('#page-title'),toolbar:box('.source-editor>.phead'),globals:box('.source-editor .monitor'),groups:box('.source-editor .groups'),main:box('#shell-content'),overflow:document.documentElement.scrollWidth>innerWidth};
      });
      assert.ok(bounds.title.y<bounds.toolbar.y,'page heading must precede the editor toolbar');
      assert.ok(bounds.globals.bottom<=bounds.groups.y,'global options and node groups must follow vertically');
      assert.ok(bounds.globals.width>=bounds.main.width-80,'editor must use the full page width');
      assert.equal(bounds.overflow,false,'page must not overflow horizontally');
      assert.equal(await page.locator('iframe').count(),0);
      assert.equal(await page.locator('dialog').count(),0);
      return bounds;
    };
    await layoutCheck();
    await page.evaluate(()=>localStorage.setItem('ppgw-theme','light'));
    await page.reload();await page.evaluate(()=>{window.fixtureDocument='unchanged';});
    await page.waitForFunction(()=>!document.querySelector('[data-ppsub-apply]')?.disabled&&document.querySelector('.source-editor .groups'));
    await page.setViewportSize({width:1863,height:878});await layoutCheck();
    await page.screenshot({path:path.join(root,'dist/subscription-unified-'+kind+'-desktop.png'),animations:'disabled'});
    const groupCount=await page.locator('.groups > .item').count();
    await page.getByRole('button',{name:'添加节点组',exact:true}).click();
    const added=page.locator('.groups > .item').last();await added.locator('[data-field=name]').fill('新节点组');
    const compact=await added.evaluate(item=>({height:item.getBoundingClientRect().height,groupTextareas:item.querySelectorAll('.editor-group-body textarea').length,width:item.getBoundingClientRect().width}));
    assert.ok(compact.height<=340,'a new desktop group must remain compact: '+JSON.stringify(compact));assert.equal(compact.groupTextareas,0);
    await added.screenshot({path:path.join(root,'dist/compact-node-group-'+kind+'-desktop.png')});
    const appearance=page.getByRole('group',{name:'外观',exact:true});await appearance.getByRole('button',{name:'暗色',exact:true}).click();await added.screenshot({path:path.join(root,'dist/compact-node-group-'+kind+'-dark.png')});await appearance.screenshot({path:path.join(root,'dist/appearance-segments-dark.png')});await appearance.getByRole('button',{name:'亮色',exact:true}).click();await appearance.screenshot({path:path.join(root,'dist/appearance-segments-light.png')});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('.source-editor *')].map(n=>({tag:n.tagName,cls:n.className,width:n.getBoundingClientRect().width,right:n.getBoundingClientRect().right})).filter(n=>n.right>innerWidth+1&&n.width>0).slice(0,20))));
    compact.mobileHeight=await added.evaluate(item=>item.getBoundingClientRect().height);assert.ok(compact.mobileHeight<=560,'a mobile group must fit within a screen');
    await added.screenshot({path:path.join(root,'dist/compact-node-group-'+kind+'-mobile.png')});
    await page.setViewportSize({width:1440,height:960});page.once('dialog',dialog=>dialog.accept());await added.getByRole('button',{name:'删除',exact:true}).click();assert.equal(await page.locator('.groups > .item').count(),groupCount);

    if (kind === 'ppsub') {
      const name = page.locator('.subs [data-field="name"]').first();
      await name.fill('Temporary draft');
      const readsBeforeCancel = configReads;
      page.once('dialog', dialog => dialog.dismiss());
      await reload.click();
      assert.equal(await name.inputValue(), 'Temporary draft');
      assert.equal(configReads, readsBeforeCancel);
      page.once('dialog', dialog => dialog.accept());
      const reread = page.waitForResponse(origin + '/sniffbox/subscription/config');
      await reload.click(); await reread;
      await page.waitForFunction(() => !document.querySelector('[data-ppsub-apply]').disabled);
      assert.equal(await name.inputValue(), native.subs[0].name);
      const choosing = page.waitForEvent('filechooser');
      await page.getByRole('button', {name:'导入 PPsub',exact:true}).click();
      const chooser = await choosing;
      const imported = structuredClone(native); imported.subs[0].name = 'Imported provider';
      await chooser.setFiles({name: 'fixture.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(imported))});
      await page.waitForFunction(() => document.querySelector('.subs [data-field="name"]').value === 'Imported provider');
      assert.equal(saved, null);
      await name.fill('Edited native provider');
      await page.locator('.subs [data-field="url"]').first().fill('https://example.com/native-updated');
      await page.getByRole('link', {name: '网关设置', exact: true}).click();
      await page.getByLabel('流量记录上限', {exact: true}).waitFor();
      await page.getByRole('link', {name: '订阅与策略', exact: true}).click();
      assert.equal(await name.inputValue(), 'Edited native provider');
      await page.locator('.editor-schedule > summary').click();
      const interval = page.getByLabel('订阅更新间隔', {exact: true});
      await interval.fill('9h'); await interval.press('Tab');
      assert.equal(await page.locator('#savebar').isVisible(),false);
      assert.equal(saved,null);
      assert.equal(await interval.inputValue(),'9h');
      assert.equal(await page.evaluate(() => window.fixtureDocument), 'unchanged');
      page.once('dialog', dialog => dialog.dismiss());
      await page.getByRole('link', {name: '概览', exact: true}).click();
      assert.equal(new URL(page.url()).hash, '#/subscriptions');
      assert.equal(await name.inputValue(), 'Edited native provider');
    }
    assert.equal(await page.locator('#subscription-options').count(), 0);
    assert.equal(await page.getByRole('radio', {name: '订阅配置', exact: true}).count(), 0);
    assert.equal(await page.locator('.monitor').getByLabel('订阅更新间隔', {exact: true}).count(), 1);
    assert.equal(await page.getByLabel('使用本地 YAML', {exact:true}).count(),0);
    await page.getByRole('heading',{name:'当前配置来源',exact:true}).waitFor();
    assert.equal(await apply.isDisabled(), false);
    await apply.click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});
    await page.locator('#apply').click();
    await page.getByText('Fixture saved ' + kind, {exact: true}).waitFor();
    assert.ok(saved);
    if(kind==='clash_patch'){
      assert.equal(await page.locator('.subs').isVisible(),false);
      assert.equal(await page.locator('.phead .menuwrap').nth(1).isVisible(),false);
      const download=page.waitForEvent('download');await page.getByRole('button',{name:'备份运行 YAML',exact:true}).click();
      assert.equal((await download).suggestedFilename(),'gateway-running.yaml');
    }
    assert.equal(await page.locator('#setting-ini-suburl').count(), 0);
    assert.equal(await page.getByRole('tab', {name: '订阅来源', exact: true}).count(), 0);
    assert.equal(await page.getByRole('tab', {name: '分组与规则', exact: true}).count(), 0);
    if(kind==='ppsub')assert.equal(await page.evaluate(url => [...document.querySelectorAll('input')].filter(input=>input.value===url&&input.checkVisibility()).length,saved.subs[0].url),1);
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', {name:'导出编辑内容',exact:true}).click();
    const downloaded = await downloading;
    const exported = JSON.parse(await fs.readFile(await downloaded.path(), 'utf8'));
    assert.equal(downloaded.suggestedFilename(), 'ppsub.json');
    assert.deepEqual(exported.subs,saved.subs);
    assert.deepEqual(exported.rules, saved.rules);
    assert.equal(await page.locator('input[autocomplete="current-password"]').count(), 0);
    assert.equal(await page.locator('a[href="#/cloudflared"]').count(), 0);
    await page.screenshot({path: path.join(root, 'dist/ppsub-native-' + kind + '-desktop.png'), fullPage: true, animations: 'disabled'});
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForFunction(() => document.querySelector('#shell-nav').getBoundingClientRect().right <= 0);
    assert.equal(await apply.isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await layoutCheck();
    await page.screenshot({path: path.join(root, 'dist/ppsub-native-' + kind + '-mobile.png'), fullPage: true, animations: 'disabled'});
    await page.setViewportSize({width: 1440, height: 960});
    await page.getByRole('link', {name: '概览', exact: true}).click();
    await apply.waitFor({state: 'detached'});
    assert.deepEqual(errors, []);
    assert.deepEqual(maintenanceRequests, []);
    return {kind, autoLoadCurrent: true, readFailureRetry: kind === 'ppsub', explicitSave: true,
      actualEditorExport: true, reloadDraftGuard: kind === 'ppsub', sharedLogin: true, routeCleanup: true,
      compactNewGroupDesktopMobile: true,newGroup: compact,maintenanceConsolidated: true, globalOptionsMerged: true, singleSubscriptionForm: true, sourceUrlSaved: true, ...(kind === 'ppsub' ? {draftAcrossSettings: true, combinedDraftSave: true, importFileDraft: true} : {}), pageErrors: errors};
  } finally { await context.close(); }
}

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    console.log(JSON.stringify({syntheticOnly: true, checks: [
      await exercise(browser, 'clash_patch'), await exercise(browser, 'ppsub'),
    ]}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
