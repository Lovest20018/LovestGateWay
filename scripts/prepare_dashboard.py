#!/usr/bin/env python3
"""Integrate Debian settings routes and save/apply hooks into the shipped dashboard."""
import argparse
import hashlib
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare_dashboard(output):
    source = ROOT / 'FILES/etc/config/clash/clash-dashboard/ui'
    shutil.copytree(source, output, dirs_exist_ok=True)
    bundles = list(source.glob('bundle.*.js'))
    if len(bundles) != 1:
        raise ValueError('Expected one upstream dashboard bundle')
    original = bundles[0]
    bundle = original.read_text(encoding='utf-8')
    for anchor in (',"/cloudflared":[3]', '{id:"/cloudflared",icon:"cloud",label:gi("nav.cloudflared")},'):
        if bundle.count(anchor) != 1:
            raise ValueError('Cloudflared removal hook changed upstream')
        bundle = bundle.replace(anchor, '')
    load_hook = 'async function B(){try{N(await Li(`/data/ppsub.json?t=${Math.floor(Date.now()/1e3)}`))'
    load_patch = ('async function B(){if(window.ppgwPpsub?.isAvailable())'
                  '{await window.ppgwPpsub.loadCurrent(N);return}'
                  'try{N(await Li(`/data/ppsub.json?t=${Math.floor(Date.now()/1e3)}`))')
    mount_hook = ('Cn("click",kt,()=>gn(r).push({priority:gn(r).length+1,remark:"",name:"",type:"manual",'
                  'url:"",rule_set_url:"",content:"",isforced:!1,behavior:"domain",interval:86400,'
                  'format:"auto",use_proxy:!1,proxy_group:""})),zn(e,oe),Vt()}')
    mount_patch = mount_hook.replace('zn(e,oe),Vt()}',
        'Fl(()=>{const destroy=window.ppgwPpsub?.mount({header:ce,exportConfig:z,importConfig:N,providers:()=>gn(n)});'
        'if(!destroy||ce.closest(".ppgw-subscription-editor"))return destroy;'
        'const before=n=>{if(!destroy.allowNavigation(n.to?.url,n.type))n.cancel()};'
        'so.add(before);return()=>{so.delete(before);destroy()}}),zn(e,oe),Vt()}')
    identities = (
        ('({name:e.name||"",url:e.url||"",isforced:', '({_clash_id:e._clash_id||"",update_mode:e.update_mode||"inherit",update_interval:e.update_interval||"1d",name:e.name||"",url:e.url||"",isforced:'),
        ('return{name:e.name||"",keywords:e.keywords', 'return{_clash_id:e._clash_id||"",name:e.name||"",keywords:e.keywords'),
        ('{priority:t+1,remark:e.remark', '{_clash_id:e._clash_id||"",priority:t+1,remark:e.remark'),
        ('const t={name:e.name,url:e.url};', 'const t={_clash_id:e._clash_id||"",name:e.name,url:e.url,...(e.update_mode&&e.update_mode!=="inherit"?{update_mode:e.update_mode,update_interval:e.update_interval||"1d"}:{})};'),
        ('const l={name:e.name,keywords:e.keywords', 'const l={_clash_id:e._clash_id||"",name:e.name,keywords:e.keywords'),
        ('const a={priority:10*(t+1)};', 'const a={_clash_id:e._clash_id||"",priority:10*(t+1)};'),
    )
    rules_hook = 'a.fixrule=(e.content||"").split("\\n").map(e=>function(e)'
    rules_patch = ('a.fixrule=window.ppgwPpsub?.clashEditing()?'
                   '(e.content||"").split("\\n").map(e=>e.trim()).filter(Boolean):'
                   '(e.content||"").split("\\n").map(e=>function(e)')
    choices_hook = 'e.push({name:"DIRECT",type:"group"},{name:"REJECT",type:"group"})),e.filter(e=>e.name&&'
    choices_patch = ('e.push({name:"DIRECT",type:"group"},{name:"REJECT",type:"group"})),e=e.concat('
                     '(window.ppgwPpsub?.nodeChoices?.()||[]).filter(t=>!e.some(a=>a.name===t.name))),e.filter(e=>e.name&&')
    labels = (
        ('loadCurrent:"当前配置"', 'loadCurrent:"重新读取"'),
        ('loadCurrent:"Current Config"', 'loadCurrent:"Reload current"'),
        ('loadSuccessWarn:"只读配置仅供载入编辑器，编辑完成后请不要忘记导出配置！"',
         'loadSuccessWarn:"已载入编辑器，点击保存并应用后生效；导出用于备份。"'),
        ('loadSuccessWarn:"Read-only config loaded. Don\'t forget to Export after editing!"',
         'loadSuccessWarn:"Loaded into the editor. Save and apply to activate changes; export for a backup."'),
    )
    for anchor, replacement in ((load_hook, load_patch), (mount_hook, mount_patch), *identities,
                                (rules_hook, rules_patch), (choices_hook, choices_patch), *labels):
        if bundle.count(anchor) != 1:
            raise ValueError('PPsub integration hook changed upstream; review before packaging')
        bundle = bundle.replace(anchor, replacement)
    # Keep the native pages in the upstream router so login, navigation,
    # mobile controls and theme settings continue to belong to one shell.
    route_hooks = (
        (',"/ppsub":[8]', ',"/ppsub":[13],"/settings":[13],"/subscriptions":[13]'),
        ('nodes:ti,', 'nodes:[...ti,()=>Promise.resolve(ppgwGatewayRoute)],'),
        ('{id:"/ppsub",icon:"package",label:gi("nav.ppsub")}',
         '{id:"/subscriptions",icon:"subscription",label:mi.zh?"订阅与策略":"Subscriptions & policies"},'
         '{id:"/settings",icon:"sliders",label:mi.zh?"网关设置":"Gateway settings"}'),
        ('const n={gauge:', 'const n={subscription:\'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>\',sliders:\'<path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>\',gauge:'),
    )
    for anchor, replacement in route_hooks:
        if bundle.count(anchor) != 1:
            raise ValueError('Gateway route integration hook changed upstream; review before packaging')
        bundle = bundle.replace(anchor, replacement)
    bundle += '''
const ppgwGatewayRoute={component:function(anchor,props){
  Ht(props,true);
  const view=In('<div class="ppgw-workspace page"><div class="ppgw-gateway page"></div><div class="ppgw-subscription-editor" hidden><!></div></div>')();
  const gateway=us(view),editorHost=ps(gateway,1);
  const editorCreated=Va(false);
  Jn(us(editorHost),branch=>{if(gn(editorCreated))branch(anchor=>Rh.component(anchor,{}))});
  Fl(()=>{
    const navigate=(hash,replace=false)=>Uo({type:'goto',url:new URL(hash,location.href),replace_state:replace,keepfocus:true,noscroll:true});
    const destroy=window.ppgwGateway.mount(gateway,{editorHost,createEditor:()=>Ka(editorCreated,true),navigate,
      refreshRuntime:async isCurrent=>{const info=await Li('/sniffbox/info');if(isCurrent())Ei.info=info;}});
    const before=n=>{if(!destroy.allowNavigation(n.to?.url,n.type))n.cancel()};
    const after=()=>destroy.refresh();
    so.add(before);lo.add(after);
    if(location.hash.split('?')[0]==='#/ppsub'||new URLSearchParams(location.hash.split('?')[1]||'').has('tab'))navigate('#/subscriptions',true);
    return()=>{so.delete(before);lo.delete(after);destroy()};
  });
  zn(anchor,view);Vt();
}};
'''
    patched_name = 'bundle.debian.' + hashlib.sha256(bundle.encode()).hexdigest()[:12] + '.js'
    (output / patched_name).write_text(bundle, encoding='utf-8', newline='\n')
    extension = (ROOT / 'integration/ppsub_panel.js').read_bytes()
    extension_name = 'ppsub-panel.' + hashlib.sha256(extension).hexdigest()[:12] + '.js'
    (output / extension_name).write_bytes(extension)
    native_assets = {}
    for filename in ('gateway.js', 'gateway.css'):
        raw = (ROOT / 'integration' / filename).read_bytes()
        path = Path(filename)
        name = path.stem + '.' + hashlib.sha256(raw).hexdigest()[:12] + path.suffix
        (output / name).write_bytes(raw)
        native_assets[filename] = name
    gateway = (ROOT / 'integration/gateway.html').read_text(encoding='utf-8')
    for original_name, hashed_name in native_assets.items(): gateway = gateway.replace(original_name, hashed_name)
    (output / 'gateway.html').write_text(gateway, encoding='utf-8', newline='\n')
    index = (source / 'index.html').read_text(encoding='utf-8').replace(original.name, patched_name)
    index = index.replace('</head>', f'<script src="/ui/{extension_name}"></script>\n</head>')
    index = index.replace('</head>', f'<link rel="stylesheet" href="/ui/{native_assets["gateway.css"]}">\n'
                          f'<script src="/ui/{native_assets["gateway.js"]}"></script>\n</head>')
    view = (ROOT / 'integration/gateway_view.html').read_text(encoding='utf-8')
    index = index.replace('</body>', f'<template id="ppgw-gateway-view">{view}</template>\n</body>')
    (output / 'index.html').write_text(index, encoding='utf-8', newline='\n')
    return patched_name


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare_dashboard(args.output))
