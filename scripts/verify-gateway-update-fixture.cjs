/* Synthetic browser acceptance; no production credentials, services or downloads. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PPGW_PLAYWRIGHT_MODULE || 'playwright');
const {sessionFixture} = require('./browser-session-fixture.cjs');
const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, 'dist/gateway-update-ui'));
const origin = 'http://127.0.0.1:18886';
const defaults = JSON.parse(execFileSync(process.env.PPGW_PYTHON || 'python', ['-c',
  "import sys,json;sys.path.insert(0,'integration');from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))"], {cwd:root,encoding:'utf8'}));

async function main() {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}, reducedMotion:'reduce'});
    const page = await context.newPage(), errors = [], posts = [];
    page.on('pageerror', error => errors.push(error.message));
    const sessions = sessionFixture();
    let fail = false, expire = false, pause = false, lost = false, reads = 0;
    const data = {installed:{version:'0.2.1'},candidate:null,available:false,operation:null};
    const model = {...defaults,revision:'a'.repeat(64),assets:[],source:{},management:{state:'loaded',recent:[]},mihomo_installed:true};
    await page.route(origin+'/**', async route => {
      const req = route.request(), target = new URL(req.url()).pathname;
      if (target.startsWith('/sniffbox/settings/gateway-update')) {
        assert.ok(sessions.authorized(req));
        if (req.method()==='POST') {
          const body = req.postDataJSON(); posts.push({target,body}); reads=0;
          data.operation={id:body.id,action:target.endsWith('/check')?'check':'update',status:'running',stage:target.endsWith('/check')?'checking':'downloading'};
          if (lost) { lost=false; return route.abort('failed'); }
          return route.fulfill({status:202,contentType:'application/json',body:JSON.stringify(data.operation)});
        }
        if (target.includes('/jobs/')) {
          assert.equal(target.split('/').pop(),data.operation.id);
          if (expire) return route.fulfill({status:401,contentType:'application/json',body:'{"error":"LOGIN_REQUIRED"}'});
          reads++;
          if (data.operation.action==='update' && reads<=2) return route.abort('failed');
          if (!pause && reads>=3) {
            data.operation.status=fail?'failed':'succeeded';data.operation.stage='complete';
            if (fail) data.operation.error='DEPLOY_FAILED_ROLLED_BACK';
            else if (data.operation.action==='check') {
              data.candidate={version:'0.2.2',token:'c'.repeat(64),release_url:'https://github.com/Lovest20018/LovestGateWay/releases/tag/v0.2.2'};data.available=true;
            } else { data.installed.version='0.2.2';data.available=false;data.operation.backup='20261106120000-abcdef012345'; }
          }
        }
        return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
      }
      if (target.startsWith('/sniffbox/settings/')) {
        const value=target.endsWith('/config')?model:{management:model.management,runtime:{status:'staged'},source:{}};
        return route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
      }
      if (target.startsWith('/sniffbox/')) return route.fulfill({contentType:'application/json',body:JSON.stringify({mode:'free',ready:false,operation:null})});
      try {
        const filename=path.join(output,target==='/ui/'?'index.html':path.basename(target));
        return route.fulfill({contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html',body:await fs.readFile(filename)});
      } catch { return route.fulfill({status:404,body:''}); }
    });
    await sessions.install(page);
    const open = async () => {
      await page.goto(origin+'/ui/#/settings');
      await page.getByRole('tab',{name:'维护与 API',exact:true}).click();
      await page.locator('#gateway-release-check').waitFor();
    };
    await open();
    assert.equal(await page.locator('#gateway-release-install').isDisabled(),true);
    await page.locator('#gateway-release-check').click();
    await page.waitForFunction(()=>!document.querySelector('#gateway-release-install')?.disabled);
    assert.equal(posts.length,1); assert.equal(posts[0].target.endsWith('/check'),true);
    assert.equal(await page.getByRole('link',{name:'查看此版本更新说明 ↗'}).getAttribute('href'),data.candidate.release_url);
    page.once('dialog',dialog=>dialog.dismiss());
    await page.locator('#gateway-release-install').click(); assert.equal(posts.length,1);
    pause=true; lost=true;
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#gateway-release-install').click();
    await page.waitForFunction(()=>document.querySelector('#notice')?.textContent.includes('暂时断连'));
    assert.equal(posts.length,2); assert.equal(posts[1].body.candidate,'c'.repeat(64));
    const retained=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('lovestgateway-release-job')));
    assert.equal(retained.id,posts[1].body.id);
    await page.reload();
    pause=false;
    await page.waitForFunction(()=>document.querySelector('#notice')?.textContent.includes('升级完成'),null,{timeout:20000});
    assert.equal(posts.length,2,'refresh and lost response must never repeat POST');
    await page.getByRole('tab',{name:'维护与 API',exact:true}).click();
    await page.getByText('升级成功；请刷新页面加载新版界面。',{exact:true}).waitFor();
    assert.equal(await page.locator('#gateway-release-install').isDisabled(),true);
    await page.screenshot({path:path.join(output,'gateway-update-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,
      JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,id:e.id,class:e.className,width:e.getBoundingClientRect().width})).slice(-15))));
    await page.screenshot({path:path.join(output,'gateway-update-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    data.installed.version='0.2.1';data.available=true;data.operation=null;
    await page.locator('#gateway-release-query').click();
    fail=true;page.once('dialog',dialog=>dialog.accept());await page.locator('#gateway-release-install').click();
    await page.getByText('升级验收失败，已恢复安装前的版本与配置。',{exact:true}).first().waitFor({timeout:15000});
    assert.equal(posts.length,3);assert.equal(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-release-job')),null);
    fail=false;expire=true;await page.locator('#gateway-release-check').click();
    await page.waitForFunction(()=>document.querySelector('#notice')?.textContent.includes('登录已失效'));
    assert.equal(posts.length,4);assert.ok(await page.evaluate(()=>sessionStorage.getItem('lovestgateway-release-job')));
    expire=false;await page.locator('#gateway-release-query').click();
    await page.waitForFunction(()=>!sessionStorage.getItem('lovestgateway-release-job'),null,{timeout:15000});
    assert.equal(posts.length,4,'resume after authentication may only query');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['check-only','pinned-release','cancel','lost-response','restart-refresh','success','rollback','401-resume','mobile'],posts:posts.length}));
  } finally { await browser.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
