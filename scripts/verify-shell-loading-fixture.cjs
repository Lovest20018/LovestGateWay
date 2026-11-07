const {sessionFixture}=require('./browser-session-fixture.cjs');
/* Synthetic route loading and mobile keyboard checks; no live gateway access. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const output=path.resolve(process.argv[2]),evidence=path.resolve(process.argv.find(value=>value.startsWith('--evidence='))?.slice(11)||'dist/ui-optimization-20261106/navigation'),origin='http://127.0.0.1:18941';
(async()=>{
  await fs.mkdir(evidence,{recursive:true});const browser=await chromium.launch({channel:'chrome',headless:true});
  let release;
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await context.newPage(),errors=[],modules=[];
    page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>localStorage.setItem('PaoPaoGateWay',JSON.stringify({clashAPIConfigs:[{secret:'fixture-token'}]})));
    let hold=true;
    const sessions=sessionFixture({proof:()=> 'fixture-token'});
    await context.route(origin+'/**',async route=>{
      const request=route.request(),target=new URL(request.url()).pathname;
      const reply=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
      if(target.startsWith('/sniffbox/')||['/configs','/connections'].includes(target)){
        assert.equal(request.method(),'GET');
        if(target.includes('proxies'))return reply({mode:'global',proxies:{GLOBAL:{type:'Selector',all:['DIRECT'],now:'DIRECT'},DIRECT:{type:'Direct'}}});
        if(target==='/connections')return reply({connections:null,downloadTotal:0,uploadTotal:0});
        if(target==='/configs')return reply({mode:'global'});
        return reply({mode:'yaml',timestamp:1793870400,uptime:12,cpu:{model:'Fixture CPU'},memory:{total:1024},interfaces:[]});
      }
      if(target.endsWith('.js'))modules.push(path.basename(target));
      if(hold&&/\/proxies\.[a-f0-9]+\.js$/.test(target))await new Promise(resolve=>{release=resolve;});
      try{return await route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
    });
    await sessions.install(context);
    await page.goto(origin+'/ui/#/overview');await page.locator('#overview-page [data-field=cpu-model]').getByText('Fixture CPU',{exact:true}).waitFor();
    assert.equal(modules.filter(name=>/^(editor|diagnostics|proxies|rules|connections|traffic|distribution|logs|region)\./.test(name)).length,0);
    await page.locator('#shell-nav [data-route=proxies]').click();
    for(let i=0;i<100&&!release;i++)await page.waitForTimeout(20);assert.equal(typeof release,'function');
    await page.locator('#shell-nav [data-route=connections]').click();await page.locator('#connections-page').waitFor();
    hold=false;release();await page.waitForTimeout(200);assert.equal(await page.locator('#proxies-page').count(),0,'late imports cannot mount an obsolete route');
    await page.goBack();await page.locator('#proxies-page').waitFor();await page.locator('#shell-nav [data-route=overview]').click();await page.locator('#overview-page').waitFor();
    const nav=page.locator('#shell-nav'),collapse=page.locator('#shell-collapse');
    assert.deepEqual(await nav.locator('[data-route] .shell-nav-label').allTextContents(),['概览','代理','规则','连接','流量','分布','日志','区域','订阅','设置','诊断']);
    assert.deepEqual(await nav.locator('.shell-nav-group-label').allTextContents(),['网络','观测','管理']);
    for(const width of [1440,1024,761]){
      await page.setViewportSize({width,height:743});
      const layout=await nav.evaluate(node=>{
        const rect=node.getBoundingClientRect(),brand=node.querySelector('.shell-brand').getBoundingClientRect(),toggle=node.querySelector('#shell-collapse').getBoundingClientRect();
        const group=node.querySelector('.shell-nav-group-label').getBoundingClientRect(),text=node.querySelector('.shell-nav-label').getBoundingClientRect(),footer=node.querySelector('.shell-nav-footer').getBoundingClientRect();
        return{width:rect.width,footerBottom:footer.bottom,brandRight:brand.right,toggleLeft:toggle.left,indent:text.left-group.left,overflow:document.documentElement.scrollWidth>innerWidth};
      });
      assert.equal(layout.width,244);assert.ok(layout.footerBottom<=743);assert.ok(layout.brandRight<=layout.toggleLeft);assert.ok(layout.indent>=24);assert.equal(layout.overflow,false);
    }
    await page.setViewportSize({width:1440,height:743});
    for(const theme of ['light','dark']){
      await page.getByRole('button',{name:theme==='light'?'亮色':'暗色',exact:true}).click();
      await nav.screenshot({path:path.join(evidence,`sidebar-expanded-${theme}.png`),animations:'disabled'});
    }
    await collapse.click();assert.equal(await collapse.getAttribute('aria-expanded'),'false');
    assert.equal(await collapse.evaluate(node=>node===document.activeElement),true);
    assert.equal(await nav.evaluate(node=>node.getBoundingClientRect().width),76);
    await page.waitForFunction(()=>document.querySelector('.shell-main').getBoundingClientRect().left===76);
    await nav.getByRole('link',{name:'连接',exact:true}).click();await page.locator('#connections-page').waitFor();
    assert.equal(await nav.getByRole('link',{name:'连接',exact:true}).getAttribute('aria-current'),'page');
    await page.reload();await page.locator('#connections-page').waitFor();
    assert.equal(await collapse.getAttribute('aria-label'),'展开侧边栏','desktop collapse survives a page reload');
    assert.equal(await nav.locator('[data-route=connections] .shell-nav-label').isVisible(),false);
    assert.equal(await nav.getByRole('link',{name:'诊断',exact:true}).getAttribute('title'),'诊断');
    for(const theme of ['light','dark']){
      await page.getByRole('button',{name:theme==='light'?'亮色':'暗色',exact:true}).click();
      await page.screenshot({path:path.join(evidence,`sidebar-collapsed-${theme}.png`),animations:'disabled'});
    }
    await nav.getByRole('link',{name:'概览',exact:true}).click();await page.locator('#overview-page').waitFor();
    for(const width of [375,320]){
      await page.setViewportSize({width,height:900});
      await page.waitForFunction(()=>!document.getElementById('app').classList.contains('shell-sidebar-collapsed'));
      for(const theme of ['light','dark']){
        await page.locator('#shell-menu').click();
        await page.getByRole('button',{name:'关闭导航',exact:true}).waitFor({state:'visible'});
        const mobileLabel=nav.locator('[data-route=overview] .shell-nav-label');
        await mobileLabel.waitFor({state:'visible'});
        assert.equal(await mobileLabel.isVisible(),true,'mobile drawer stays expanded despite desktop preference');
        assert.equal(await collapse.isVisible(),false);
        await page.getByRole('group',{name:'外观',exact:true}).getByRole('button',{name:theme==='light'?'亮色':'暗色',exact:true}).click();
        const focusable=nav.locator('a[href],button').filter({visible:true});
        await focusable.last().focus();await page.keyboard.press('Tab');assert.equal(await focusable.first().evaluate(node=>node===document.activeElement),true);
        await page.keyboard.press('Shift+Tab');assert.equal(await focusable.last().evaluate(node=>node===document.activeElement),true);
        await page.screenshot({path:path.join(evidence,`drawer-${width}-${theme}.png`)});
        await page.keyboard.press('Escape');assert.equal(await page.locator('#shell-menu').evaluate(node=>node===document.activeElement),true);
        await page.locator('#shell-menu').click();await page.locator('#shell-backdrop').click({position:{x:width-8,y:500}});
        assert.equal(await page.locator('#shell-backdrop').isVisible(),false);
        await page.locator('#overview-page h1').evaluate(node=>{node.tabIndex=-1;node.focus();});await page.keyboard.press('Escape');
        assert.equal(await page.locator('#overview-page h1').evaluate(node=>node===document.activeElement),true,'closed drawer cannot steal Escape focus');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      }
    }
    await page.setViewportSize({width:1024,height:540});
    await page.waitForFunction(()=>document.getElementById('app').classList.contains('shell-sidebar-collapsed'));
    assert.equal(await collapse.getAttribute('aria-label'),'展开侧边栏','mobile resizing preserves desktop preference');
    await collapse.click();await nav.getByRole('link',{name:'诊断',exact:true}).scrollIntoViewIfNeeded();
    assert.equal(await nav.getByRole('link',{name:'诊断',exact:true}).isVisible(),true);
    assert.ok(await page.locator('#shell-logout').evaluate(node=>node.getBoundingClientRect().bottom<=innerHeight),'footer stays available in short windows');
    await page.screenshot({path:path.join(evidence,'sidebar-short-window.png')});
    const connectionOnly=await context.newPage(),before=modules.length;await connectionOnly.goto(origin+'/ui/#/connections');await connectionOnly.locator('#connections-page').waitFor();assert.equal(modules.slice(before).some(name=>name.startsWith('overview.')),false,'shared formatting cannot pull in the overview route');await connectionOnly.close();
    assert.deepEqual(errors,[]);
    const report={syntheticOnly:true,lazyInitialRoutes:true,obsoleteImportIgnored:true,backNavigation:true,desktopWidths:[1440,1024,761],groupedShortLabels:true,indentedLinks:true,persistentDesktopCollapse:true,collapsedNavigation:true,mobileIgnoresDesktopCollapse:true,shortWindowFooter:true,mobileWidths:[375,320],themes:['light','dark'],backdrop:true,focusTrap:true,escapeRestoresFocus:true,closedEscapePreservesFocus:true,pageErrors:errors};
    await fs.writeFile(path.join(evidence,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));await context.close();
  }finally{release?.();for(const context of browser.contexts())for(const page of context.pages())await page.unrouteAll({behavior:'ignoreErrors'});await browser.close();}
})().then(()=>process.exit(0),error=>{console.error(error);process.exit(1);});
