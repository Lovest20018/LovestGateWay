import test from 'node:test';
import assert from 'node:assert/strict';
import {sha256,restoreSession,loginSession,logoutSession,sessionContext,setSessionContext} from '../src/auth.js';

test('browser sessions clear every legacy credential and retain preferences across refresh and logout',async()=>{
  const original={fetch:globalThis.fetch,localStorage:globalThis.localStorage};
  let saved=JSON.stringify({secret:'old-proof',theme:'dark',clashAPIConfigs:[{name:'one',secret:'proof-one'},{name:'two',secret:'proof-two'}]}),authenticated=false,logoutFails=true;
  const requests=[];
  globalThis.localStorage={getItem:()=>saved,setItem:(key,value)=>{assert.equal(key,'PaoPaoGateWay');saved=value;}};
  globalThis.fetch=async(url,options)=>{
    requests.push({url,options});assert.equal(options.credentials,'same-origin');assert.equal(options.headers.Authorization,undefined);
    if(url.endsWith('/login')){assert.deepEqual(JSON.parse(options.body),{proof:sha256('synthetic-password')});authenticated=true;return Response.json({authenticated:true});}
    if(url.endsWith('/logout')){if(logoutFails)return Response.json({error:'unavailable'},{status:503});authenticated=false;return Response.json({authenticated:false});}
    return Response.json(authenticated?{authenticated:true,context:'public-cache-context',expires_in:43200}:{authenticated:false});
  };
  try{
    assert.equal(await restoreSession(),false);
    assert.deepEqual(JSON.parse(saved),{secret:'',theme:'dark',clashAPIConfigs:[{name:'one',secret:''},{name:'two',secret:''}]});
    await loginSession('synthetic-password');assert.equal(sessionContext(),'public-cache-context');
    setSessionContext('');assert.equal(await restoreSession(),true,'refresh restores the server session without a stored proof');
    await assert.rejects(logoutSession());assert.equal(sessionContext(),'public-cache-context','failed logout must remain visible and retryable');
    logoutFails=false;await logoutSession();assert.equal(sessionContext(),'');assert.equal(await restoreSession(),false);
    assert.equal(requests.filter(row=>row.url.endsWith('/login')).length,1,'refresh and logout never replay login');
    assert.equal(saved.includes(sha256('synthetic-password')),false);
  }finally{setSessionContext('');Object.assign(globalThis,original);}
});
