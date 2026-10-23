import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_canvas_vector_rendering_clipping_validation_and_limits():
    host = Path(__file__).resolve().parents[1] / "stationnet" / "static" / "application_host.js"
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const listeners = Object.create(null), canvases = [];
global.addEventListener = (type, handler) => (listeners[type] ||= []).push(handler);
global.window = global;
global.navigator = {};

class Classes {
  constructor(owner) { this.owner = owner; this.values = new Set(); }
  add(...values) { values.forEach(value => this.values.add(value)); }
  contains(value) { return this.values.has(value); }
}
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.dataset = {}; this.children = [];
    this.classList = new Classes(this); this.attributes = {}; this.events = {};
  }
  set className(value) { this._className = value; String(value).split(/\s+/).filter(Boolean).forEach(item => this.classList.add(item)); }
  get className() { return this._className || ''; }
  append(...nodes) { this.children.push(...nodes); nodes.forEach(node => { if (node) node.parent = this; }); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, handler) { (this.events[type] ||= []).push(handler); }
  querySelector(selector) {
    if (selector === 'canvas') return this.children.find(child => child.tagName === 'CANVAS') || null;
    if (selector === '.app-canvas-controls') return this.children.find(child => child.classList?.contains('app-canvas-controls')) || null;
    return null;
  }
  querySelectorAll(selector) {
    const found = [];
    const visit = node => { if (selector === '[data-app-component]' && node.dataset?.appComponent != null) found.push(node); (node.children || []).forEach(visit); };
    this.children.forEach(visit); return found;
  }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  replaceWith(node) { if (!this.parent) return; const index = this.parent.children.indexOf(this); this.parent.children[index] = node; node.parent = this.parent; }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.width || 1, height: this.height || 1 }; }
  setPointerCapture() {}
}
class Context {
  constructor() { this.ops = []; }
  op(name, ...args) { this.ops.push({ name, args, fillStyle: this.fillStyle, strokeStyle: this.strokeStyle, lineWidth: this.lineWidth }); }
  clearRect(...args) { this.op('clearRect', ...args); } fillRect(...args) { this.op('fillRect', ...args); }
  save() { this.op('save'); } restore() { this.op('restore'); } beginPath() { this.op('beginPath'); }
  rect(...args) { this.op('rect', ...args); } clip() { this.op('clip'); }
  arc(...args) { this.op('arc', ...args); } moveTo(...args) { this.op('moveTo', ...args); }
  lineTo(...args) { this.op('lineTo', ...args); } closePath() { this.op('closePath'); }
  fill() { this.op('fill'); } stroke() { this.op('stroke'); } fillText(...args) { this.op('fillText', ...args); }
}
class Canvas extends Element {
  constructor() { super('canvas'); this.context = new Context(); canvases.push(this); }
  getContext(kind) { assert.equal(kind, '2d'); return this.context; }
}

const frame = new Element('iframe'); frame.contentWindow = {};
const surface = new Element('main'); surface.dataset = {};
const status = new Element('p');
global.document = {
  hidden: false, hasFocus: () => true, body: new Element('body'),
  addEventListener: (type, handler) => (listeners[`document:${type}`] ||= []).push(handler),
  querySelector: selector => selector === '[data-app-runtime]' ? frame : selector === '[data-app-surface]' ? surface : selector === '[data-app-status]' ? status : null,
  createElement: tag => tag === 'canvas' ? new Canvas() : new Element(tag),
  createDocumentFragment: () => new Element('fragment')
};

vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });
const send = data => listeners.message[0]({ source: frame.contentWindow, data: { protocol: 'stationnet-app-v1', ...data } });
const render = commands => { send({ type: 'render', mode: 'replace', components: [{ type: 'canvas', width: 320, height: 180, commands }] }); return canvases.at(-1).context; };
send({ type: 'initialized' });

// Existing raster commands retain their behavior alongside vector commands.
let context = render([
  { type: 'rect', x: 2, y: 3, width: 4, height: 5, color: '#123456' },
  { type: 'circle', x: 8, y: 9, radius: 3, color: '#abc' },
  { type: 'text', x: 10, y: 11, text: 'old API', size: 12, color: '#fff' },
  { type: 'line', x1: -5000, y1: 20, x2: 5000, y2: 20, color: 'rgb(1, 2, 3)', width: 2.5 },
  { type: 'lines', segments: [[0, 0, 10, 10], [10, 10, 20, 0]], color: '#abcdef80', width: 4 },
  { type: 'polygon', points: [[-10, 5], [30, -10], [40, 30]], fill: '#112233', stroke: '#445566', width: 3 }
]);
assert.ok(context.ops.some(op => op.name === 'fillRect' && op.args.join(',') === '2,3,4,5' && op.fillStyle === '#123456'));
assert.ok(context.ops.some(op => op.name === 'arc' && op.args.slice(0, 3).join(',') === '8,9,3'));
assert.ok(context.ops.some(op => op.name === 'fillText' && op.args[0] === 'old API'));
assert.deepEqual(context.ops.find(op => op.name === 'rect' && op.args[2] === 320).args, [0, 0, 320, 180]);
assert.ok(context.ops.some(op => op.name === 'clip'));
assert.ok(context.ops.some(op => op.name === 'moveTo' && op.args[0] === -5000));
assert.ok(context.ops.some(op => op.name === 'lineTo' && op.args[0] === 5000));
const strokes = context.ops.filter(op => op.name === 'stroke');
assert.deepEqual(strokes.map(op => op.lineWidth), [2.5, 4, 3]);
assert.equal(strokes[1].strokeStyle, '#abcdef80');
assert.ok(context.ops.some(op => op.name === 'closePath'));
assert.ok(context.ops.some(op => op.name === 'fill' && op.fillStyle === '#112233'));

// Malformed geometry is skipped, widths are bounded, and invalid CSS colors fall back safely.
context = render([
  { type: 'line', x1: 0, y1: 0, x2: 'not-a-number', y2: 4 },
  { type: 'lines', segments: [[0, 0, null, 1], ['bad', 0, 1, 1]] },
  { type: 'polygon', points: [[0, 0], [1, 1]] },
  { type: 'line', x1: 0, y1: 0, x2: 2, y2: 2, color: 'rgb(999,0,0)', width: 999 }
]);
assert.equal(context.ops.filter(op => op.name === 'stroke').length, 1);
assert.equal(context.ops.find(op => op.name === 'stroke').strokeStyle, '#00ed45');
assert.equal(context.ops.find(op => op.name === 'stroke').lineWidth, 64);
assert.equal(context.ops.filter(op => op.name === 'closePath').length, 0);

// Batches use one stroke and stop at the per-frame segment budget.
context = render([{ type: 'lines', segments: Array.from({ length: 10005 }, () => [0, 0, 1, 1]) }]);
assert.equal(context.ops.filter(op => op.name === 'stroke').length, 1);
assert.equal(context.ops.filter(op => op.name === 'lineTo').length, 10000);

// The command and polygon budgets apply independently.
context = render(Array.from({ length: 605 }, () => ({ type: 'line', x1: 0, y1: 0, x2: 1, y2: 1 })));
assert.equal(context.ops.filter(op => op.name === 'stroke').length, 600);
context = render(Array.from({ length: 300 }, () => ({ type: 'polygon', points: [[0, 0], [1, 0], [0, 1]], color: '#0f0' })));
assert.equal(context.ops.filter(op => op.name === 'closePath').length, 256);

// A single polygon and a whole frame are both vertex-bounded.
context = render([{ type: 'polygon', points: Array.from({ length: 5000 }, () => [0, 0]), color: '#0f0' }]);
assert.equal(context.ops.filter(op => op.name === 'lineTo').length, 4095);
context = render(Array.from({ length: 5 }, () => ({ type: 'polygon', points: Array.from({ length: 4096 }, () => [0, 0]), color: '#0f0' })));
assert.equal(context.ops.filter(op => op.name === 'lineTo').length, 20000 - 5);
"""
    subprocess.run(["node", "-e", script, str(host)], check=True, capture_output=True, text=True)
