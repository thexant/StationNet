'use strict';
const {core, data} = require('./load-core.cjs')();
const cases = [];
for (const [id, entry] of Object.entries(data.presets)) {
  for (const [includeSite, includeMasthead] of [[true, true], [true, false], [false, true]]) {
    const state = {...core.initial(), ...core.preset(id), id: 'test-' + id,
      name: entry.name, author: 'Theme Creator Test', includeSite, includeMasthead};
    cases.push({state, exported: core.exportTheme(state), variables: core.variables(state)});
  }
}
for (let channel = 0; channel < 256; channel++) {
  const color = '#' + [channel, 255 - channel, (channel * 37) % 256].map(c => c.toString(16).padStart(2, '0')).join('');
  const state = {...core.initial(), id: 'channel-' + channel, name: '🎨'.repeat(80), author: '\u0085 Tester \u001c'};
  state.site.green = color;
  state.site.cyan = color;
  if (channel % 2) { state.site.field = '#fefefe'; state.site.button = '#aabbcc'; }
  cases.push({state, exported: core.exportTheme(state), variables: core.variables(state)});
}
process.stdout.write(JSON.stringify({data, cases}));
