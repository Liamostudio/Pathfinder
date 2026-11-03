import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const {validateReplay}=createRequire(import.meta.url)('../../replay/replay.js');
const demo=JSON.parse(fs.readFileSync(new URL('../../replay/demo.json',import.meta.url)));
assert.equal(validateReplay(demo),demo);
for(const bad of [null,{}, {...demo,frames:[]},{...demo,frames:[{t:0,scene:{}}]},{...demo,events:[{t:0,source:'x'}]}])assert.throws(()=>validateReplay(bad));
console.log('Recorded fixture and corrupt replay validation passed.');
