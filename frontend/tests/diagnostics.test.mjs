import test from 'node:test';
import assert from 'node:assert/strict';
import {publicReport,pendingRecoveryJob,operationError} from '../src/diagnostics.js';

test('historical busy failures use past tense while current and recovery errors remain current',()=>{
 assert.equal(operationError({status:'failed',error:'MANAGEMENT_BUSY'}),'该次操作因其他管理任务占用未执行。');
 for(const row of [{status:'running'},{status:'queued'},{status:'applying'},{status:'failed',recovery_required:true}])assert.match(operationError({...row,error:'MANAGEMENT_BUSY'}),/正在执行/);
});

test('download projection retains only diagnostic fields and fixed public values',()=>{
  const value=publicReport({schema:999,generated_at:100,secret:'private-password',services:{paopaogw:{active:true,enabled:false,token:'private-token'},'private-unit':{active:true,enabled:true}},runtime:{state:'running',config:'private-config'},recovery:{required:true,phase:'prepared',journal:'private-journal'},source:{kind:'native_ppsub',policy_state:'active',overridden_sections:2,url:'https://private-url'},recent:[{source:'settings',kind:'restore',status:'failed',phase:'complete',updated_at:99,error:'APPLY_FAILED_ROLLED_BACK',message:'private-message'},{source:'private-source',kind:'private-kind',status:'private-status',phase:'private-phase',updated_at:Infinity,error:'private-error'}],causes:['RECOVERY_REQUIRED','private-cause']});
  assert.equal(value.schema,1);assert.deepEqual(value.services,{paopaogw:{active:true,enabled:false}});assert.equal(value.recovery.phase,'prepared');assert.equal(value.recent[0].error,'APPLY_FAILED_ROLLED_BACK');assert.equal(value.recent[1].error,'OPERATION_FAILED');assert.equal(value.recent[1].updated_at,null);assert.doesNotMatch(JSON.stringify(value),/private/);
});
test('restored task metadata contains only a valid ID and supported action',()=>{
  const old=globalThis.sessionStorage;
  try{globalThis.sessionStorage={getItem:()=>JSON.stringify({id:'a'.repeat(32),kind:'restore',preview_token:'not-persisted',password:'not-persisted'})};assert.deepEqual(pendingRecoveryJob(),{id:'a'.repeat(32),kind:'restore'});for(const value of [{id:'../path',kind:'restore'},{id:'a'.repeat(32),kind:'shell'},null]){globalThis.sessionStorage.getItem=()=>JSON.stringify(value);assert.equal(pendingRecoveryJob(),null);}}
  finally{if(old===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=old;}
});
