"""Run the actual github-script publisher against a fake GitHub API."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import yaml


@unittest.skipUnless(shutil.which('node'), 'Node is required to exercise the workflow publisher')
class ReleaseWorkflowTests(unittest.TestCase):
    def test_drafts_tags_upload_failure_and_success(self):
        path = Path(__file__).resolve().parents[1] / '.github/workflows/release.yml'
        workflow = yaml.load(path.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        script = workflow['jobs']['publish']['steps'][-1]['with']['script']
        harness = r'''
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
const publish=new AsyncFunction('require','github','context','core','process',SCRIPT);
async function run({existing=[],tagSha='a'.repeat(40),badDigest=false,publishFlag='true'}={}){
  const calls=[];
  const manifest={assets:{'archive.tar.gz':{}},upstream:{reviewed_head:'b'.repeat(40)}};
  const readFileSync=(path,encoding)=>path.endsWith('release-manifest.json')?JSON.stringify(manifest):encoding?'Release notes':Buffer.from('fixture');
  const github={paginate:async()=>existing,rest:{repos:{
    listReleases:()=>{},
    createRelease:async input=>{assert.equal(input.draft,true);calls.push('create-draft');return {data:{id:1,html_url:'https://example.invalid/release'}};},
    uploadReleaseAsset:async input=>{calls.push('upload');return {data:{size:input.data.length,digest:badDigest?'wrong':'sha256:'+crypto.createHash('sha256').update(input.data).digest('hex')}};},
    updateRelease:async input=>{assert.equal(input.draft,false);calls.push('publish');}
  },git:{getRef:async()=>({data:{object:{type:'commit',sha:tagSha}}}),createRef:async()=>{calls.push('tag');}}}};
  const summary={addLink(){return this},addRaw(){return this},write(){}};
  let error;
  try{await publish(name=>name==='fs'?{readFileSync}:require(name),github,{repo:{owner:'fixture',repo:'fixture'}},{summary},{env:{VERSION:'1.2.3',COMMIT:'a'.repeat(40),PUBLISH:publishFlag}});}
  catch(value){error=value;}
  return {calls,error};
}
(async()=>{
  for(const draft of [true,false]){
    const result=await run({existing:[{tag_name:'v1.2.3',draft}]});
    assert.match(result.error.message,/already exists/);assert.deepEqual(result.calls,[]);
  }
  const wrong=await run({tagSha:'c'.repeat(40)});assert.match(wrong.error.message,/points elsewhere/);assert.deepEqual(wrong.calls,[]);
  const failed=await run({badDigest:true});assert.match(failed.error.message,/mismatch/);assert.deepEqual(failed.calls,['create-draft','upload']);
  const draft=await run({publishFlag:'false'});assert.equal(draft.error,undefined);assert.deepEqual(draft.calls,['create-draft','upload','upload']);
  const success=await run();assert.equal(success.error,undefined);assert.deepEqual(success.calls,['create-draft','upload','upload','publish']);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(['node', '-'], input='const SCRIPT=' + json.dumps(script) + ';\n' + harness,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()
