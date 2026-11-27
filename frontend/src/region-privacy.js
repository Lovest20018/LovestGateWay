const ipv4Pattern=/^(?:\d{1,3}\.){3}\d{1,3}$/;

function ipv6(value) {
  if(!value.includes(':'))return '';
  const address=value.replace(/^\[|\]$/g,'').split('%',1)[0];
  try{return new URL('http://['+address+']/').hostname.slice(1,-1);}
  catch{return '';}
}

function ipv4(value) {
  return ipv4Pattern.test(value)&&value.split('.').every(part=>Number(part)<=255);
}

export const isRegionIp=value=>typeof value==='string'&&(ipv4(value)||Boolean(ipv6(value)));

export function maskRegionIps(value='') {
  return value.replace(/\[[\da-f:.]+(?:%[\w.-]+)?\]|(?<![\da-z])(?:[\da-f]*:[\da-f:.]+(?:%[\w.-]+)?|(?:\d{1,3}\.){3}\d{1,3})(?![\da-z])/gi,match=>{
    const address=match.replace(/\.+$/,'');
    const v6=ipv6(address);
    let masked;
    if(v6){
      const prefix=v6.split('::',1)[0].split(':').slice(0,2).filter(Boolean).join(':');
      masked=(prefix?prefix+':':'')+'••••';
      if(address.startsWith('['))masked='['+masked+']';
    }else if(ipv4(address))masked=address.split('.').slice(0,2).join('.')+'.•••.•••';
    else return match;
    return masked+match.slice(address.length);
  });
}
