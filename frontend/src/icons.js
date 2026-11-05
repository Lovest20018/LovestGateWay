// Established dashboard icon paths, shared by the maintained navigation.
const paths={
  "monitor": "<rect x=\"2\" y=\"3\" width=\"20\" height=\"14\" rx=\"2\"/><path d=\"M8 21h8M12 17v4\"/>",
  "sun": "<circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4\"/>",
  "moon": "<path d=\"M20.9 13.1A8.5 8.5 0 1 1 10.9 3.1a7 7 0 0 0 10 10z\"/>",
  "gauge": "<path d=\"M12 14.5 15.6 11\"/><path d=\"M20.3 18a9 9 0 1 0-16.6 0\"/>",
  "globe": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M3 12h18M12 3a15.3 15.3 0 0 1 0 18M12 3a15.3 15.3 0 0 0 0 18\"/>",
  "filter": "<path d=\"M4 6h16M7 12h10M10 18h4\"/>",
  "swap": "<path d=\"M8 3 4 7l4 4\"/><path d=\"M4 7h16\"/><path d=\"m16 21 4-4-4-4\"/><path d=\"M20 17H4\"/>",
  "history": "<path d=\"M3 12a9 9 0 1 0 2.6-6.3L3 8\"/><path d=\"M3 3v5h5\"/><path d=\"M12 7v5l3.2 2\"/>",
  "pie": "<path d=\"M21.2 15.9A10 10 0 1 1 8 2.8\"/><path d=\"M22 12A10 10 0 0 0 12 2v10z\"/>",
  "terminal": "<path d=\"m4 17 6-6-6-6\"/><path d=\"M12 19h8\"/>",
  "radio": "<circle cx=\"12\" cy=\"12\" r=\"2\"/><path d=\"M7.8 16.2a6 6 0 0 1 0-8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 19.1a10 10 0 0 1 0-14.2M19.1 4.9a10 10 0 0 1 0 14.2\"/>",
  "package": "<path d=\"M21 8.5v7a2 2 0 0 1-1 1.73l-6.9 4a2 2 0 0 1-2 0l-7-4A2 2 0 0 1 3 15.5v-7a2 2 0 0 1 1-1.73l7-4a2 2 0 0 1 2 0l7 4a2 2 0 0 1 1 1.73z\"/><path d=\"M3.3 7 12 12l8.7-5M12 22V12\"/>",
  "settings": "<circle cx=\"12\" cy=\"12\" r=\"3\"/><path d=\"M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.9 2.9l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.9-2.9l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.9-2.9l.1.1a1.7 1.7 0 0 0 1.9.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.2a1.7 1.7 0 0 0 1 1.5h0a1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.9 2.9l-.1.1a1.7 1.7 0 0 0-.3 1.9v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.2a1.7 1.7 0 0 0-1.5 1z\"/>",
  "activity": "<path d=\"M3 12h4l3 8 4-16 3 8h4\"/>",
  "palette": "<circle cx=\"13.5\" cy=\"6.5\" r=\".5\"/><circle cx=\"17.5\" cy=\"10.5\" r=\".5\"/><circle cx=\"8.5\" cy=\"7.5\" r=\".5\"/><circle cx=\"6.5\" cy=\"12.5\" r=\".5\"/><path d=\"M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.9 0 1.5-.6 1.5-1.5 0-.4-.1-.7-.3-1-.2-.3-.3-.6-.3-.9 0-.8.7-1.5 1.5-1.5H17c3.3 0 6-2.7 6-6 0-4.4-4.9-9-11-9z\"/>",
  "logout": "<path d=\"M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4\"/><path d=\"m16 17 5-5-5-5\"/><path d=\"M21 12H9\"/>",
  "menu": "<path d=\"M4 6h16M4 12h16M4 18h16\"/>"
};
export function icon(name){
  const node=document.createElementNS('http://www.w3.org/2000/svg','svg');
  for(const [key,value]of Object.entries({viewBox:'0 0 24 24',width:'18',height:'18',fill:'none',stroke:'currentColor','stroke-width':'1.9','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false',class:'shell-icon'}))node.setAttribute(key,value);
  node.innerHTML=paths[name]||paths.settings;
  return node;
}
