/* Shared-document navigation, authentication and layout; synthetic APIs only. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PPGW_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, 'dist/runtime-navigation-ui'));
const origin = 'http://127.0.0.1:18889';
const defaults = JSON.parse(execFileSync(process.env.PPGW_PYTHON || 'python', ['-c',
  "import sys,json;sys.path.insert(0,'integration');from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini']['mode']='yaml';s['service']['PPGW_INTERFACE']='ens18';print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))"
], {cwd: root, encoding: 'utf8'}));
const native = {version: 'PaoPaoGateWay 3.1', exported_at: 'fixture',
  subs: [{name: 'Fixture provider', url: 'https://example.com/provider'}],
  'node-groups': [{name: 'Fixture Main', subs: ['all']}], rules: []};
(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 960}, reducedMotion: 'reduce'});
    const page = await context.newPage(), errors = [];
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {if(!localStorage.getItem('PaoPaoGateWay'))localStorage.setItem('PaoPaoGateWay', JSON.stringify({clashAPIConfigs: [{secret: 'fixture-token'}]}));});
    const model = {...defaults, revision: 'a'.repeat(64), assets: [], source: {native_ppsub: false, local_policy: false}, mihomo_installed: true};
    let job, infoReads=0,sourceImports=0,expectedSecret='fixture-token';const requests=[];
    await page.route(origin + '/**', async route => {
      const request = route.request(), target = new URL(request.url()).pathname;
      const reply = (data, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)});
      if(target==='/connections')return reply({connections:[],downloadTotal:0,uploadTotal:0});
      if(target==='/configs')return reply({mode:'rule'});
      if(target==='/rules')return reply({rules:[{type:'Match',payload:'',proxy:'Fixture Main'}]});
      if(target==='/providers/rules')return reply({providers:{}});
      if (target.startsWith('/sniffbox/')) {
        requests.push({target,auth:request.headers().authorization});
        if(request.headers().authorization!=='Bearer '+expectedSecret)return reply({error:'LOGIN_REQUIRED'},401);
        if(target.endsWith('/sse'))return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
        if(target==='/sniffbox/screen')return route.fulfill({body:'Fixture log',headers:{'X-Screen-Status':'unavailable'}});
        if(target==='/sniffbox/traffic/total')return reply({downloadTotal:0,uploadTotal:0,domains:[],ips:[],processes:[]});
        if(target==='/sniffbox/nodes')return reply({nodes:[],total:0});

        if (target === '/sniffbox/info') infoReads++;
        if (target === '/sniffbox/settings/config') return reply(model);
        if (target === '/sniffbox/settings/preview') return reply({action: 'restart', changes: request.postDataJSON().patch, effects: {core: 'restart', services: []}});
        if (target === '/sniffbox/settings/apply') {
          job = request.postDataJSON();
          Object.assign(model.settings.ini, job.payload.patch.ini);
          if (job.payload.source_ppsub) { assert.deepEqual(JSON.parse(job.payload.source_ppsub), native); model.source.native_ppsub = true; sourceImports++; }
          return reply({id: job.id, status: 'queued'}, 202);
        }
        if (target.startsWith('/sniffbox/settings/jobs/')) return reply({id: job.id, status: 'succeeded', result: {runtime: {status: 'running'}}});
        if (target === '/sniffbox/subscription/status') return reply({ready: model.settings.ini.mode !== 'free', edit_kind: 'ppsub', ppsub_write_allowed: true, base_revision: model.revision});
        if (target === '/sniffbox/subscription/config') return reply({config: native, write_allowed: true, edit_kind: 'ppsub', base_revision: model.revision, settings_revision: model.revision, node_choices: ['Fixture Node']});
        if (target === '/sniffbox/proxies') return reply({proxies: {
          'Fixture Main': {name: 'Fixture Main', type: 'Selector', now: 'Fixture Node', all: ['Fixture Node', 'DIRECT'], history: []},
          'Fixture Node': {name: 'Fixture Node', type: 'Shadowsocks', udp: true, history: []},
          DIRECT: {name: 'DIRECT', type: 'Direct', history: []}
        }});
        return reply({mode: model.settings.ini.mode, clash_version: 'v1.19.33', ppsub: model.source.native_ppsub});
      }
      try {
        const filename = path.join(output, target === '/ui/' ? 'index.html' : path.basename(target));
        return route.fulfill({body: await fs.readFile(filename), contentType: target.endsWith('.js') ? 'application/javascript' : target.endsWith('.css') ? 'text/css' : target.endsWith('.svg') ? 'image/svg+xml' : 'text/html'});
      } catch { return route.fulfill({status: 404, body: ''}); }
    });
    await page.goto(origin+'/ui/#/overview');
    const runtime=page.locator('#runtime-panel');await page.locator('#overview-page').waitFor();
    await page.evaluate(()=>{window.documentIdentity='same-document';});
    const pages=[];
    assert.equal(await page.locator('#shell-nav [data-route]>.shell-icon').count(),11);
    const brandLogo=page.locator('#shell-nav .shell-brand img');assert.equal(await brandLogo.getAttribute('src'),'/ui/lovestgateway.svg');await page.waitForFunction(()=>document.querySelector('.shell-brand img')?.naturalWidth>0);
    for(const key of ['proxies','rules','connections','traffic','distribution','logs','region']){
      await page.locator('#shell-nav [data-route="'+key+'"]').click();
      await page.waitForURL('**/#/'+key);
      await runtime.locator('main h1').first().waitFor();
      await page.waitForTimeout(80);
      pages.push({key,heading:await runtime.locator('main h1').first().innerText()});
      assert.equal(await runtime.locator('aside').first().isVisible(),false);
      assert.equal(await page.evaluate(()=>window.documentIdentity),'same-document');
    }
    assert.equal(new Set(pages.map(row=>row.heading)).size,7,'each compatibility navigation must render its own runtime component');
    assert.equal(await page.locator('iframe').count(),0);
    await page.locator('#shell-nav [data-route="overview"]').click();await page.locator('#overview-page').waitFor();
    await page.screenshot({path:path.join(root,'dist/unified-overview-desktop.png')});
    await page.locator('#shell-nav [data-route="settings"]').click();
    const limit=page.getByLabel('流量记录上限',{exact:true});await limit.fill('9010');await limit.press('Tab');
    await page.locator('#preview').click();await page.locator('#preview-dialog').waitFor({state:'visible'});await limit.fill('9011');await limit.press('Tab');await page.locator('#apply').click();await page.getByText('编辑内容已变化，请重新预览后再确认应用。',{exact:true}).waitFor();assert.equal(job,undefined);await limit.fill('9010');await limit.press('Tab');
    const appearance=page.getByRole('group',{name:'外观',exact:true});
    assert.deepEqual(await appearance.getByRole('button').allTextContents(),['系统','亮色','暗色']);
    assert.equal(await appearance.locator('[aria-pressed=true]').getAttribute('data-theme-choice'),'system');
    await page.emulateMedia({colorScheme:'dark'});await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
    await page.emulateMedia({colorScheme:'light'});await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');
    await appearance.getByRole('button',{name:'暗色',exact:true}).click();await page.emulateMedia({colorScheme:'light'});
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark','explicit choice must override the system');
    await appearance.getByRole('button',{name:'亮色',exact:true}).click();await page.emulateMedia({colorScheme:'dark'});
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');
    assert.equal(await limit.inputValue(),'9010','appearance selection must retain the draft');
    assert.equal(new URL(page.url()).hash,'#/settings','appearance must not navigate');
    page.once('dialog',dialog=>dialog.dismiss());await page.goBack();await page.waitForURL('**/#/settings');await page.waitForTimeout(150);
    assert.equal(await limit.inputValue(),'9010','rejected Back must retain the draft');
    page.once('dialog',dialog=>dialog.accept());await page.locator('#shell-nav [data-route="proxies"]').click();
    await runtime.getByText('Fixture Main',{exact:true}).first().waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');
    // Changing the accepted secret forces the mounted upstream form to reauth.
    const password='ChangedFixturePassword8',newSecret=require('node:crypto').createHash('sha256').update(password).digest('hex');
    expectedSecret=newSecret;
    await page.locator('#shell-nav [data-route="traffic"]').click();
    await runtime.locator('input[autocomplete="current-password"]').waitFor({state:'attached'});
    await page.getByRole('button',{name:'重新登录',exact:true}).click();
    await page.locator('#reauth-panel').getByLabel('管理密码',{exact:true}).fill(password);
    await page.locator('#reauth-panel').getByRole('button',{name:'登录',exact:true}).click();
    await page.locator('#reauth-panel').waitFor({state:'detached'});
    await runtime.locator('main').waitFor();
    await page.locator('#shell-nav [data-route="proxies"]').click();
    await runtime.getByText('Fixture Main',{exact:true}).first().waitFor();
    assert.ok(requests.some(row=>row.target==='/sniffbox/proxies'&&row.auth==='Bearer '+newSecret),'upstream must use the new secret after reauth');
    assert.equal(await page.evaluate(()=>window.documentIdentity),'same-document','reauthentication must not reload the document');
    await page.goBack();await page.waitForURL('**/#/traffic');await runtime.locator('main h1').filter({hasText:'流量'}).waitFor();
    await page.goForward();await page.waitForURL('**/#/proxies');await runtime.getByText('Fixture Main',{exact:true}).first().waitFor();
    assert.equal(await page.evaluate(()=>window.documentIdentity),'same-document','browser Back and Forward must not reload the document');
    await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.locator('#shell-menu').click();await page.locator('#shell-nav [data-route="overview"]').click();
    await page.locator('#overview-page').getByRole('heading',{name:'概览',exact:true}).waitFor();await page.screenshot({path:path.join(root,'dist/unified-overview-mobile.png')});
    assert.equal(await page.locator('#shell-status').isVisible(),false,'reauth must clear the expired banner');
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');
    await page.reload();await page.locator('#overview-page').waitFor();
    assert.equal(await page.locator('#shell-appearance [aria-pressed=true]').getAttribute('data-theme-choice'),'light');
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');
    await page.evaluate(()=>{localStorage.setItem('ppgw-theme','custom');localStorage.setItem('ppgw-theme-base','dark');localStorage.setItem('ppgw-custom-colors',JSON.stringify({'--brand':'#7c5cff'}));});
    await page.emulateMedia({colorScheme:'light'});await page.reload();await page.locator('#overview-page').waitFor();
    assert.equal(await page.locator('#shell-appearance [aria-pressed=true]').getAttribute('data-theme-choice'),'system');
    assert.deepEqual(await page.evaluate(()=>[document.documentElement.dataset.theme,localStorage.getItem('ppgw-theme-base'),localStorage.getItem('ppgw-custom-colors'),document.documentElement.style.getPropertyValue('--brand')]),['light',null,null,'']);
    assert.equal(await page.locator('input[type=color]').count(),0);
    assert.equal(await page.locator('dialog').count(),0);assert.deepEqual(errors,[]);
    const evidence={syntheticOnly:true,pages,sameDocument:true,noIframe:true,noFloatingTools:true,appearancePreservesDraft:true,threeAppearanceOptions:true,systemThemeChangesLive:true,explicitThemeOverridesSystem:true,choiceSurvivesReload:true,retiredCustomThemeMigrated:true,backForwardAndDraftGuard:true,runtimeChangedPasswordReauth:true,mobile390:true,pageErrors:errors};
    await fs.writeFile(path.join(root,'dist/unified-runtime-browser-evidence.json'),JSON.stringify(evidence,null,2)+'\n');console.log(JSON.stringify(evidence));
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

