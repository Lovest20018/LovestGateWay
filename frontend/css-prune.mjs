// Remove archived component rules while preserving shared theme CSS.
// Scan CSS tokens so braces/commas inside strings, comments and functions are inert.
function scan(source,start,stop){
  let quote='',comment=false,round=0,square=0,depth=0;
  for(let i=start;i<source.length;i++){
    const c=source[i],next=source[i+1];
    if(comment){if(c==='*'&&next==='/'){comment=false;i++;}continue;}
    if(quote){if(c==='\\')i++;else if(c===quote)quote='';continue;}
    if(c==='/'&&next==='*'){comment=true;i++;continue;}
    if(c==='"'||c==="'"){quote=c;continue;}
    if(c==='\\'){i++;continue;}
    if(c==='(')round++;else if(c===')')round--;
    if(c==='[')square++;else if(c===']')square--;
    if(round||square)continue;
    if(c==='{')depth++;
    if(c==='}')depth--;
    if(stop(c,depth))return i;
  }
  if(quote||comment||round||square||depth)throw Error('Unbalanced theme CSS');
  return source.length;
}
function selectorParts(value){
  const parts=[];let start=0;
  while(start<value.length){const end=scan(value,start,c=>c===',');parts.push(value.slice(start,end));start=end+1;}
  return parts;
}
function scoped(selector){
  const tokens=selector.replace(/\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g,'');
  return /\.svelte-[\w-]+/.test(tokens);
}
export function pruneScopedStyles(source){
  let output='',start=0;
  while(start<source.length){
    const open=scan(source,start,(c,depth)=>c===';'||(c==='{'&&depth===1));
    if(open===source.length){output+=source.slice(start);break;}
    if(source[open]===';'){output+=source.slice(start,open+1);start=open+1;continue;}
    const end=scan(source,open,(c,depth)=>c==='}'&&depth===0);
    if(end===source.length)throw Error('Unclosed theme rule');
    const prelude=source.slice(start,open),body=source.slice(open+1,end),clean=prelude.replace(/\/\*[\s\S]*?\*\//g,'').trim();
    if(/^@(media|supports|layer|container|document|starting-style)\b/i.test(clean)){
      const children=pruneScopedStyles(body);
      if(children.trim())output+=prelude+'{'+children+'}';
    }else if(clean.startsWith('@')){
      if(!/^@(?:-\w+-)?keyframes\s+svelte-/i.test(clean))output+=prelude+'{'+body+'}';
    }else{
      const selectors=selectorParts(prelude).filter(selector=>!scoped(selector));
      if(selectors.length)output+=selectors.join(',')+'{'+body+'}';
    }
    start=end+1;
  }
  return output;
}
