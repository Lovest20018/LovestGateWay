/* Large synthetic subscription documents; no deployed gateway or real providers. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PPGW_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, 'dist/complex-editor-ui'));
const evidenceDir = path.resolve(process.argv[3] || path.join(root, 'dist/complex-editor-evidence'));
const origin = 'http://complex-fixture.invalid';
function python(code, value) {
  return JSON.parse(execFileSync(process.env.PPGW_PYTHON || 'python', ['-X','utf8','-c',
    "import sys,json;sys.path.insert(0,'integration');" + code],
  {cwd:root, input:JSON.stringify(value), encoding:'utf8', maxBuffer:16*1024*1024}));
}
const defaults = python("from manage_model import SCHEMA,SECRET_KEYS,public_settings;s={ns:{k:v[1] for k,v in f.items()} for ns,f in SCHEMA.items()};s['ini'].update(mode='suburl',suburl='ppsub@http://127.0.0.1/ppsub.json');print(json.dumps({'settings':public_settings(s),'schema':{ns:{k:{'type':v[0],'secret':k in SECRET_KEYS,'constraints':v[2:]} for k,v in f.items()} for ns,f in SCHEMA.items()}}))", null);
const counts = {providers:12, groups:60, rules:200, nodes:1000};
const suffix = '跨区域多机场长名称'.repeat(9) + '<img src=x onerror=alert(1)>';
const providerNames = Array.from({length:counts.providers}, (_,i)=>`机场 ${i+1} ${suffix}`);
const groupNames = Array.from({length:counts.groups}, (_,i)=>`策略组 ${i+1} ${suffix}`);
const nodeNames = Array.from({length:counts.nodes}, (_,i)=>`节点 ${i+1} ${suffix}`);
const raw = {secret:'synthetic-core-secret', dns:{nameserver:['1.1.1.1']},
  proxies:nodeNames.map((name,i)=>({name,type:'socks5',server:'127.0.0.1',port:10000+i,password:`hidden-${i}`})),
  'proxy-providers':Object.fromEntries(providerNames.map((name,i)=>[name,{type:'http',url:`https://example.com/provider-${i}`,path:`./saved-${i}.yaml`,header:{Authorization:[`hidden-provider-${i}`]}}])),
  'proxy-groups':groupNames.map((name,i)=>({name,type:'select',use:[providerNames[i%counts.providers]],proxies:['DIRECT',nodeNames[i]],hidden:i%2===0,lazy:true})),
  rules:Array.from({length:counts.rules}, (_,i)=>`DOMAIN,fixture-${i}.example.com,${groupNames[i%counts.groups]}`)};
const baseline = python('from clash_ppsub import editor_config;print(json.dumps(editor_config(json.load(sys.stdin))[0]))', raw);
const native = {version:'PaoPaoGateWay 3.1', custom:{untouched:{credential:'hidden-extension'}},
  subs:providerNames.map((name,i)=>({name,url:`https://example.com/provider-${i}`,update_mode:i%2?'auto':'manual',update_interval:'6h',extension:{credential:`hidden-${i}`}})),
  'node-groups':groupNames.map((name,i)=>({name,subs:[providerNames[i%counts.providers]],include:['DIRECT',nodeNames[i]],keywords:[`关键词-${i}-${suffix}`],exclude_keywords:['expired'],extension:{keep:i}})),
  rules:Array.from({length:counts.rules}, (_,i)=>({priority:(i+1)*10,remark:`规则 ${i+1} ${suffix}`,fixrule:[`DOMAIN,fixture-${i}.example.com,${groupNames[i%counts.groups]}`],extension:{keep:i}}))};

async function exercise(browser, kind, width) {
  const context = await browser.newContext({viewport:{width,height:900}, reducedMotion:'reduce'});
  const page = await context.newPage(), errors = [], unexpected = [];
  page.setDefaultTimeout(30000);
  page.on('pageerror', error=>errors.push(error.message));
  await page.addInitScript(()=>localStorage.setItem('PaoPaoGateWay',JSON.stringify({clashAPIConfigs:[{secret:'fixture-token'}]})));
  let config = structuredClone(kind==='ppsub'?native:baseline), posted, operation, merged, providerPolls=0;
  const initial = structuredClone(config);
  const model = {...structuredClone(defaults),revision:'a'.repeat(64),assets:[],source:{native_ppsub:kind==='ppsub',local_policy:kind==='clash_patch'},mihomo_installed:true};
  await page.route('**/*', async route=>{
    const request = route.request(), url = new URL(request.url()), target=url.pathname;
    const reply = (value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if(url.origin!==origin){unexpected.push(request.url());return route.abort();}
    if(target.startsWith('/sniffbox/')) {
      if(target==='/sniffbox/info')return reply({mode:'suburl',ppsub:kind==='ppsub',clash_version:'fixture'});
      if(target==='/sniffbox/settings/config')return reply(model);
      if(target==='/sniffbox/settings/status')return reply({management:{state:'loaded',recent:[]},running_mode:'suburl',source:model.source});
      if(target==='/sniffbox/subscription/status')return reply({edit_kind:kind,base_revision:model.revision,ppsub_write_allowed:true,operation});
      if(target==='/sniffbox/subscription/providers') {
        providerPolls++;
        return reply({subscriptions:providerNames.map((name,i)=>({name,nodes:80+i,result:'saved',attempted_at:1700000000,succeeded_at:1700000000,update_mode:'manual'}))});
      }
      if(target==='/sniffbox/subscription/config')return reply({edit_kind:kind,config,base_revision:model.revision,settings_revision:model.revision,snapshot_id:'complex-fixture',write_allowed:true,node_choices:nodeNames});
      if(target==='/sniffbox/subscription/apply') {
        const body=request.postDataJSON(), submitted=JSON.parse(body.value);
        if(kind==='ppsub')python("from unified_editor import validate_ppsub;v=json.load(sys.stdin);validate_ppsub(v);print(json.dumps({'valid':True}))",submitted);
        else merged=python("from clash_ppsub import merge_editor,feature_options;v=json.load(sys.stdin);feature_options(v['submitted']);print(json.dumps(merge_editor(v['raw'],v['baseline'],v['submitted'])[0]))",{raw,baseline,submitted});
        if(body.dry_run)return reply({action:'restart',editor_changed:true,changes:{},application:{action:'restart',core_active:true,services:[]}});
        posted=submitted;config=submitted;operation={status:'applied',message:'Complex fixture saved'};return reply(operation,202);
      }
      return reply({});
    }
    try {return route.fulfill({body:await fs.readFile(path.join(output,target==='/ui/'?'index.html':path.basename(target))),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.svg')?'image/svg+xml':'text/html'});}
    catch {return route.fulfill({status:404,body:''});}
  });
  const timings = {};
  const measure = async (name, action)=>{const begin=performance.now();await action();timings[name]=Math.round(performance.now()-begin);};
  const ready=()=>page.waitForFunction(()=>document.querySelector('.source-editor')?.dataset.editorState==='loaded');
  const groups=page.locator('.groups > .item');
  const noOverflow=async()=>{
    const size=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert.ok(size.scroll<=size.width+1,JSON.stringify(size));
    return size;
  };
  try {
    await measure('load',async()=>{await page.goto(origin+'/ui/#/subscriptions');await ready();assert.equal(await groups.count(),counts.groups);});
    assert.equal(await page.locator('.subs > .item').count(),counts.providers);
    assert.equal(await page.locator('.rules > .item').count(),kind==='ppsub'?counts.rules:1);
    assert.equal(await page.locator('.source-editor img').count(),0,'long names must remain text');
    await noOverflow();
    const target=groups.nth(counts.groups-1), editedName=`Edited ${kind} ${width} ${suffix}`;
    await measure('editLastGroup',()=>target.locator('[data-field=name]').fill(editedName));
    const pollBefore=providerPolls;
    await page.waitForFunction(()=>document.querySelectorAll('[data-provider-status]').length===12);
    if(kind==='ppsub') {
      // Observe a real timer refresh with the draft still present.
      const deadline=Date.now()+12000;
      while(providerPolls<=pollBefore&&Date.now()<deadline)await page.waitForTimeout(200);
      assert.ok(providerPolls>pollBefore,'provider polling must continue during a large edit');
    }
    assert.equal(await target.locator('[data-field=name]').inputValue(),editedName);
    await target.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidenceDir,`${kind}-${width}-groups.png`),animations:'disabled'});
    await measure('copyLastGroup',async()=>{await target.getByRole('button',{name:'复制',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.groups > .item').length===61);});
    assert.equal(await groups.last().locator('[data-field=name]').inputValue(),editedName+' 副本');
    await measure('moveCopiedGroup',async()=>{await groups.last().getByRole('button',{name:'上移',exact:true}).click();assert.equal(await groups.nth(59).locator('[data-field=name]').inputValue(),editedName+' 副本');});
    page.once('dialog',dialog=>dialog.accept());
    await measure('deleteCopiedGroup',async()=>{await groups.nth(59).getByRole('button',{name:'删除',exact:true}).click();assert.equal(await groups.count(),counts.groups);});
    const fixed=groups.last().getByRole('group',{name:'固定节点 / 组',exact:true});
    await fixed.locator(':scope > .btn').click();
    await fixed.locator('[data-field=include]').fill(nodeNames.at(-1));
    await fixed.locator('[data-field=include]').press('Tab');
    await noOverflow();
    await groups.last().getByRole('radio',{name:'自动测速',exact:true}).check();
    await groups.last().locator('[data-field=speedtest_url]').fill('https://example.com/speed');
    await groups.last().locator('[data-field=interval]').fill('900');
    await page.getByRole('tab',{name:'配置来源',exact:true}).click();
    await page.getByRole('tab',{name:'策略编辑',exact:true}).click();
    assert.equal(await groups.last().locator('[data-field=name]').inputValue(),editedName);
    const provider=page.locator('.subs > .item').nth(7);
    await provider.getByRole('button',{name:'修改地址',exact:true}).click();await provider.locator('[data-field=url]').fill('https://example.com/edited-provider');
    if(kind==='ppsub') {
      await provider.locator('[data-provider-mode]').selectOption('auto');
      await provider.locator('[data-provider-interval]').fill('7h');
    }
    await provider.scrollIntoViewIfNeeded();await noOverflow();
    await page.screenshot({path:path.join(evidenceDir,`${kind}-${width}-providers.png`),animations:'disabled'});
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.locator('#preview-dialog').waitFor({state:'visible'});await noOverflow();
    await page.locator('#apply').click();await page.getByText('Complex fixture saved',{exact:true}).waitFor();await ready();
    assert.equal(posted.subs.length,counts.providers);assert.equal(posted['node-groups'].length,counts.groups);
    assert.equal(posted['node-groups'].at(-1).name,editedName);
    assert.ok(posted['node-groups'].at(-1).include.includes(nodeNames.at(-1)));
    assert.equal(posted['node-groups'].at(-1).interval,900);
    assert.equal(posted.subs[7].url,'https://example.com/edited-provider');
    for(let i=0;i<counts.groups-1;i++)assert.deepEqual(posted['node-groups'][i],initial['node-groups'][i],'untouched group '+i);
    assert.deepEqual(posted.rules,initial.rules,'all untouched rules and priorities retained');
    if(kind==='ppsub') {
      assert.equal(posted.subs[7].update_interval,'7h');assert.deepEqual(posted.custom,native.custom);
      for(let i=0;i<counts.providers;i++)assert.deepEqual(posted.subs[i].extension,native.subs[i].extension);
    } else {
      assert.deepEqual(merged.proxies,raw.proxies);assert.deepEqual(merged.dns,raw.dns);
      const expectedRules=Array.from({length:counts.rules},(_,i)=>`DOMAIN,fixture-${i}.example.com,${i%counts.groups===counts.groups-1?editedName:groupNames[i%counts.groups]}`);
      assert.deepEqual(merged.rules,expectedRules,'renamed group references update without reordering rules');assert.equal(merged.secret,raw.secret);
      for(const name of providerNames)assert.deepEqual(merged['proxy-providers'][name].header,raw['proxy-providers'][name].header);
    }
    assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
    return {kind,width,counts,timings,providerPolls,backendValidated:true,untouchedDataPreserved:true,copyMoveDelete:true,tabsRetainDraft:true,longNamesAsText:true,noHorizontalOverflow:true,pageErrors:errors,unexpectedNetwork:unexpected};
  } catch(error) {
    await page.screenshot({path:path.join(evidenceDir,`failure-${kind}-${width}.png`)}).catch(()=>{});
    await fs.writeFile(path.join(evidenceDir,`failure-${kind}-${width}.json`),JSON.stringify({timings,providerPolls,errors,unexpected,error:String(error)},null,2)+'\n');
    throw error;
  } finally {for(const tab of context.pages())await tab.unrouteAll({behavior:'ignoreErrors'});await context.close();}
}
(async()=>{
  await fs.mkdir(evidenceDir,{recursive:true});
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const results=[];
    for(const kind of ['ppsub','clash_patch'])for(const width of [1440,320]) {
      const result=await exercise(browser,kind,width);results.push(result);console.log(JSON.stringify(result));
      await fs.writeFile(path.join(evidenceDir,'browser-evidence.json'),JSON.stringify({syntheticOnly:true,results},null,2)+'\n');
    }
  } finally {await browser.close();}
})().then(()=>process.exit(0),error=>{console.error(error);process.exit(1);});
