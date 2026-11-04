import assert from 'node:assert/strict';
import {confirmation} from '../../lookandpick/launcher/web/confirmation.js';
const doc={activeElement:null},node=()=>({inert:false,focus(){doc.activeElement=this;}}),previous=node(),cancel=node(),accept=node(),background=[node(),{inert:true}];doc.activeElement=previous;
const root={ownerDocument:doc,hidden:true},message={},ask=confirmation(root,message,cancel,accept,background);
let answer=ask('Clear manual restrictions?');assert.equal(root.hidden,false);assert.equal(message.textContent,'Clear manual restrictions?');assert.equal(doc.activeElement,cancel);assert.ok(background.every(n=>n.inert));
cancel.onclick();assert.equal(await answer,false);assert.equal(root.hidden,true);assert.equal(doc.activeElement,previous);assert.equal(background[0].inert,false);assert.equal(background[1].inert,true);
answer=ask('Confirm');accept.onclick();assert.equal(await answer,true);
// A prior affirmative response must never turn Escape/outside click into consent.
answer=ask('Again');assert.equal(await ask('Second concurrent request'),false);
let prevented=0;root.onkeydown({key:'Tab',preventDefault(){prevented++;}});assert.equal(doc.activeElement,accept);
root.onkeydown({key:'Escape',preventDefault(){prevented++;}});assert.equal(await answer,false);assert.equal(prevented,2);
answer=ask('Outside click');root.onclick({target:root});assert.equal(await answer,false);
console.log('Launcher confirmation requires an affirmative click; cancellation, focus and keyboard behaviour passed.');
