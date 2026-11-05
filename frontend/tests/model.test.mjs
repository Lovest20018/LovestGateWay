import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {sha256} from '../src/auth.js';
import {documentModel,exported,move,setGroupMode} from '../src/model.js';
test('HTTP LAN password hash agrees with server SHA256 UTF8',()=>{for(const value of ['', 'abc','网关管理密码','a'.repeat(1000)])assert.equal(sha256(value),createHash('sha256').update(value).digest('hex'));});
test('editing and moving preserve untouched identities, optional secrets, and unknown nested fields',()=>{const value={version:'custom',custom:{a:[1,2]},subs:[{_clash_id:'s:0',name:'test',url:'https://example.invalid',custom:{headers:['a']}}],'node-groups':[{_clash_id:'g:0',name:'a',hidden:123,monitor_url:'https://example.invalid'}],rules:[{_clash_id:'p:rules',fixrule:['MATCH,a']},{_clash_id:'rules',fixrule:['DOMAIN,a,a']}]};const doc=documentModel(value);doc.subs[0].name='renamed';move(doc.rules,0,1);setGroupMode(doc['node-groups'][0],'smart');const result=exported(doc);assert.deepEqual(result.subs[0].custom,value.subs[0].custom);assert.equal(result.subs[0]._clash_id,'s:0');assert.equal(result['node-groups'][0].hidden,123);assert.equal(result['node-groups'][0].monitor_url,'https://example.invalid');assert.equal(result.rules[1]._clash_id,'p:rules');assert.deepEqual(result.custom,value.custom);assert.equal(value.subs[0].name,'test');});
