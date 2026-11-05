import {readFile,writeFile,mkdir,copyFile,unlink} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const here=dirname(fileURLToPath(import.meta.url)), root=resolve(here,'..');
const textFile=async path=>(await readFile(path,'utf8')).replaceAll('\r\n','\n');
export async function build(output=join(here,'dist')) {
  await mkdir(output,{recursive:true});
  // Delete only prior manifest-owned basenames; never recursively remove a caller's path.
  try {const previous=JSON.parse(await readFile(join(output,'manifest.json'),'utf8'));for(const name of Object.values(previous.assets||{}))if(typeof name==='string'&&/^[a-z_-]+\.[a-f0-9]{12}\.(js|css)$/.test(name))await unlink(join(output,name)).catch(()=>{});}catch{}
  const original=join(root,'FILES/etc/config/clash/clash-dashboard/ui');
  const html=await textFile(join(original,'index.html'));
  const assets={};
  async function emit(label,raw) {
    const ext=label.slice(label.lastIndexOf('.')), name=label.slice(0,-ext.length)+'.'+createHash('sha256').update(raw).digest('hex').slice(0,12)+ext;
    await writeFile(join(output,name),raw); assets[label]=name; return name;
  }
  // Keep the upstream runtime bundle intact and mount it in the shared document.
  const bundle=html.match(/import\("\/ui\/(bundle\.[^"]+\.js)"\)/)?.[1];
  if(!bundle) throw Error('Upstream public entry point missing');
  for(const name of [bundle,'TwemojiCountryFlags.woff2','lovestgateway.svg','paopaogateway.svg']) await copyFile(join(original,name),join(output,name));
  const styles=[...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(m=>m[1]).join('\n');
  await emit('theme.css',styles);
  for(const name of ['gateway.js','gateway.css','ppsub_panel.js']) await emit(name,await textFile(join(root,'integration',name)));
  for(const name of ['model.js','auth.js','icons.js','proxy-model.js'])await emit(name,await textFile(join(here,'src',name)));
  await emit('legacy-bridge.js',(await textFile(join(here,'src/legacy-bridge.js'))).replaceAll('@@runtime-bundle@@',bundle));
  for(const name of ['editor.js','diagnostics.js','overview.js','proxies.js','shell.js']) {
    let source=await textFile(join(here,'src',name));
    for(const [label,hashed] of Object.entries(assets)) source=source.replaceAll('./'+label,'./'+hashed);
    await emit(name,source);
  }
  await emit('shell.css',await textFile(join(here,'src/shell.css')));
  await emit('editor.css',await textFile(join(here,'src/editor.css')));
  await emit('diagnostics.css',await textFile(join(here,'src/diagnostics.css')));
  await emit('overview.css',await textFile(join(here,'src/overview.css')));
  await emit('proxies.css',await textFile(join(here,'src/proxies.css')));
  await writeFile(join(output,'legacy.html'),'<!doctype html><meta charset="utf-8"><script>location.replace("/ui/"+location.hash)</script>');
  let index=await textFile(join(here,'src/index.html'));
  for(const [label,hashed] of Object.entries(assets)) index=index.replaceAll('@@'+label+'@@',hashed);
  index=index.replace('@@gateway_view@@',await textFile(join(root,'integration/gateway_view.html')));
  await writeFile(join(output,'index.html'),index);
  await copyFile(join(root,'integration/gateway.html'),join(output,'gateway.html'));
  await writeFile(join(output,'manifest.json'),JSON.stringify({schema:1,entry:'index.html',assets},null,2)+'\n');
  return assets['shell.js'];
}
if(process.argv[1]===fileURLToPath(import.meta.url)) console.log(await build(process.argv.includes('--output')?resolve(process.argv[process.argv.indexOf('--output')+1]):undefined));
