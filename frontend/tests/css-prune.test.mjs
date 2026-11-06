import {test} from 'node:test';
import assert from 'node:assert/strict';
import {pruneScopedStyles} from '../css-prune.mjs';

test('prunes scoped selectors inside nested rules and retains shared selectors',()=>{
  const result=pruneScopedStyles(':root{--x:1}.gone.svelte-abc,.shared{color:red}@media(max-width:600px){.gone.svelte-abc{color:red}@supports(display:grid){.shared{display:grid}}}');
  assert.equal(result,':root{--x:1}.shared{color:red}@media(max-width:600px){@supports(display:grid){.shared{display:grid}}}');
});
test('preserves strings, escaped delimiters, data URLs, fonts and keyframes',()=>{
  const css=String.raw`/* } { */@font-face{src:url("data:x,{a,b}")}a[data-x=".svelte-abc"]:is(.a,.b){content:"} \" {"}@keyframes pulse{from{opacity:0}to{opacity:1}}.escaped\,name{color:red}`;
  assert.equal(pruneScopedStyles(css),css);
  assert.equal(pruneScopedStyles('@keyframes svelte-a{from{opacity:0}}'), '');
});
test('rejects malformed CSS rather than emitting a partial theme',()=>{
  assert.throws(()=>pruneScopedStyles('a{content:"unterminated}'));
  assert.throws(()=>pruneScopedStyles('a{color:red'));
});
