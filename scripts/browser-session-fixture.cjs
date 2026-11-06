/* Synthetic session adapter shared by UI fixtures. Real enforcement is tested in Rust. */
const {randomBytes,createHash}=require('node:crypto');
function sessionFixture({proof=()=> 'fixture-token',initial=true,expired=()=>false}={}){
  let raw=initial?randomBytes(32).toString('hex'):'',signed=proof(),bootstrap=initial;
  const headers=request=>typeof request.headers==='function'?request.headers():request.headers;
  const authorized=request=>!!raw&&!expired()&&signed===proof()&&headers(request).cookie?.split(';').some(row=>row.trim()==='ppgw_session='+raw);
  function handle(request,body){
    const url=new URL(typeof request.url==='function'?request.url():'http://fixture'+request.url),path=url.pathname;
    if(!path.startsWith('/sniffbox/auth/'))return null;
    let setCookie;
    const reply=(data,status=200)=>({status,data,headers:setCookie?{'Set-Cookie':setCookie}:{}});
    if(path.endsWith('/login')){
      const value=body??request.postDataJSON();
      if(value.proof!==proof())return reply({error:'LOGIN_REQUIRED'},401);
      raw=randomBytes(32).toString('hex');signed=proof();setCookie='ppgw_session='+raw+'; HttpOnly; SameSite=Strict; Path=/';
      return reply({authenticated:true});
    }
    if(path.endsWith('/logout')){raw='';bootstrap=false;setCookie='ppgw_session=; Max-Age=0; Path=/';return reply({authenticated:false});}
    if(path.endsWith('/session')){
      if(bootstrap){bootstrap=false;setCookie='ppgw_session='+raw+'; HttpOnly; SameSite=Strict; Path=/';return reply({authenticated:true,context:createHash('sha256').update(raw).digest('hex').slice(0,24),expires_in:43200});}
      return reply(authorized(request)?{authenticated:true,context:createHash('sha256').update(raw).digest('hex').slice(0,24),expires_in:43200}:{authenticated:false});
    }
    return reply({error:'NOT_FOUND'},404);
  }
  return {authorized,handle,reset(){raw=randomBytes(32).toString('hex');signed=proof();bootstrap=true;},async install(page){
    await page.route('**/sniffbox/auth/*',async route=>{const result=handle(route.request());await route.fulfill({status:result.status,contentType:'application/json',headers:result.headers,body:JSON.stringify(result.data)});});
  }};
}
module.exports={sessionFixture};
