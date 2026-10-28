import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

function element(dataset = {}) {
  const listeners = new Map();
  return {
    dataset,
    checked: false,
    textContent: '',
    addEventListener(type, listener) { listeners.set(type, listener); },
    fire(type, event = {}) { listeners.get(type)?.(event); },
  };
}

const availability = [
  element({originSource: 'default'}),
  element({originSource: 'imported'}),
];
const selectImported = element({originSelection: 'imported'});
const group = {
  querySelectorAll(selector) {
    return selector === '[data-origin-selection]' ? [selectImported] : availability;
  },
};

const race = element({removeName: 'Marshfolk', removeKind: 'Race'});
const characterClass = element({removeName: 'Trail Warden', removeKind: 'Class'});
const summary = element();
const confirmation = element();
const form = {
  querySelectorAll() { return [race, characterClass]; },
  querySelector(selector) {
    return selector === '.origin-removal-summary' ? summary : confirmation;
  },
  addEventListener(type, listener) { this.submit = listener; },
};
const document = {
  querySelectorAll(selector) { return selector === '[data-origin-group]' ? [group] : []; },
  querySelector() { return null; },
  getElementById(id) { return id === 'mud-origin-removal' ? form : null; },
};

vm.runInNewContext(
  readFileSync(new URL('../stationnet/static/settings-mud.js', import.meta.url), 'utf8'),
  {document},
);

selectImported.fire('click');
assert.equal(availability[0].checked, false);
assert.equal(availability[1].checked, true);

race.checked = true;
race.fire('change');
assert.equal(summary.textContent, 'Remove 1 choice: Race: Marshfolk');
confirmation.checked = true;
characterClass.checked = true;
characterClass.fire('change');
assert.equal(summary.textContent, 'Remove 2 choices: Race: Marshfolk; Class: Trail Warden');
assert.equal(confirmation.checked, false);

let prevented = false;
form.submit({preventDefault() { prevented = true; }});
assert.equal(prevented, false);

race.checked = false;
characterClass.checked = false;
form.submit({preventDefault() { prevented = true; }});
assert.equal(prevented, true);
assert.equal(summary.textContent, 'Select at least one imported race or class.');
