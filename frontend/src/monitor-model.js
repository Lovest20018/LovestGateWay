export const record = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
export const text = value => typeof value === 'string' ? value : '';
export const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0;
export const strings = value => Array.isArray(value) ? value.filter(item => typeof item === 'string') : [];

export function bytes(value) {
  if (value == null || !Number.isFinite(value)) return '—';
  let size = Math.max(0, value), unit = 0;
  while (size >= 1024 && unit < 4) { size /= 1024; unit++; }
  return size.toFixed(unit ? size >= 100 ? 0 : 1 : 0) + ' ' + ['B','KB','MB','GB','TB'][unit];
}
export function date(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString('zh-CN') : '—';
}
export function duration(value) {
  if (value == null || !Number.isFinite(value)) return '—';
  const seconds = Math.max(0, Math.floor(value / 1000));
  return seconds >= 3600 ? Math.floor(seconds / 3600) + 'h ' + Math.floor(seconds % 3600 / 60) + 'm' : seconds >= 60 ? Math.floor(seconds / 60) + 'm ' + seconds % 60 + 's' : seconds + 's';
}
export function sortRows(rows, get, direction = -1) {
  return [...rows].sort((a,b) => {
    const left = get(a), right = get(b);
    return (typeof left === 'number' && typeof right === 'number' ? left-right : String(left ?? '').localeCompare(String(right ?? ''), 'zh-CN', {numeric:true})) * direction;
  });
}
export function pageRows(rows, page = 1, size = 100) {
  const limit = [10,20,50,100].includes(Number(size)) ? Number(size) : 100;
  const pages = Math.max(1, Math.ceil(rows.length / limit));
  const current = Math.max(1, Math.min(Number.isInteger(page) ? page : 1, pages));
  return {rows:rows.slice((current-1)*limit, current*limit), page:current, pages, total:rows.length};
}
export function csv(rows, columns) {
  const escape = value => {
    let string = String(value ?? '');
    // API names are data when a CSV is opened in a spreadsheet.
    if (/^[=+@\-\t\r]/.test(string)) string = "'" + string;
    return /[",\r\n]/.test(string) ? '"' + string.replaceAll('"','""') + '"' : string;
  };
  return '\ufeff' + [columns.map(column=>escape(column.label)).join(','), ...rows.map(row=>columns.map(column=>escape(column.value(row))).join(','))].join('\r\n');
}
export function rateWindow(limit = 31) {
  let previous = null;
  const history = {down:[], up:[], total:[]};
  return {
    history,
    reset() { previous = null; for (const values of Object.values(history)) values.length = 0; },
    sample(down, up, at = Date.now(), identity = '') {
      let result = {down:null, up:null, total:null};
      if (previous && previous.identity === identity && at > previous.at && down >= previous.down && up >= previous.up) {
        const seconds = (at-previous.at)/1000;
        result = {down:(down-previous.down)/seconds, up:(up-previous.up)/seconds}; result.total = result.down+result.up;
      } else for (const values of Object.values(history)) values.length = 0;
      previous = {down, up, at, identity};
      for (const key of ['down','up','total']) { history[key].push(result[key]); if (history[key].length>limit) history[key].shift(); }
      return result;
    }
  };
}

// Incremental SSE framing: CRLF and UTF-8 boundaries may split across network chunks.
export function eventDecoder(onData) {
  let pending = '', data = [], size = 0;
  const line = value => {
    if (!value) { if (data.length) onData(data.join('\n')); data = []; size = 0; return; }
    if (value.startsWith('data:')) { const part = value.slice(5).replace(/^ /,''); size += part.length; if (size > 16*1024*1024) throw Error('实时事件过大，请重新连接。'); data.push(part); }
  };
  return {
    push(chunk) {
      pending += chunk;
      let position;
      while ((position = pending.search(/[\r\n]/)) >= 0) {
        if (pending[position] === '\r' && position === pending.length-1) break;
        const width = pending[position] === '\r' && pending[position+1] === '\n' ? 2 : 1;
        line(pending.slice(0,position)); pending = pending.slice(position+width);
      }
      if (pending.length > 16*1024*1024) throw Error('实时事件过大，请重新连接。');
    }
  };
}
