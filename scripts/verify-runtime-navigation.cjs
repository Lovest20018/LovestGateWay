const {sessionFixture}=require('./browser-session-fixture.cjs');
/* Synthetic first-install runtime mode changes; never writes to a live gateway. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PPGW_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, 'dist/runtime-navigation-ui'));
const origin = 'http://127.0.0.1:18889';
const defaults = JSON.parse(execFileSync(process.env.PPGW_PYTHON || 'python', ['-c',
  "import sys,json;sys.path.insert(0,'integration');from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini']['mode']='free';s['service']['PPGW_INTERFACE']='ens18';print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))"
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
    await page.addInitScript(() => localStorage.setItem('PaoPaoGateWay', JSON.stringify({clashAPIConfigs: [{secret: 'fixture-token'}]})));
    const model = {...defaults, revision: 'a'.repeat(64), assets: [], source: {native_ppsub: false, local_policy: false}, mihomo_installed: true};
    let job, infoReads = 0, sourceImports = 0;
    const sessions=sessionFixture({proof:()=> 'fixture-token'});
    await page.route(origin + '/**', async route => {
      const request = route.request(), target = new URL(request.url()).pathname;
      const reply = (data, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)});
      if (target.startsWith('/sniffbox/')) {
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
    await sessions.install(page);
    await page.goto(origin + '/ui/#/subscriptions');
    await page.getByRole('tab', {name: '配置来源', exact: true}).click();
    await page.locator('.source-kind').waitFor();
    const nav = page.locator('#shell-nav');
    assert.equal(await nav.locator('a[href="#/proxies"]').isVisible(), false);
    assert.equal(await page.locator('#source-type').isVisible(),false);await page.getByRole('button',{name:'切换配置来源',exact:true}).click();
    await page.locator('#source-type').selectOption('ppsub');
    await page.locator('#source-ppsub').setInputFiles({name: 'import.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(native))});
    await page.getByText('PPsub 文件已选中，预览并确认后才会切换来源。', {exact: true}).waitFor();
    await page.getByRole('button', {name: '预览来源切换', exact: true}).click();
    await page.locator('#apply').click();
    await page.getByText('配置已应用，运行状态验收通过。', {exact: true}).waitFor();
    await nav.locator('a[href="#/proxies"]').waitFor();
    assert.equal(await nav.locator('a[href="#/rules"]').count(), 1);
    await nav.locator('a[href="#/proxies"]').click();
    await page.locator('#proxies-page').getByText('Fixture Main', {exact: true}).first().waitFor();
    await page.locator('#proxies-page').getByText('Fixture Node', {exact: true}).first().waitFor();
    await nav.getByRole('link', {name: '网关设置', exact: true}).click();
    await page.getByRole('tab', {name: '网络与出站', exact: true}).click();
    await page.getByLabel('网关出站模式', {exact: true}).selectOption('free');
    await page.locator('#preview').click();
    await page.locator('#apply').click();
    await page.getByText('配置已应用，运行状态验收通过。', {exact: true}).waitFor();
    assert.equal(await nav.locator('a[href="#/proxies"]').isVisible(), false);
    assert.equal(await nav.locator('a[href="#/rules"]').isVisible(), false);
    assert.equal(sourceImports, 1);
    assert.deepEqual(errors, []);
    const evidence = {syntheticOnly: true, initialDirectMode: true, importedPpsubApplied: true,
      proxiesAndRulesAppearWithoutReload: true, proxyGroupAndNodeVisible: true, directModeHidesProxyNavigation: true,
      sourceImports, infoReads, pageErrors: errors};
    await fs.writeFile(path.join(root, 'dist/runtime-navigation-browser-evidence.json'), JSON.stringify(evidence, null, 2) + '\n');
    console.log(JSON.stringify(evidence));
    await context.close();
  } finally { await browser.close(); }
})().then(()=>process.exit(0),error=>{console.error(error);process.exit(1);});
