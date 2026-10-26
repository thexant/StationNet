/* Calculator V2 — graphical calculator for the StationNet application canvas.
   StationNet Community · keyboard, pointer/touch, private user storage. */
StationNet.start(async (ui) => {
  const { calculate, formatNumber } = await ui.modules.load('engine.js');
  const STORE = 'calculator.v2.state';  // Same user data as Calculator V2 1.0.
  const W = 480, H = 800;
  const COLORS = {
    background: '#08120f', border: '#294d3e', header: '#0b1e18', panel: '#10271e',
    display: '#071c16', text: '#e2f7eb', muted: '#83a99a', bright: '#5cffae',
    key: '#20342d', edge: '#344f42', operation: '#28483d',
    science: '#213c47', sciEdge: '#3a626d', memory: '#243f36',
    red: '#503034', redEdge: '#87525a', equal: '#40d68e',
    equalText: '#072419', active: '#47856d', soft: '#172b23'
  };

  let saved;
  try { saved = await ui.storage.get(STORE); } catch (_) { saved = null; }
  let expression = '';
  let answer = Number.isFinite(saved?.answer) ? saved.answer : 0;
  let memory = Number.isFinite(saved?.memory) ? saved.memory : 0;
  let hasMemory = !!saved?.hasMemory;
  let angle = saved?.angle === 'RAD' ? 'RAD' : 'DEG';
  let history = Array.isArray(saved?.history)
    ? saved.history.filter(h => typeof h?.expr === 'string' && Number.isFinite(h?.value)).slice(0, 80)
    : [];
  let mode = ['standard', 'scientific'].includes(saved?.mode) ? saved.mode : 'standard';
  let lastKeypadMode = mode;
  let historyPage = 0;
  let justEvaluated = false;
  let lastCalculation = '';
  let notice = '';
  let saving = Promise.resolve();
  let hitboxes = [];

  function persist() {
    const snapshot = { answer, memory, hasMemory, angle, history: history.slice(0, 80), mode: lastKeypadMode };
    saving = saving.then(() => ui.storage.set(STORE, snapshot)).catch(() => {
      notice = 'Storage unavailable';
      draw();
    });
  }
  const canEndValue = s => /(?:\d|π|e|\)|!|%)$/.test(s) || s.endsWith('Ans');
  // Permit common calculator shorthand: sin(30= implicitly closes its parentheses.
  function evaluate(value) {
    const opens = (value.match(/\(/g) || []).length;
    const closes = (value.match(/\)/g) || []).length;
    return calculate(value + ')'.repeat(Math.max(0, opens - closes)), angle, answer);
  }
  const currentValue = () => expression ? evaluate(expression) : 0;
  function fail(error) { notice = error?.message || 'Invalid expression'; }

  async function changeMode(newMode) {
    if (newMode === mode) return;
    mode = newMode;
    if (mode !== 'history') { lastKeypadMode = mode; persist(); }
    draw();
  }

  function input(action) {
    if (action === '−') action = '-';
    notice = '';
    if (action === 'C') { expression = ''; lastCalculation = ''; justEvaluated = false; return draw(); }
    if (action === 'CE') {
      expression = expression.replace(/(?:\(-(?:\d+(?:\.\d*)?|\.\d+)|(?:\d+(?:\.\d*)?|\.\d+)(?:e[+\-]?\d+)?|Ans|π)\)?$/, '');
      justEvaluated = false; lastCalculation = '';
      return draw();
    }
    if (action === '⌫') {
      const fn = expression.match(/(?:asin|acos|atan|sqrt|sin|cos|tan|abs|exp|log|ln)\($/);
      expression = fn ? expression.slice(0, -fn[0].length) : expression.slice(0, -1);
      justEvaluated = false; lastCalculation = '';
      return draw();
    }
    if (action === '=') {
      if (!expression) return draw();
      try {
        const value = evaluate(expression);
        const original = expression;
        expression = formatNumber(value);
        answer = value;
        history.unshift({ expr: original, value });
        history = history.slice(0, 80);
        lastCalculation = original + ' =';
        justEvaluated = true;
        historyPage = 0;
        persist();
      } catch (e) { fail(e); }
      return draw();
    }
    if (action === '±') {
      justEvaluated = false; lastCalculation = '';
      const wrapped = expression.match(/\(-((?:\d+(?:\.\d*)?|\.\d+)(?:e[+\-]?\d+)?|Ans|π)\)$/);
      if (wrapped) expression = expression.slice(0, -wrapped[0].length) + wrapped[1];
      else {
        const ending = expression.match(/((?:\d+(?:\.\d*)?|\.\d+)(?:e[+\-]?\d+)?|Ans|π)$/);
        if (ending) expression = expression.slice(0, -ending[0].length) + '(-' + ending[0] + ')';
        else if (!expression || /[+\-×÷^(]$/.test(expression)) expression += '-';
        else expression = '-(' + expression + ')';
      }
      return draw();
    }
    if (action === 'MS' || action === 'M+' || action === 'M−') {
      try {
        const v = currentValue();
        memory = action === 'MS' ? v : memory + (action === 'M+' ? v : -v);
        if (!Number.isFinite(memory)) throw Error('Memory overflow');
        hasMemory = true;
        persist();
      } catch (e) { fail(e); }
      return draw();
    }
    if (action === 'MC') { memory = 0; hasMemory = false; persist(); return draw(); }
    if (action === 'MR') {
      if (!hasMemory) { notice = 'Memory is empty'; return draw(); }
      action = memory < 0 ? '(' + formatNumber(memory) + ')' : formatNumber(memory);
    }
    if (action === 'DEG/RAD') {
      angle = angle === 'DEG' ? 'RAD' : 'DEG';
      persist(); return draw();
    }
    if (action === 'x²' || action === 'x³' || action === 'xʸ')
      action = action === 'x²' ? '^2' : action === 'x³' ? '^3' : '^';
    if (action === '1/x') {
      expression = expression ? '1/(' + expression + ')' : '1/(';
      justEvaluated = false; lastCalculation = '';
      return draw();
    }
    if (action === '10ˣ') action = '10^(';
    if (action === 'eˣ') action = 'exp(';
    if (action === '√') action = 'sqrt(';
    if (['sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'ln', 'log', 'abs', 'exp'].includes(action)) action += '(';
    // Starting a new number or function after equals clears the result; operators continue it.
    if (justEvaluated && (/^[\d.(πe]/.test(action) || action.startsWith('Ans') || /^[a-z]+\(/.test(action))) expression = '';
    justEvaluated = false;
    lastCalculation = '';

    if (/^[+\-×÷^]$/.test(action)) {
      if (!expression) expression = action === '-' ? '-' : '';
      else if (/[+\-×÷^]$/.test(expression)) {
        if (action === '-' && !expression.endsWith('-')) expression += '-';
        else expression = expression.slice(0, -1) + action;
      } else expression += action;
    } else if (action === ')') {
      const opens = (expression.match(/\(/g) || []).length;
      const closes = (expression.match(/\)/g) || []).length;
      if (opens > closes && canEndValue(expression)) expression += ')';
    } else if (action === '%' || action === '!') {
      if (canEndValue(expression)) expression += action;
    } else if (action === '.') {
      const tail = expression.match(/(?:\d+(?:\.\d*)?|\.\d+)(?:e[+\-]?\d+)?$/);
      if (tail && tail[0].includes('.')) return draw();
      if (canEndValue(expression) && !/\d$/.test(expression)) expression += '×';
      expression += tail ? '.' : '0.';
    } else if (action === '(') {
      if (canEndValue(expression)) expression += '×';
      expression += '(';
    } else {
      const beginsValue = /^[\dπe]|^Ans|^\(|^[a-z]+\($/.test(action);
      const digit = /^\d$/.test(action);
      if (beginsValue && canEndValue(expression) && !(digit && /\d$/.test(expression))) expression += '×';
      if (digit && /(?:^|[+\-×÷^(])0$/.test(expression) && action !== '0') expression = expression.slice(0, -1);
      expression += action;
    }
    if (expression.length > 160) { expression = expression.slice(0, 160); notice = 'Expression limit reached'; }
    draw();
  }

  const txt = (text, x, y, size = 16, color = COLORS.text, align = 'left') =>
    ({ type: 'text', x, y, text: String(text), size, color, align });
  const rect = (x, y, width, height, color) => ({ type: 'rect', x, y, width, height, color });
  const clipped = (s, limit) => s.length > limit ? '…' + s.slice(-(limit - 1)) : s;

  function button(cmds, label, action, x, y, width, height, kind = 'normal', fontSize = 19) {
    const styles = {
      normal: [COLORS.key, COLORS.edge, COLORS.text],
      operation: [COLORS.operation, COLORS.border, COLORS.bright],
      scientific: [COLORS.science, COLORS.sciEdge, COLORS.text],
      memory: [COLORS.memory, COLORS.edge, COLORS.muted],
      clear: [COLORS.red, COLORS.redEdge, COLORS.text],
      equal: [COLORS.equal, COLORS.equal, COLORS.equalText],
      tab: [COLORS.soft, COLORS.border, COLORS.muted],
      selected: [COLORS.operation, COLORS.bright, COLORS.bright]
    };
    const [fill, stroke, ink] = styles[kind] || styles.normal;
    cmds.push(rect(x, y, width, height, stroke));
    cmds.push(rect(x + 1, y + 1, width - 2, height - 3, fill));
    cmds.push(txt(label, x + width / 2, y + height / 2 + 1, fontSize, ink, 'center'));
    hitboxes.push({ x, y, width, height, action });
  }
  function buttonGrid(cmds, rows, x, y, cols, width, height, gapX, gapY) {
    const cw = (width - gapX * (cols - 1)) / cols;
    for (let r = 0; r < rows.length; r++) {
      const row = rows[r];
      for (let c = 0; c < row.length; c++) {
        const label = row[c];
        const action = label === 'RAD' || label === 'DEG' ? 'DEG/RAD' : label;
        let kind = 'normal';
        if (['÷', '×', '−', '+', '%', '√', '(', ')', '±'].includes(label)) kind = 'operation';
        if (['C', 'CE'].includes(label)) kind = 'clear';
        if (label === '=') kind = 'equal';
        if (cols === 6 && !['C', 'CE', '='].includes(label)) kind = 'scientific';
        button(cmds, label, action, x + c * (cw + gapX), y + r * (height + gapY), cw, height, kind,
          label.length > 5 ? 13 : cols === 6 ? 16 : 23);
      }
    }
  }
  function drawHeader(cmds) {
    cmds.push(rect(0, 0, W, H, COLORS.background));
    cmds.push(rect(0, 0, W, 50, COLORS.header));
    cmds.push(rect(0, 49, W, 2, COLORS.border));
    cmds.push(txt('CALCULATOR', 17, 25, 23, COLORS.bright));
    cmds.push(txt('V2', 178, 25, 15, COLORS.muted));
    cmds.push(txt('STATIONNET', 463, 24, 11, COLORS.muted, 'right'));
    const modes = [['STANDARD', 'standard'], ['SCIENTIFIC', 'scientific'], ['HISTORY', 'history']];
    const tabWidth = 144;
    for (let i = 0; i < modes.length; i++) {
      const [name, key] = modes[i];
      button(cmds, name, '@' + key, 12 + i * 156, 59, tabWidth, 43, mode === key ? 'selected' : 'tab', 15);
    }
  }
  function drawDisplay(cmds) {
    cmds.push(rect(12, 111, 456, 126, COLORS.border));
    cmds.push(rect(14, 113, 452, 122, COLORS.display));
    cmds.push(txt('EXPRESSION', 24, 127, 11, COLORS.muted));
    const expressionText = justEvaluated && lastCalculation ? lastCalculation : expression || '0';
    cmds.push(txt(clipped(expressionText, 36), 450, 155, 18, COLORS.text, 'right'));
    let preview = '0';
    if (expression) {
      try { preview = formatNumber(evaluate(expression)); }
      catch (_) { preview = '…'; }
    }
    cmds.push(txt('RESULT', 24, 185, 11, COLORS.muted));
    const resultSize = preview.length > 19 ? 22 : preview.length > 14 ? 29 : preview.length > 10 ? 35 : 43;
    cmds.push(txt(clipped(preview, 28), 451, 207, resultSize, COLORS.bright, 'right'));
    if (notice) cmds.push(txt(clipped(notice, 64), 19, 247, 13, '#f2b2a7'));
    else cmds.push(txt(`${angle}   |   ${hasMemory ? 'M: ' + clipped(formatNumber(memory), 13) : 'M: EMPTY'}   |   Ans: ${clipped(formatNumber(answer), 12)}`,
      19, 251, 13, COLORS.muted));
  }
  function drawMemory(cmds) {
    const labels = ['MC', 'MR', 'M+', 'M−', 'MS'];
    const margin = 12, gap = 7, cw = (456 - 4 * gap) / 5;
    labels.forEach((label, i) => button(cmds, label, label, margin + i * (cw + gap), 261, cw, 43,
      'memory', 17));
  }
  function drawStandard(cmds) {
    const rows = [
      ['C', 'CE', '⌫', '%'],
      ['(', ')', '√', '÷'],
      ['7', '8', '9', '×'],
      ['4', '5', '6', '−'],
      ['1', '2', '3', '+'],
      ['±', '0', '.', '=']
    ];
    buttonGrid(cmds, rows, 12, 316, 4, 456, 70, 8, 8);
    cmds.push(txt('TOUCH KEYS OR USE KEYBOARD', 240, 790, 10, COLORS.muted, 'center'));
  }
  function drawScientific(cmds) {
    const advanced = [
      ['sin', 'cos', 'tan', 'asin', 'acos', 'atan'],
      ['ln', 'log', 'exp', '√', 'π', 'e'],
      ['x²', 'abs', 'xʸ', '1/x', '!', angle]
    ];
    const main = [
      ['C', 'CE', '⌫', '%'],
      ['(', ')', 'Ans', '÷'],
      ['7', '8', '9', '×'],
      ['4', '5', '6', '−'],
      ['1', '2', '3', '+'],
      ['±', '0', '.', '=']
    ];
    buttonGrid(cmds, advanced, 12, 315, 6, 456, 43, 6, 5);
    buttonGrid(cmds, main, 12, 465, 4, 456, 46, 8, 7);
    cmds.push(txt('ANGLES: TAP DEG / RAD TO SWITCH', 240, 793, 10, COLORS.muted, 'center'));
  }
  function drawHistory(cmds) {
    const perPage = 7;
    const lastPage = Math.max(0, Math.ceil(history.length / perPage) - 1);
    historyPage = Math.min(historyPage, lastPage);
    cmds.push(txt(`${history.length} SAVED CALCULATIONS`, 16, 286, 15, COLORS.muted));
    if (!history.length) {
      cmds.push(txt('No calculations yet.', 240, 418, 21, COLORS.text, 'center'));
      cmds.push(txt('Results appear here after pressing =', 240, 455, 15, COLORS.muted, 'center'));
    }
    for (let index = 0; index < perPage; index++) {
      const absoluteIndex = historyPage * perPage + index;
      const entry = history[absoluteIndex];
      if (!entry) break;
      const y = 307 + index * 57;
      cmds.push(rect(12, y, 456, 53, COLORS.border));
      cmds.push(rect(13, y + 1, 454, 51, COLORS.panel));
      cmds.push(txt(`${absoluteIndex + 1}. ${clipped(entry.expr, 38)}`, 23, y + 17, 15, COLORS.text));
      cmds.push(txt('= ' + clipped(formatNumber(entry.value), 25), 455, y + 39, 18, COLORS.bright, 'right'));
      hitboxes.push({ x: 12, y, width: 456, height: 53, action: '@recall:' + absoluteIndex });
    }
    button(cmds, '◀ PREV', '@previous', 12, 719, 122, 52, 'operation', 15);
    button(cmds, `${historyPage + 1} / ${lastPage + 1}`, '@nop', 140, 719, 104, 52, 'tab', 15);
    button(cmds, 'NEXT ▶', '@next', 250, 719, 100, 52, 'operation', 15);
    button(cmds, 'CLEAR', '@clearHistory', 356, 719, 112, 52, 'clear', 15);
    cmds.push(txt('TAP AN ENTRY TO RECALL ITS RESULT', 240, 792, 11, COLORS.muted, 'center'));
  }

  function draw() {
    hitboxes = [];
    const cmds = [];
    drawHeader(cmds);
    drawDisplay(cmds);
    if (mode === 'history') drawHistory(cmds);
    else {
      drawMemory(cmds);
      if (mode === 'scientific') drawScientific(cmds);
      else drawStandard(cmds);
    }
    ui.update('calc', ui.canvas(W, H, cmds, {
      id: 'calc', label: 'Calculator V2 graphical keypad',
      onPointer: pointer
    }));
    ui.update('readout', ui.statusBar(notice || `Calculator V2 • ${mode.toUpperCase()} • ${angle}`, { id: 'readout' }));
  }

  async function activate(action) {
    if (action === '@nop') return;
    if (action.startsWith('@recall:')) {
      const entry = history[Number(action.slice(8))];
      if (!entry) return;
      expression = formatNumber(entry.value);
      answer = entry.value;
      lastCalculation = entry.expr + ' =';
      justEvaluated = true;
      mode = lastKeypadMode;
      notice = '';
      draw();
      return;
    }
    if (action.startsWith('@') && ['@standard', '@scientific', '@history'].includes(action)) {
      await changeMode(action.slice(1));
      return;
    }
    if (action === '@previous') { historyPage = Math.max(0, historyPage - 1); draw(); return; }
    if (action === '@next') { historyPage = Math.min(Math.max(0, Math.ceil(history.length / 7) - 1), historyPage + 1); draw(); return; }
    if (action === '@clearHistory') {
      if (!history.length) return;
      const result = await ui.dialog('Clear calculation history?',
        [ui.text('All saved calculations will be removed. Memory remains unchanged.')],
        [{ label: 'Clear', value: 'yes' }, { label: 'Cancel', value: 'no' }]);
      if (result === 'yes') { history = []; historyPage = 0; persist(); draw(); }
      return;
    }
    input(action);
  }
  function pointer(e) {
    if (e.phase !== 'start') return; // Avoid handling both start and gesture/tap.
    const hit = hitboxes.find(b => e.x >= b.x && e.x < b.x + b.width && e.y >= b.y && e.y < b.y + b.height);
    if (hit) void activate(hit.action);
  }
  ui.onKey(({ key, phase, ctrlKey, altKey, metaKey }) => {
    if (phase !== 'down' || ctrlKey || altKey || metaKey) return;
    const map = {
      Enter: '=', '=': '=', Backspace: '⌫', Delete: 'CE', Escape: 'C',
      '*': '×', x: '×', '/': '÷', '-': '-', '+': '+', '^': '^',
      p: 'π', P: 'π', a: 'Ans', A: 'Ans', r: '√', R: '√',
      s: 'sin', S: 'sin', c: 'cos', t: 'tan', l: 'ln'
    };
    const action = map[key] || (/^[0-9().%!π]$/.test(key) ? key : null);
    if (action) input(action);
  });
  // Render the shell only once. In-place canvas updates preserve the mobile layout.
  ui.render(
    ui.canvas(W, H, [], { id: 'calc', label: 'Calculator V2 graphical keypad', onPointer: pointer }),
    ui.statusBar('Calculator V2 ready', { id: 'readout' })
  );
  draw();
});
