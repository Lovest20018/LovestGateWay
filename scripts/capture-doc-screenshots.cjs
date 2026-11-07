/* Render the maintained UI with synthetic data; never contact a running gateway. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const {sessionFixture}=require('./browser-session-fixture.cjs');
const root=path.resolve(__dirname,'..');
const ui=path.resolve(process.argv[2]||path.join(root,'dist/docs-ui'));
const destination=path.resolve(process.argv[3]||path.join(root,'docs/images'));
const origin='http://127.0.0.1:18897';
const names=['🇭🇰 香港 01','🇭🇰 香港 02','🇯🇵 日本 01','🇯🇵 日本 02','🇸🇬 新加坡 01','🇺🇸 美国 01','🇩🇪 德国 01','🇬🇧 英国 01'];
const delays=[28,36,51,58,67,142,188,201];
const proxies={GLOBAL:{type:'Selector',all:['节点选择','自动选择','流媒体','DIRECT'],now:'节点选择'},
  '节点选择':{type:'Selector',all:[...names,'DIRECT'],now:names[0]},
  '自动选择':{type:'URLTest',all:names,now:names[0]},
  '流媒体':{type:'Selector',all:[names[2],names[4],names[5]],now:names[2]},DIRECT:{type:'Direct',delay:1}};
for(const [i,name]of names.entries())proxies[name]={type:'Shadowsocks',udp:true,alive:true,delay:delays[i],hist:[delays[i]+4,delays[i]-2,delays[i]+1,delays[i]]};
const info={version:'0.2.4 · 演示',clash_version:'v1.19.33',clash_meta:true,mode:'suburl',kernel:'6.1',
  timestamp:1794038400,uptime:90061,integration:{data:{boot_id:'documentation-demo'}},
  memory:{total:4*1024**3,available:2.9*1024**3,sniffbox_rss:42*1024**2,sniffbox_uptime:90000,clash_rss:86*1024**2,clash_uptime:90000},
  cpu:{model:'4 核虚拟处理器',cores:4,freq_min:800,freq_max:3200,usage:12.5,per_core:[10,15,20,5],freq:2400,temp:46.5,sniffbox:2.5,clash:3.5},
  interfaces:[{name:'ens18',ipv4:['192.0.2.10/24'],ipv6:[],gateway:'192.0.2.1',mac:'02:00:00:00:00:10',model:'VirtIO',speed:1000,duplex:'full',link:true}],
  dns:{trusted:{ip:'1.1.1.1',port:53},ex_dns:[],system:['192.0.2.1']},
  acl:{admin_cidr:['192.0.2.0/24'],proxy_cidr:['192.0.2.0/24']},
  features:{net_rec:true,dns_burn:false,openport:false,udp_enable:true,max_rec:10000,net_cleanday:7},
  route:{block_bittorrent:true,block_quic:false,block_unknown:false},fakeip:{cidr:'198.18.0.0/15',mapped:328},
  connections:{tcp:42,udp:8,active:50,clients:6,fakeip:38,realip:12},
  traffic:{downloadTotal:24*1024**3,uploadTotal:3*1024**3}};

(async()=>{
  await fs.mkdir(destination,{recursive:true});
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1,reducedMotion:'reduce',locale:'zh-CN',timezoneId:'Asia/Shanghai'});
    const page=await context.newPage(),errors=[],unexpected=[];
    let sample=0,download=info.traffic.downloadTotal,upload=info.traffic.uploadTotal;
    const reading=()=>{const cpu=12+Math.sin(sample*.8)*4;return {timestamp:info.timestamp+sample,uptime:info.uptime+sample,integration:info.integration,
      memory:{...info.memory,available:info.memory.available-sample*1024**2},cpu:{...info.cpu,usage:cpu,per_core:[cpu-2,cpu+2,cpu,cpu+1]},
      traffic:{downloadTotal:download,uploadTotal:upload},connections:info.connections,fakeip:info.fakeip};};
    page.setDefaultTimeout(15000);
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{
      localStorage.setItem('ppgw-theme','light');
      localStorage.setItem('ppgw-proxy-settings',JSON.stringify({gcollapse:{GLOBAL:true,'自动选择':true,'流媒体':true},showUdp:true}));
    });
    await page.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin!==origin){unexpected.push(url.origin);return route.abort();}
      const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
      if(request.method()!=='GET'){unexpected.push(request.method()+' '+url.pathname);return route.abort();}
      if(url.pathname==='/sniffbox/info')return json({...info,...reading()});
      if(url.pathname==='/sniffbox/info/dynamic'){
        sample++;download+=(1.5+Math.sin(sample*.5)*.3)*1024**2;upload+=(180+Math.cos(sample*.5)*25)*1024;
        return json(reading());
      }
      if(url.pathname==='/sniffbox/geo')return json({version:'2026-11-07'});
      if(url.pathname==='/sniffbox/proxies')return json({mode:'rule',testUrl:'https://example.com/generate_204',proxies,providers:{},subs:[]});
      if(url.pathname==='/configs')return json({mode:'rule'});
      if(url.pathname==='/connections')return json({connections:[],downloadTotal:0,uploadTotal:0});
      if(url.pathname.startsWith('/sniffbox/')){unexpected.push(url.pathname);return route.fulfill({status:404,body:''});}
      const name=url.pathname==='/ui/'?'index.html':path.basename(url.pathname);
      const mime={'.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2','.woff':'font/woff','.png':'image/png','.html':'text/html'};
      try{return route.fulfill({body:await fs.readFile(path.join(ui,name)),contentType:mime[path.extname(name)]||'application/octet-stream'});}
      catch{unexpected.push(url.pathname);return route.fulfill({status:404,body:''});}
    });
    await sessionFixture().install(page);
    await page.clock.install({time:new Date('2026-11-07T08:00:00Z')});
    await page.goto(origin+'/ui/#/overview');
    await page.getByText('4 核虚拟处理器',{exact:true}).waitFor();
    for(let i=0;i<100;i++){
      await page.clock.runFor(1100);await page.waitForTimeout(30);
      if(Number(await page.locator('.overview-chart').first().getAttribute('data-samples'))>=40)break;
    }
    assert.equal(await page.locator('.overview-chart').first().getAttribute('data-samples'),'40');
    await page.evaluate(()=>document.fonts.ready);
    await page.screenshot({path:path.join(destination,'overview.png'),animations:'disabled'});
    await page.locator('[data-route=proxies]').click();
    await page.locator('.proxy-node').first().waitFor();
    await page.setViewportSize({width:1440,height:800});
    await page.screenshot({path:path.join(destination,'proxies.png'),animations:'disabled'});
    await page.locator('#shell-theme').click();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(destination,'mobile-dark.png'),animations:'disabled'});
    assert.deepEqual(errors,[],'documentation pages must render without errors');
    assert.deepEqual(unexpected,[],'all requests must stay in the synthetic fixture');
    console.log('Captured 3 documentation screenshots with synthetic data only.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
