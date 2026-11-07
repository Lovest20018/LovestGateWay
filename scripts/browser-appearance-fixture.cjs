/* Drive the visible appearance cycle; never write the preference behind the UI. */
const assert=require('node:assert/strict');
async function setAppearance(page,value){
  assert.ok(['system','light','dark'].includes(value));
  const choice=page.locator('#shell-theme');
  for(let turn=0;turn<3;turn++){
    if(await choice.getAttribute('data-theme-choice')===value)return;
    await choice.click();
  }
  assert.equal(await choice.getAttribute('data-theme-choice'),value,'appearance cycle must reach the requested mode');
}
module.exports={setAppearance};
