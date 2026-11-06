export const storageKey='lovestgateway-recovery-job';
export function pendingRecoveryJob(){try{const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');if(saved&&/^[a-f0-9]{32}$/.test(saved.id)&&['backup','restore','recover'].includes(saved.kind))return{id:saved.id,kind:saved.kind};}catch{}return null;}
