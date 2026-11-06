const number=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;
export function formatBytes(value,rate=false){
  value=number(value);if(value===null)return '—';
  const units=['B','KB','MB','GB','TB','PB'],i=Math.min(units.length-1,Math.floor(Math.log(Math.max(1,value))/Math.log(1024)));
  return (value/1024**i).toFixed(1)+' '+units[i]+(rate?'/s':'');
}
