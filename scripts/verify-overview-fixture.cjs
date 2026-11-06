/* Maintained overview acceptance. All requests use intercepted synthetic origins. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),output=path.resolve(process.argv[2]||path.join(root,'frontend/dist'));
const evidence=path.join(root,'dist/selection-overview-20261105'),origin='http://127.0.0.1:18891';
const fixture={version:'20261105.fixture',clash_version:'v1.19.33',clash_meta:true,mode:'suburl',kernel:'6.1.fixture',
  timestamp:1793880000,uptime:90061,integration:{data:{boot_id:'fixture-boot-1'}},
  memory:{total:8*1024**3,available:5*1024**3,sniffbox_rss:40*1024**2,sniffbox_uptime:600,clash_rss:80*1024**2,clash_uptime:599,cloudflared_rss:20*1024**2,cloudflared_uptime:500},
  cpu:{model:'Fixture CPU 4-core',cores:4,freq_min:800,freq_max:4200,usage:12.5,per_core:[10,15,20,5],freq:2400,freq_per_core:[2200,2400,2600,2400],temp:51.5,temp_sensor:'Fixture sensor',sniffbox:2.5,clash:3.5,cloudflared:1},
  interfaces:[{name:'ens18',ipv4:['192.0.2.10/24'],ipv6:['2001:db8::10/64'],gateway:'192.0.2.1',mac:'00:11:22:33:44:55',model:'Fixture NIC',speed:1000,duplex:'full',link:true},
    {name:'<img src=x onerror="window.untrustedRan=true">',ipv4:[],ipv6:[],link:false}],
  dns:{trusted:{ip:'1.1.1.1',port:53},ex_dns:['9.9.9.9'],system:['192.0.2.1']},acl:{admin_cidr:['192.0.2.0/24'],proxy_cidr:['2001:db8::/32']},
  features:{net_rec:true,dns_burn:false,openport:false,openport_port:1080,udp_enable:true,max_rec:9000,net_cleanday:7},
  route:{block_bittorrent:true,block_quic:false,block_unknown:false},fakeip:{cidr:'198.18.0.0/15',mapped:82},
  connections:{tcp:12,udp:3,active:15,clients:4,fakeip:10,realip:5},traffic:{downloadTotal:1024**3,uploadTotal:1024**2},
  pool:{idle:8,min:10,max:20,cold_acquires:3,discarded_dead:1,warmed:15,acquires:100}};
(async()=>{
  await fs.mkdir(evidence,{recursive:true});const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce'}),page=await context.newPage(),errors=[],requests=[];
    page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{localStorage.setItem('PaoPaoGateWay',JSON.stringify({clashAPIConfigs:[{secret:'fixture-token'}]}));localStorage.setItem('debug','1');});
    let expectedSecret='fixture-token',dynamic=0,status=200,boot='fixture-boot-1',reset=false,hold=false,releaseHeld;
    async function capture(name){const viewport=page.viewportSize(),height=await page.evaluate(()=>document.documentElement.scrollHeight);await page.setViewportSize({width:viewport.width,height:Math.max(viewport.height,height)});await page.evaluate(async()=>{scrollTo(0,0);await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});await page.screenshot({path:path.join(evidence,name),fullPage:true,animations:'disabled'});await page.setViewportSize(viewport);}
    await page.route(origin+'/**',async route=>{
      const request=route.request(),target=new URL(request.url()).pathname;requests.push({target,method:request.method()});
      const reply=(data,code=200)=>route.fulfill({status:code,contentType:'application/json',body:JSON.stringify(data)});
      if(target.startsWith('/sniffbox/')){
        assert.equal(request.method(),'GET','overview cannot mutate the gateway');
        if(request.headers().authorization!=='Bearer '+expectedSecret)return reply({error:'LOGIN_REQUIRED'},401);
        if(target==='/sniffbox/info/dynamic'){
          if(hold)await new Promise(resolve=>{releaseHeld=resolve;});
          if(status!==200)return reply({error:'fixture'},status);
          dynamic++;return reply({timestamp:fixture.timestamp+dynamic,uptime:fixture.uptime+dynamic,integration:{data:{boot_id:boot}},
            memory:{available:4*1024**3},cpu:{usage:22.5,per_core:[20,25,30,15]},
            traffic:{downloadTotal:reset?2048:fixture.traffic.downloadTotal+dynamic*1024*80,uploadTotal:reset?1024:fixture.traffic.uploadTotal+dynamic*1024*10},
            connections:fixture.connections,fakeip:{mapped:83},pool:{...fixture.pool,cold_acquires:3+dynamic,acquires:100+dynamic}});
        }
        if(target==='/sniffbox/info')return reply({...fixture,integration:{data:{boot_id:boot}}});
        if(target==='/sniffbox/geo')return reply({version:'2026-11-05.fixture'});
        if(target==='/sniffbox/proxies')return reply({proxies:{GLOBAL:{name:'GLOBAL',type:'Selector',now:'Fixture Node',all:['Fixture Node','DIRECT'],history:[]},'Fixture Node':{name:'Fixture Node',type:'Shadowsocks',history:[]},DIRECT:{name:'DIRECT',type:'Direct',history:[]}}});
        return reply({});
      }
      if(target==='/configs')return reply({mode:'global'});
      if(target==='/connections')return reply({connections:[],downloadTotal:0,uploadTotal:0});
      try{return route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
    });
    await page.goto(origin+'/ui/#/overview');const overview=page.locator('#overview-page');await overview.getByText('Fixture CPU 4-core',{exact:true}).waitFor();
    assert.equal(await overview.locator('[data-field=mode]').textContent(),'订阅');await page.evaluate(()=>window.documentIdentity='overview-source');
    assert.equal(requests.filter(row=>row.target.includes('bundle.')).length,0,'overview must not mount the compatibility runtime');
    assert.equal(await page.locator('#runtime-panel').count(),0);
    await overview.locator('[data-field=memory-total]').getByText('8.0 GB',{exact:true}).waitFor();
    await overview.locator('[data-field=rate-down]').filter({hasText:/KB\/s/}).waitFor();
    assert.equal(await overview.locator('[data-field=memory-total]').innerText(),'8.0 GB','dynamic nested fields must retain static totals');
    assert.equal(await overview.locator('[data-field=cpu-model]').innerText(),'Fixture CPU 4-core');
    assert.equal(await overview.locator('[data-cores]>.overview-core').count(),4);
    assert.equal(await overview.locator('[data-interfaces] tr').count(),2);
    assert.equal(await overview.locator('[data-interfaces] img').count(),0);assert.equal(await page.evaluate(()=>window.untrustedRan),undefined);
    await overview.locator('canvas').first().hover();await overview.locator('.overview-chart-tip').first().waitFor({state:'visible'});
    await capture('overview-source-desktop.png');
    status=503;await overview.locator('.overview-status').filter({hasText:'HTTP 503'}).waitFor();
    assert.equal(await overview.locator('[data-field=memory-total]').innerText(),'8.0 GB');status=200;
    await overview.locator('.overview-status').waitFor({state:'hidden'});
    reset=true;boot='fixture-boot-2';await overview.locator('[data-field=traffic-down]').getByText('2.0 KB',{exact:true}).waitFor();
    assert.ok(!/GB\/s/.test(await overview.locator('[data-field=rate-down]').innerText()),'counter reset must not spike rates');
    const password='OverviewFixturePassword8';expectedSecret=require('node:crypto').createHash('sha256').update(password).digest('hex');
    await page.getByRole('button',{name:'重新登录',exact:true}).waitFor();await page.getByRole('button',{name:'重新登录',exact:true}).click();
    await page.locator('#reauth-panel').getByLabel('管理密码',{exact:true}).fill(password);await page.locator('#reauth-panel').getByRole('button',{name:'登录',exact:true}).click();
    await page.locator('#reauth-panel').waitFor({state:'detached'});await overview.locator('.overview-status').waitFor({state:'hidden'});
    assert.equal(await page.evaluate(()=>window.documentIdentity),'overview-source');
    await page.getByRole('group',{name:'外观',exact:true}).getByRole('button',{name:'暗色',exact:true}).click();
    await capture('overview-source-dark.png');
    await page.locator('#shell-nav [data-route=proxies]').click();
    await page.locator('#proxies-page').getByRole('heading',{name:'代理',exact:true}).waitFor();
    const stoppedAt=dynamic;await page.waitForTimeout(1200);assert.equal(dynamic,stoppedAt,'leaving overview must stop source polling');
    await page.goBack();await page.waitForURL('**/#/overview');await page.locator('#overview-page').waitFor();
    assert.equal(await page.locator('#runtime-panel').isVisible(),false);assert.equal(await page.evaluate(()=>window.documentIdentity),'overview-source');
    await page.goForward();await page.waitForURL('**/#/proxies');await page.locator('#proxies-page').getByRole('heading',{name:'代理',exact:true}).waitFor();
    await page.locator('#shell-nav [data-route=overview]').click();
    for(const width of [390,320]){
      await page.setViewportSize({width,height:900});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await capture('overview-source-mobile-'+width+'.png');
    }
    hold=true;for(let i=0;i<100&&!releaseHeld;i++)await page.waitForTimeout(50);assert.equal(typeof releaseHeld,'function');
    await page.locator('#shell-menu').click();await page.locator('#shell-nav [data-route=connections]').click();
    await page.locator('#overview-page').waitFor({state:'detached'});hold=false;releaseHeld();await page.waitForTimeout(100);assert.equal(await page.locator('#overview-page').count(),0);assert.deepEqual(errors,[]);
    delete fixture.cpu.cloudflared;delete fixture.memory.cloudflared_rss;expectedSecret='fixture-token';await page.goto(origin+'/ui/?without-cloudflared#/overview');await page.locator('#overview-page [data-field=cpu-model]').getByText('Fixture CPU 4-core',{exact:true}).waitFor();assert.equal(await page.locator('#overview-page canvas[aria-label*=Cloudflared]').count(),0);assert.doesNotMatch(await page.locator('[data-cpu-legend]').textContent(),/Cloudflared/);
    const report={syntheticOnly:true,sourceOverviewWithoutRuntimeBundle:true,readOnlyRequests:true,nestedStaticFieldsRetained:true,trafficSamples:true,counterAndBootReset:true,apiFailureRecovery:true,reauthWithoutReload:true,untrustedTextOnly:true,chartHover:true,theme:true,mobileWidths:[390,320],backForward:true,pollingDisposed:true,lateResponseIgnored:true,pageErrors:errors};
    await fs.writeFile(path.join(evidence,'overview-browser-evidence.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));await context.close();
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
