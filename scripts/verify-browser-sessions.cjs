/* Synthetic UI protocol regression. Rust HTTP tests enforce the actual sessions. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path'),{createHash}=require('node:crypto');
const {sessionFixture}=require('./browser-session-fixture.cjs');
const {chromium}=require(process.env.PPGW_PLAYWRIGHT_MODULE||'playwright');
const output=path.resolve(process.argv[2]),origin='http://127.0.0.1:18948',password='synthetic-password',proof=createHash('sha256').update(password).digest('hex');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const context=await browser.newContext(),page=await context.newPage(),errors=[],requests=[];
    const sessions=sessionFixture({proof:()=>proof,initial:false});let failLogout=true;
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{if(!localStorage.getItem('fixture-initialized')){localStorage.setItem('fixture-initialized','true');localStorage.setItem('PaoPaoGateWay',JSON.stringify({secret:'old',clashAPIConfigs:[{name:'one',secret:'old-one'},{name:'two',secret:'old-two'}],preference:'retained'}));}});
    await context.route(origin+'/**',async route=>{
      const request=route.request(),target=new URL(request.url()).pathname;
      if(!target.startsWith('/ui/')){
        requests.push({target,method:request.method(),authorization:request.headers().authorization,body:request.postData()});
        const reply=(status,data,headers={})=>route.fulfill({status,contentType:'application/json',headers,body:JSON.stringify(data)});
        if(target==='/sniffbox/auth/logout'&&failLogout)return reply(503,{error:'unavailable'});
        const auth=sessions.handle(request);if(auth)return reply(auth.status,auth.data,auth.headers);
        if(!sessions.authorized(request))return reply(401,{error:'LOGIN_REQUIRED'});
        if(target==='/connections')return reply(200,{connections:null,uploadTotal:0,downloadTotal:0});
        if(target==='/configs')return reply(200,{mode:'global'});
        return reply(200,{mode:'suburl',timestamp:1793870400,uptime:1,interfaces:[]});
      }
      try{return await route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return route.fulfill({status:404,body:''});}
    });
    await page.goto(origin+'/ui/#/connections');await page.getByLabel('管理密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('#connections-page').waitFor();
    const cookies=await context.cookies();const cookie=cookies.find(row=>row.name==='ppgw_session');assert.ok(cookie?.httpOnly);assert.equal(cookie.sameSite,'Strict');
    assert.equal(await page.evaluate(()=>document.cookie.includes('ppgw_session')),false);
    const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('PaoPaoGateWay')));assert.equal(stored.preference,'retained');assert.equal(stored.secret,'');assert.deepEqual(stored.clashAPIConfigs,[{name:'one',secret:''},{name:'two',secret:''}]);
    assert.equal(await page.evaluate(()=>JSON.stringify({...localStorage}).includes('synthetic-password')),false);
    await page.reload();await page.locator('#connections-page').waitFor();assert.equal(requests.filter(row=>row.target.endsWith('/login')).length,1);
    await page.getByRole('button',{name:'退出登录',exact:true}).click();await page.getByText('退出未确认，请重试以撤销服务器会话。',{exact:true}).waitFor();assert.equal(await page.locator('#connections-page').isVisible(),true);
    failLogout=false;await page.getByRole('button',{name:'退出登录',exact:true}).click();await page.getByLabel('管理密码',{exact:true}).waitFor();assert.equal((await context.cookies()).some(row=>row.name==='ppgw_session'),false);
    assert.ok(requests.every(row=>row.authorization===undefined&&!row.target.includes(password)&&!row.target.includes(proof)));
    assert.deepEqual(JSON.parse(requests.find(row=>row.target.endsWith('/login')).body),{proof});assert.deepEqual(errors,[]);
    console.log(JSON.stringify({syntheticOnly:true,httpOnlyCookie:true,noPersistentProof:true,legacyCredentialsCleared:true,preferencesRetained:true,refreshRestoresSession:true,failedLogoutRetryable:true,confirmedLogoutClearsSession:true,noPermanentBearer:true,pageErrors:errors}));
    await context.close();
  }finally{await browser.close();}
})().then(()=>process.exit(0),error=>{console.error(error);process.exit(1);});
