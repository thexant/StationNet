// StationNet Calculator - key-entry model.
// Both the keyboard and the touch keypad call press(action); nothing here knows
// about the screen. The engine is passed in because bundled modules cannot import
// each other (see the StationNet package notes).

export const MAX_TOKENS = 200;
export const MAX_DEPTH = 32;
export const MAX_HISTORY = 100;

const BASIC = new Set([
  "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "+", "-", "*", "/",
  "(", ")", "%", "sqrt", "sq", "neg", "back", "ce", "c", "=",
  "mc", "mr", "mplus", "mminus", "ms", "mode", "mode:standard", "mode:scientific",
]);
// "2nd" alternates, applied when the 2nd key is lit.
const INVERSE = { sin: "asin", cos: "acos", tan: "atan", log: "pow10", ln: "powe", sq: "cube", sqrt: "cbrt" };
const FUNC_TOKEN = {
  sin: "sin(", cos: "cos(", tan: "tan(", asin: "asin(", acos: "acos(", atan: "atan(",
  log: "log(", ln: "ln(", pow10: "10^(", powe: "e^(", sqrt: "√(", cbrt: "∛(", abs: "abs(",
};
const POSTFIX_TOKEN = { "!": "!", "%": "%", sq: "²", cube: "³", recip: "⁻¹" };
const BINARY_ACTION = new Set(["+", "-", "*", "/", "^", "root"]);
const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

export function createCalculator(E, saved) {
  const s = {
    tokens: [],
    locked: false,     // last token is a whole value (result / recall): the next digit replaces it
    evaluated: false,  // a result is showing
    error: null,
    notice: "",
    lastExpr: "",
    resultValue: 0,
    memory: 0,
    hasMemory: false,
    mode: "standard",
    angle: "DEG",
    inv: false,
    history: [],       // newest first: { t: tokens, v: full-precision value token, r: display text }
  };

  const k = E.kind;
  const last = () => s.tokens[s.tokens.length - 1];

  function pushTok(t) {
    if (s.tokens.length >= MAX_TOKENS) return false;
    s.tokens.push(t);
    s.locked = false;
    return true;
  }
  function popTok() {
    s.tokens.pop();
    s.locked = false;
  }
  function endsOperand() {
    const t = last();
    if (t === undefined) return false;
    const x = k(t);
    return x === "num" || x === "const" || x === "close" || x === "post";
  }
  function openDepth() {
    let d = 0;
    for (const t of s.tokens) {
      const x = k(t);
      if (x === "open" || x === "func") d++;
      else if (x === "close") d--;
    }
    return d;
  }
  function isUnaryAt(i) {
    if (i === 0) return true;
    const x = k(s.tokens[i - 1]);
    return x === "bin" || x === "open" || x === "func";
  }
  /** Index where the operand that ends the expression begins, or -1. */
  function operandStart() {
    let i = s.tokens.length - 1;
    while (i >= 0 && k(s.tokens[i]) === "post") i--;
    if (i < 0) return -1;
    const x = k(s.tokens[i]);
    if (x === "num" || x === "const") return i;
    if (x === "close") {
      let d = 0;
      for (let j = i; j >= 0; j--) {
        const y = k(s.tokens[j]);
        if (y === "close") d++;
        else if (y === "open" || y === "func") { d--; if (d === 0) return j; }
      }
    }
    return -1;
  }
  /** Drop a dangling exponent ("1E", "1E-") from the number being typed. */
  function sanitize() {
    const t = last();
    if (t !== undefined && E.isNumberToken(t) && /E[+-]?$/.test(t)) {
      s.tokens[s.tokens.length - 1] = t.replace(/E[+-]?$/, "");
    }
  }
  function cleanCopy() {
    const c = s.tokens.slice();
    const t = c[c.length - 1];
    if (t !== undefined && E.isNumberToken(t)) c[c.length - 1] = t.replace(/E[+-]?$/, "");
    return c;
  }
  function freshStart() {
    if (s.evaluated) {
      s.tokens = [];
      s.locked = false;
      s.evaluated = false;
      s.lastExpr = "";
    }
  }
  function clearAll() {
    s.tokens = [];
    s.locked = false;
    s.evaluated = false;
    s.error = null;
    s.notice = "";
    s.lastExpr = "";
  }
  /** Replace the current entry with a whole value (memory recall, history result). */
  function insertValue(v) {
    if (s.evaluated) { s.tokens = []; s.evaluated = false; s.lastExpr = ""; }
    sanitize();
    const t = last();
    if (t !== undefined && E.isNumberToken(t)) popTok();
    else if (endsOperand()) pushTok("*");
    if (pushTok(E.numberToToken(v))) s.locked = true;
  }
  function currentValue() {
    if (s.evaluated) return s.resultValue;
    if (!s.tokens.length) return 0;
    const r = E.tryEvaluate(E.closeParens(cleanCopy()), { angle: s.angle });
    return r.ok ? r.value : undefined;
  }

  // ------------------------------------------------------------ entry ---

  /** True when the last token is a minus sign that starts an operand (a typed "-5"). */
  function endsWithUnaryMinus() {
    return last() === "-" && isUnaryAt(s.tokens.length - 1);
  }

  function digit(d) {
    freshStart();
    const t = last();
    if (t !== undefined && E.isNumberToken(t) && !s.locked) {
      const [mant, exp] = t.split("E");
      const negative = mant[0] === "-";
      const m = negative ? mant.slice(1) : mant;
      if (exp !== undefined) {
        if (exp.replace(/^[+-]/, "").length >= 3) return;
        s.tokens[s.tokens.length - 1] = t + d;
      } else if (m === "0") {
        if (d !== "0") s.tokens[s.tokens.length - 1] = (negative ? "-" : "") + d;
      } else {
        if (m.replace(/\./g, "").length >= 16) return;
        s.tokens[s.tokens.length - 1] = t + d;
      }
      return;
    }
    // A minus typed at the start of an operand becomes the sign of the number: "-5" is one entry.
    if (endsWithUnaryMinus()) { popTok(); pushTok("-" + d); return; }
    if (t !== undefined && E.isNumberToken(t)) popTok(); // replace a locked value
    else if (endsOperand()) pushTok("*");
    pushTok(d);
  }

  function point() {
    freshStart();
    const t = last();
    if (t !== undefined && E.isNumberToken(t) && !s.locked) {
      if (!t.includes(".") && !t.includes("E")) s.tokens[s.tokens.length - 1] = t + ".";
      return;
    }
    if (endsWithUnaryMinus()) { popTok(); pushTok("-0."); return; }
    if (t !== undefined && E.isNumberToken(t)) popTok();
    else if (endsOperand()) pushTok("*");
    pushTok("0.");
  }

  function exponent() {
    const t = last();
    if (t === undefined || !E.isNumberToken(t) || s.locked || t.includes("E")) return;
    s.tokens[s.tokens.length - 1] = t + "E";
  }

  function binary(op) {
    const t = last();
    // Right after EXP the sign belongs to the exponent: 1E then - gives 1E-.
    if (t !== undefined && E.isNumberToken(t) && /E$/.test(t) && (op === "+" || op === "-")) {
      s.tokens[s.tokens.length - 1] = t + op;
      return;
    }
    sanitize();
    s.evaluated = false;
    const l = last();
    if (l === undefined) {
      if (op === "-") pushTok("-");
      else if (op !== "+") { pushTok("0"); pushTok(op); }
      return;
    }
    const lk = k(l);
    if (lk === "open" || lk === "func") {
      if (op === "-") pushTok("-"); // unary minus is fine here, other operators are not
      return;
    }
    if (lk === "bin") {
      const unaryMinus = l === "-" && isUnaryAt(s.tokens.length - 1);
      if (unaryMinus && op === "-") return;
      if (op === "-" && !unaryMinus && l !== "+" && l !== "-") { pushTok("-"); return; } // 5 × −3
      while (s.tokens.length && k(last()) === "bin") popTok(); // a new operator replaces the old one
      binary(op);
      return;
    }
    pushTok(op);
  }

  function open() {
    sanitize();
    freshStart();
    if (openDepth() >= MAX_DEPTH) return;
    if (endsOperand()) pushTok("*");
    pushTok("(");
  }
  function close() {
    sanitize();
    s.evaluated = false;
    if (openDepth() <= 0 || !endsOperand()) return;
    pushTok(")");
  }

  function func(token) {
    sanitize();
    s.evaluated = false;
    if (openDepth() >= MAX_DEPTH) return;
    if (endsOperand()) {
      // 9 then √ gives √(9): the function wraps the number just entered.
      const st = operandStart();
      if (st >= 0 && s.tokens.length + 2 <= MAX_TOKENS) {
        s.tokens.splice(st, 0, token);
        s.tokens.push(")");
        s.locked = false;
        return;
      }
      pushTok("*");
    }
    pushTok(token);
  }

  function constant(c) {
    sanitize();
    freshStart();
    if (endsOperand()) pushTok("*");
    pushTok(c);
  }

  function postfix(tok) {
    sanitize();
    if (!endsOperand()) return;
    s.evaluated = false;
    pushTok(tok);
  }

  function negate() {
    sanitize();
    const t = last();
    const i = s.tokens.length - 1;
    if (t !== undefined && E.isNumberToken(t)) {
      if (Number(t) === 0) return;
      if (i > 0 && s.tokens[i - 1] === "-" && isUnaryAt(i - 1)) { s.tokens.splice(i - 1, 1); return; }
      s.tokens[i] = t[0] === "-" ? t.slice(1) : "-" + t;
      s.evaluated = false;
      return;
    }
    if (endsOperand()) {
      const st = operandStart();
      if (st < 0) return;
      s.evaluated = false;
      if (st > 0 && s.tokens[st - 1] === "-" && isUnaryAt(st - 1)) s.tokens.splice(st - 1, 1);
      else if (s.tokens.length < MAX_TOKENS) { s.tokens.splice(st, 0, "-"); s.locked = false; }
      return;
    }
    if (t === "-" && isUnaryAt(i)) popTok();
    else if (t === undefined || ["bin", "open", "func"].includes(k(t))) pushTok("-");
  }

  function back() {
    const t = last();
    if (t === undefined) return;
    s.evaluated = false;
    if (E.isNumberToken(t) && !s.locked) {
      const shorter = t.slice(0, -1);
      if (shorter === "" || shorter === "-") popTok();
      else s.tokens[s.tokens.length - 1] = shorter;
    } else popTok();
  }

  function clearEntry() {
    if (s.evaluated) { clearAll(); return; }
    const t = last();
    if (t !== undefined && E.isNumberToken(t)) popTok();
  }

  function equals() {
    sanitize();
    if (!s.tokens.length || s.evaluated) return;
    const closed = E.closeParens(s.tokens);
    const r = E.tryEvaluate(closed, { angle: s.angle });
    if (!r.ok) {
      s.error = { code: r.error.code, message: r.error.message };
      return;
    }
    const entry = { t: closed, v: E.numberToToken(r.value), r: E.format(r.value) };
    const trivial = closed.length === 1 && k(closed[0]) === "num";
    if (!trivial) {
      s.history.unshift(entry);
      if (s.history.length > MAX_HISTORY) s.history.length = MAX_HISTORY;
    }
    s.resultValue = r.value;
    s.lastExpr = E.toDisplay(closed) + " =";
    s.tokens = [entry.v];
    s.locked = true;
    s.evaluated = true;
  }

  function memory(action) {
    if (action === "mc") { s.memory = 0; s.hasMemory = false; return; }
    if (action === "mr") {
      if (!s.hasMemory) { s.notice = "Memory is empty"; return; }
      insertValue(s.memory);
      return;
    }
    const v = currentValue();
    if (v === undefined) { s.notice = "Finish the expression first"; return; }
    let next = v;
    if (action === "mplus") next = s.memory + v;
    if (action === "mminus") next = s.memory - v;
    if (!Number.isFinite(next)) { s.notice = "Overflow"; return; }
    s.memory = next === 0 ? 0 : next;
    s.hasMemory = true;
    // As on a desk calculator, the next digit starts a new entry.
    if (!s.evaluated && s.tokens.length === 1 && k(s.tokens[0]) === "num") s.locked = true;
  }

  // ------------------------------------------------------------ dispatch ---

  /** Handle one key. Returns false when the key does not exist in the current mode. */
  function press(action) {
    if (s.mode === "standard" && !BASIC.has(action)) return false;
    s.notice = "";
    s.error = null;

    if (s.mode === "scientific" && s.inv && own(INVERSE, action)) {
      action = INVERSE[action];
      s.inv = false;
    }

    if (/^[0-9]$/.test(action)) digit(action);
    else if (action === ".") point();
    else if (action === "exp") exponent();
    else if (BINARY_ACTION.has(action)) binary(action);
    else if (action === "(") open();
    else if (action === ")") close();
    else if (own(FUNC_TOKEN, action)) func(FUNC_TOKEN[action]);
    else if (own(POSTFIX_TOKEN, action)) postfix(POSTFIX_TOKEN[action]);
    else if (action === "pi") constant("π");
    else if (action === "e") constant("e");
    else if (action === "neg") negate();
    else if (action === "back") back();
    else if (action === "ce") clearEntry();
    else if (action === "c") clearAll();
    else if (action === "=") equals();
    else if (["mc", "mr", "ms", "mplus", "mminus"].includes(action)) memory(action);
    else if (action === "inv") s.inv = !s.inv;
    else if (action === "angle") s.angle = E.ANGLES[(E.ANGLES.indexOf(s.angle) + 1) % E.ANGLES.length];
    else if (action === "mode" || action === "mode:standard" || action === "mode:scientific") {
      s.mode = action === "mode" ? (s.mode === "standard" ? "scientific" : "standard")
        : action.slice(5);
      if (s.mode === "standard") s.inv = false;
    } else return false;
    return true;
  }

  // ------------------------------------------------------------- history ---

  function recallExpression(i) {
    const e = s.history[i];
    if (!e) return false;
    s.tokens = e.t.slice();
    s.locked = false;
    s.evaluated = false;
    s.error = null;
    s.notice = "";
    return true;
  }
  function useResult(i) {
    const e = s.history[i];
    if (!e) return false;
    s.error = null;
    s.notice = "";
    insertValue(Number(e.v));
    return true;
  }
  function clearHistory() {
    s.history = [];
  }
  function historyRows(offset, count) {
    return s.history.slice(offset, offset + count).map((e, n) => ({
      index: offset + n, expr: E.toDisplay(e.t), result: e.r,
    }));
  }

  // ---------------------------------------------------------------- view ---

  const minus = (str) => str.replace(/^-/, "−");

  function preview(primary) {
    if (s.tokens.length < 2) return "";
    const r = E.tryEvaluate(E.closeParens(cleanCopy()), { angle: s.angle });
    if (!r.ok) return "";
    const text = minus(E.format(r.value));
    return text === primary ? "" : "= " + text;
  }

  /** Everything the screen needs to show. */
  function view() {
    let primary;
    let secondary = "";
    let tone = "normal";
    let secondaryTone = "dim";
    if (s.error) {
      primary = s.error.message;
      tone = "error";
      secondary = E.toDisplay(s.tokens);
    } else if (s.evaluated) {
      primary = minus(E.format(s.resultValue));
      secondary = s.lastExpr;
    } else {
      primary = s.tokens.length ? E.toDisplay(s.tokens) : "0";
      secondary = preview(primary);
    }
    if (s.notice) { secondary = s.notice; secondaryTone = "error"; }
    return {
      primary, secondary, tone, secondaryTone,
      memory: s.hasMemory,
      memoryText: s.hasMemory ? minus(E.format(s.memory)) : "",
      inv: s.inv,
      angle: s.mode === "scientific" ? s.angle : null,
      mode: s.mode,
      historyCount: s.history.length,
    };
  }

  // ---------------------------------------------------------- persistence ---

  function snapshot() {
    return {
      v: 1,
      mode: s.mode,
      angle: s.angle,
      memory: s.hasMemory ? s.memory : null,
      history: s.history,
    };
  }
  function restore(data) {
    if (!data || typeof data !== "object") return;
    if (data.mode === "scientific") s.mode = "scientific";
    if (E.ANGLES.includes(data.angle)) s.angle = data.angle;
    if (typeof data.memory === "number" && Number.isFinite(data.memory)) {
      s.memory = data.memory;
      s.hasMemory = true;
    }
    if (Array.isArray(data.history)) {
      for (const e of data.history.slice(0, MAX_HISTORY)) {
        const ok = e && Array.isArray(e.t) && e.t.length > 0 && e.t.length <= MAX_TOKENS
          && e.t.every((t) => k(t) !== "bad")
          && typeof e.v === "string" && k(e.v) === "num"
          && typeof e.r === "string" && e.r.length < 40;
        if (ok) s.history.push({ t: e.t.slice(), v: e.v, r: e.r });
      }
    }
  }
  restore(saved);

  return {
    state: s, press, view, snapshot, currentValue,
    recallExpression, useResult, clearHistory, historyRows,
  };
}
