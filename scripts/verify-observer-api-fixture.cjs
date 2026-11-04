/* Optional API settings acceptance against synthetic APIs; no live gateway. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PPGW_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, 'dist/observer-api-ui'));
const origin = 'http://127.0.0.1:18885';
const fixture = JSON.parse(execFileSync(process.env.PPGW_PYTHON || 'python', ['-c',
  "import sys,json;sys.path.insert(0,'integration');from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in fields.items()} for ns,fields in SCHEMA.items()};s['maintenance'].update(history_enabled=False,auto_update=False);s['api']['read_token']='fixture-'+('a'*48);print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in fields.items()} for ns,fields in SCHEMA.items()}}))"
], {cwd:root, encoding:'utf8'}));

(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  const passed = [];
  try {
    for (const width of [1440, 390, 320]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
      try {
        const model = {...structuredClone(fixture),revision:'a'.repeat(64),assets:[],
          source:{native_ppsub:false,local_policy:false},mihomo_installed:false};
        const page = await context.newPage(), errors = [], changes = [];
        let token = 'fixture-' + 'a'.repeat(48), reveals = 0, job, applied = false, failNext = false;
        page.on('pageerror', error => errors.push(error.message));
        await page.addInitScript(theme => {
          localStorage.setItem('PaoPaoGateWay',JSON.stringify({clashAPIConfigs:[{secret:'fixture-admin'}]}));
          localStorage.setItem('ppgw-theme',theme);
          Object.defineProperty(navigator,'clipboard',{value:undefined});
          document.execCommand = action => action === 'copy';
        }, theme);
        await page.route(origin + '/**', async route => {
          const request = route.request(), target = new URL(request.url()).pathname;
          if (target.startsWith('/sniffbox/')) {
            let data = {mode:'free',ppsub:false,clash_version:'fixture'}, status = 200;
            if (target.startsWith('/sniffbox/settings/')) {
              assert.equal(request.headers().authorization,'Bearer fixture-admin');
              if (target.endsWith('/config')) data = model;
              else if (target.endsWith('/observer-token')) {
                assert.equal(request.method(),'POST'); assert.deepEqual(request.postDataJSON(),{});
                reveals++; data = {token};
              } else if (target.endsWith('/preview')) {
                const body = request.postDataJSON(); changes.push(body.patch);
                data = {action:'none',changes:structuredClone(body.patch),effects:{core:'none',services:['api','history']}};
                if (data.changes.api?.read_token) data.changes.api.read_token = '********';
              } else if (target.endsWith('/apply')) {
                job = request.postDataJSON(); applied = false; status = 202; data = {id:job.id,status:'queued'};
              } else if (target.includes('/jobs/')) {
                if (failNext) data = {id:job.id,status:'failed',error:'APPLY_FAILED_ROLLED_BACK'};
                else {
                  if (!applied) {
                    for (const [namespace, fields] of Object.entries(job.payload.patch)) for (const [key,value] of Object.entries(fields)) {
                      if (key === 'read_token') token = value;
                      model.settings[namespace][key] = model.schema[namespace][key].secret ? '********' : value;
                    }
                    model.revision = (model.revision.startsWith('a') ? 'b' : 'a').repeat(64); applied = true;
                  }
                  data = {id:job.id,status:'succeeded',result:{runtime:{status:'saved',reason:'core_unchanged'}}};
                }
              } else throw Error('Unexpected settings request ' + target);
            } else if (target.endsWith('/maintenance')) data = {core:{installed_version:null},operation:null};
            return route.fulfill({status,contentType:'application/json',headers:{'Cache-Control':'no-store'},body:JSON.stringify(data)});
          }
          const file = path.join(output,target === '/ui/' ? 'index.html' : path.basename(target));
          try {
            return route.fulfill({contentType:target.endsWith('.js') ? 'application/javascript' : target.endsWith('.css') ? 'text/css' : target.endsWith('.svg') ? 'image/svg+xml' : 'text/html',body:await fs.readFile(file)});
          } catch { return route.fulfill({status:404,body:''}); }
        });
        const maintenance = () => page.getByRole('tab',{name:'维护与 API',exact:true}).click();
        const toggle = () => page.getByLabel('启用对外观测 API',{exact:true});
        const save = async () => {
          await page.locator('#preview').click(); await page.locator('#preview-dialog').waitFor({state:'visible'});
          assert.match(await page.locator('#preview-content').innerText(),/无需重启转发核心/);
          await page.locator('#apply').click();
        };
        const saved = () => page.getByText('配置已应用，运行状态验收通过。',{exact:true}).waitFor();
        await page.goto(origin + '/ui/#/settings'); await maintenance();
        assert.equal(await toggle().isChecked(),false);
        await page.getByText('API 已关闭：不监听端口',{exact:true}).waitFor();
        assert.equal(reveals,0);
        await page.getByRole('button',{name:'查看当前 Token',exact:true}).click();
        await page.waitForFunction(expected => document.querySelector('#observer-api-current-token')?.value === expected,token);
        assert.equal(await page.getByLabel('当前 API Token',{exact:true}).inputValue(),token);
        await page.getByRole('button',{name:'隐藏 Token',exact:true}).click();
        assert.equal(await page.getByLabel('当前 API Token',{exact:true}).inputValue(),'');
        await page.getByRole('button',{name:'生成新 Token',exact:true}).click();
        const replacement = await page.getByLabel('更换 API Token',{exact:true}).inputValue();
        assert.match(replacement,/^[0-9a-f]{64}$/);
        await page.getByLabel('API 监听地址',{exact:true}).fill('192.0.2.10');
        await page.getByLabel('API 监听地址',{exact:true}).press('Tab');
        await toggle().check(); await page.getByLabel('API 历史采集',{exact:true}).check();
        await save(); await saved();
        assert.deepEqual(changes.at(-1),{api:{read_token:replacement,listen:'192.0.2.10',enabled:true},maintenance:{history_enabled:true}});
        assert.equal(await toggle().isChecked(),true);
        assert.doesNotMatch(await page.evaluate(() => JSON.stringify([localStorage,sessionStorage])),new RegExp(replacement));
        await toggle().uncheck(); await save(); await saved();
        assert.deepEqual(changes.at(-1),{api:{enabled:false}});
        assert.equal(await toggle().isChecked(),false);
        assert.equal(model.settings.maintenance.history_enabled,true);
        await page.reload(); await maintenance();
        assert.equal(await toggle().isChecked(),false);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
        await page.screenshot({path:path.join(root,`dist/observer-api-${width}-${theme}.png`),fullPage:true});
        failNext = true; await toggle().check(); await save();
        await page.getByText('应用验收失败，已恢复原配置。请检查网卡、监听端口和资源。',{exact:true}).waitFor();
        assert.equal(model.settings.api.enabled,false);
        assert.equal(await toggle().isChecked(),false);
        assert.deepEqual(errors,[]);
        passed.push({width,theme,defaultOff:true,enableDisable:true,tokenRotation:true,failedEnable:true,noOverflow:true});
      } finally { await context.close(); }
    }
    const report = {syntheticOnly:true,cases:passed};
    await fs.writeFile(path.join(root,'dist/observer-api-browser-20261104.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
