'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function loadCore() {
  const context = vm.createContext({TextEncoder});
  for (const name of ['theme-data.js', 'theme-core.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', name), 'utf8'), context, {filename: name});
  }
  return {core: context.ThemeCore, data: context.ThemeData};
}
module.exports = loadCore;
