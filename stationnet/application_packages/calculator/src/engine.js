// StationNet Calculator - math engine.
// Pure functions only: no DOM, no imports, no eval(). Expressions are arrays of
// string tokens, parsed by a small recursive-descent parser.
//
// Token vocabulary
//   number     "12", "0.5", "1.5E+3", "-5" (a signed literal, produced by results)
//   constant   "π" "e"
//   binary     "+" "-" "*" "/" "^" "root"      (a root b  =  a to the 1/b)
//   postfix    "!" "%" "²" "³" "⁻¹"
//   grouping   "(" ")"  and function openers such as "sin(" "√("

export const ANGLES = ["DEG", "RAD", "GRAD"];

export class CalcError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "CalcError";
    this.code = code;
  }
}

const ERR = {
  DIV0: ["DIV0", "Cannot divide by zero"],
  UNDEF: ["UNDEF", "Result is undefined"],
  DOMAIN: ["DOMAIN", "Invalid input"],
  OVERFLOW: ["OVERFLOW", "Overflow"],
  SYNTAX: ["SYNTAX", "Invalid expression"],
};
function fail(kind) {
  throw new CalcError(kind[0], kind[1]);
}

// ---------------------------------------------------------------- tokens ---

const NUM = /^-?(?:\d+\.?\d*|\.\d+)(?:E[+-]?\d+)?$/; // complete, evaluable
const NUM_LOOSE = /^-?(?:\d+\.?\d*|\.\d+)(?:E[+-]?\d*)?$/; // may be mid-entry ("1E-")
const BINARY = new Set(["+", "-", "*", "/", "^", "root"]);
const POSTFIX = new Set(["!", "%", "²", "³", "⁻¹"]);

export const FUNCTIONS = [
  "sin(", "cos(", "tan(", "asin(", "acos(", "atan(",
  "log(", "ln(", "10^(", "e^(", "√(", "∛(", "abs(",
];

/** Classify a token: num | const | open | close | bin | post | func | bad. */
export function kind(t) {
  if (typeof t !== "string") return "bad";
  if (NUM.test(t)) return "num";
  if (t === "π" || t === "e") return "const";
  if (t === "(") return "open";
  if (t === ")") return "close";
  if (BINARY.has(t)) return "bin";
  if (POSTFIX.has(t)) return "post";
  if (FUNCTIONS.includes(t)) return "func";
  return "bad";
}

/** True for a number token, including one still being typed ("1E", "1E-"). */
export function isNumberToken(t) {
  return typeof t === "string" && NUM_LOOSE.test(t);
}

/** Append closing parentheses for any that are still open. */
export function closeParens(tokens) {
  let depth = 0;
  for (const t of tokens) {
    const k = kind(t);
    if (k === "open" || k === "func") depth++;
    else if (k === "close") depth = Math.max(0, depth - 1);
  }
  const out = tokens.slice();
  while (depth-- > 0) out.push(")");
  return out;
}

const WORDS = {
  sin: "sin(", cos: "cos(", tan: "tan(", asin: "asin(", acos: "acos(",
  atan: "atan(", log: "log(", ln: "ln(", sqrt: "√(", cbrt: "∛(", abs: "abs(",
};

/**
 * Convert a typed string such as "2+3*sin(30)" into tokens. Used by tests and
 * history restore; the keypad builds tokens directly.
 */
export function tokenize(src) {
  const out = [];
  let i = 0;
  while (i < src.length) {
    const rest = src.slice(i);
    let m;
    if (/^\s/.test(rest)) { i++; continue; }
    if (rest.startsWith("⁻¹")) { out.push("⁻¹"); i += 2; continue; }
    if ((m = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(rest))) {
      out.push(m[0].replace("e", "E"));
      i += m[0].length;
      continue;
    }
    if ((m = /^[A-Za-z]+/.exec(rest))) {
      const w = m[0].toLowerCase();
      if (WORDS[w]) {
        i += m[0].length;
        if (src[i] !== "(") fail(ERR.SYNTAX);
        i++;
        out.push(WORDS[w]);
      } else if (w === "pi") { out.push("π"); i += m[0].length; }
      else if (w === "e") { out.push("e"); i += 1; }
      else if (w === "root") { out.push("root"); i += m[0].length; }
      else fail(ERR.SYNTAX);
      continue;
    }
    const c = src[i];
    const map = { "×": "*", "÷": "/", "−": "-", "π": "π" };
    if ("+-*/^()!%".includes(c)) out.push(c);
    else if (map[c]) out.push(map[c]);
    else if (c === "²" || c === "³") out.push(c);
    else if (c === "√" && src[i + 1] === "(") { out.push("√("); i++; }
    else if (c === "∛" && src[i + 1] === "(") { out.push("∛("); i++; }
    else fail(ERR.SYNTAX);
    i++;
  }
  return out;
}

// --------------------------------------------------------------- parsing ---

function parse(input) {
  const toks = closeParens(input);
  let depth = 0;
  for (const t of toks) {
    const k = kind(t);
    if (k === "bad") fail(ERR.SYNTAX);
    if (k === "open" || k === "func") depth++;
    if (k === "close" && --depth < 0) fail(ERR.SYNTAX);
  }
  if (!toks.length) fail(ERR.SYNTAX);

  let pos = 0;
  const peek = () => toks[pos];
  const startsOperand = () => {
    const t = toks[pos];
    if (t === undefined) return false;
    const k = kind(t);
    if (k === "func" || k === "open" || k === "const") return true;
    return k === "num" && pos > 0 && kind(toks[pos - 1]) !== "num";
  };
  const expectClose = () => {
    if (toks[pos] !== ")") fail(ERR.SYNTAX);
    pos++;
  };

  // expr := term (('+'|'-') term)*
  function parseExpr() {
    let l = parseTerm();
    while (peek() === "+" || peek() === "-") {
      const op = toks[pos++];
      l = { t: "bin", op, l, r: parseTerm() };
    }
    return l;
  }
  // term := unary (('*'|'/') unary | implicit-multiplication unary)*
  function parseTerm() {
    let l = parseUnary();
    for (;;) {
      const t = peek();
      if (t === "*" || t === "/") {
        pos++;
        l = { t: "bin", op: t, l, r: parseUnary() };
      } else if (startsOperand()) {
        l = { t: "bin", op: "*", l, r: parseUnary() };
      } else break;
    }
    return l;
  }
  // unary := ('-'|'+') unary | power        so -2^2 = -(2^2)
  function parseUnary() {
    const t = peek();
    if (t === "-") { pos++; return { t: "neg", a: parseUnary() }; }
    if (t === "+") { pos++; return parseUnary(); }
    return parsePower();
  }
  // power := postfix (('^'|'root') unary)?   right-associative: 2^3^2 = 2^(3^2)
  function parsePower() {
    const base = parsePostfix();
    const t = peek();
    if (t === "^" || t === "root") {
      pos++;
      return { t: "bin", op: t, l: base, r: parseUnary() };
    }
    return base;
  }
  function parsePostfix() {
    let a = parsePrimary();
    for (;;) {
      const t = peek();
      if (t === "%") { pos++; a = { t: "pct", a }; }
      else if (POSTFIX.has(t)) { pos++; a = { t: "post", op: t, a }; }
      else break;
    }
    return a;
  }
  function parsePrimary() {
    const t = toks[pos];
    if (t === undefined) fail(ERR.SYNTAX);
    const k = kind(t);
    if (k === "num") { pos++; return { t: "num", v: Number(t) }; }
    if (k === "const") { pos++; return { t: "num", v: t === "π" ? Math.PI : Math.E }; }
    if (k === "open") { pos++; const a = parseExpr(); expectClose(); return a; }
    if (k === "func") { pos++; const a = parseExpr(); expectClose(); return { t: "fn", f: t, a }; }
    return fail(ERR.SYNTAX);
  }

  const ast = parseExpr();
  if (pos !== toks.length) fail(ERR.SYNTAX);
  return ast;
}

// ------------------------------------------------------------ evaluation ---

function check(v) {
  if (Number.isNaN(v)) fail(ERR.DOMAIN);
  if (!Number.isFinite(v)) fail(ERR.OVERFLOW);
  return v === 0 ? 0 : v; // normalise -0
}

/** Round away cancellation noise: 0.1 + 0.2 - 0.3 is 0, not 5.5e-17. */
function addSub(a, b, r) {
  return Math.abs(r) <= 1e-15 * Math.max(Math.abs(a), Math.abs(b)) ? 0 : r;
}

function power(a, b) {
  if (a === 0 && b < 0) fail(ERR.DIV0);
  return check(Math.pow(a, b)); // negative base with fractional exponent -> NaN -> DOMAIN
}

function rootPositive(a, n) {
  const r = Math.pow(a, 1 / n);
  const k = Math.round(r);
  if (Number.isInteger(n) && n > 0 && Math.pow(k, n) === a) return k; // exact: 64 root 3 = 4
  return r;
}
function root(a, n) {
  if (n === 0) fail(ERR.DOMAIN);
  if (a === 0) { if (n < 0) fail(ERR.DIV0); return 0; }
  if (a < 0) {
    if (Number.isInteger(n) && Math.abs(n) % 2 === 1) return check(-rootPositive(-a, n));
    fail(ERR.DOMAIN);
  }
  return check(rootPositive(a, n));
}

function factorial(v) {
  if (!Number.isInteger(v) || v < 0) fail(ERR.DOMAIN);
  if (v > 170) fail(ERR.OVERFLOW);
  let r = 1;
  for (let i = 2; i <= v; i++) r *= i;
  return r;
}

const S3 = Math.sqrt(3) / 2;
const SIN30 = [0, 0.5, S3, 1, S3, 0.5, 0, -0.5, -S3, -1, -S3, -0.5]; // sin(30° * index)

function toDegrees(x, angle) {
  if (angle === "RAD") return (x * 180) / Math.PI;
  if (angle === "GRAD") return (x * 9) / 10;
  return x;
}
function toRadians(x, angle) {
  if (angle === "RAD") return x;
  return (toDegrees(x, angle) * Math.PI) / 180;
}
function fromRadians(r, angle) {
  if (angle === "RAD") return r;
  const v = angle === "GRAD" ? (r * 200) / Math.PI : (r * 180) / Math.PI;
  const k = Math.round(v);
  return Math.abs(v - k) < 1e-10 * Math.max(1, Math.abs(v)) ? k : v; // asin(.5) = 30, not 30.000000000000004
}
/** Integer k if v is within rounding noise of k * step, else null. */
function nearMultiple(v, step) {
  const k = Math.round(v / step);
  const tol = 1e-10 + Math.abs(v) * 1e-14;
  return Math.abs(v - k * step) <= tol ? k : null;
}

function trig(fn, x, angle) {
  const d = toDegrees(x, angle);
  if (fn === "sin" || fn === "cos") {
    const k = nearMultiple(d + (fn === "cos" ? 90 : 0), 30); // exact values at multiples of 30 degrees
    if (k !== null) return SIN30[((k % 12) + 12) % 12];
    return fn === "sin" ? Math.sin(toRadians(x, angle)) : Math.cos(toRadians(x, angle));
  }
  const k90 = nearMultiple(d, 90);
  if (k90 !== null) {
    if (k90 % 2 !== 0) fail(ERR.DOMAIN); // tan(90) is undefined
    return 0;
  }
  const k45 = nearMultiple(d, 45);
  if (k45 !== null) return ((k45 % 4) + 4) % 4 === 1 ? 1 : -1;
  return Math.tan(toRadians(x, angle));
}

const FN = {
  "sin(": (x, c) => trig("sin", x, c.angle),
  "cos(": (x, c) => trig("cos", x, c.angle),
  "tan(": (x, c) => trig("tan", x, c.angle),
  "asin(": (x, c) => { if (Math.abs(x) > 1) fail(ERR.DOMAIN); return fromRadians(Math.asin(x), c.angle); },
  "acos(": (x, c) => { if (Math.abs(x) > 1) fail(ERR.DOMAIN); return fromRadians(Math.acos(x), c.angle); },
  "atan(": (x, c) => fromRadians(Math.atan(x), c.angle),
  "log(": (x) => { if (x <= 0) fail(ERR.DOMAIN); return Math.log10(x); },
  "ln(": (x) => { if (x <= 0) fail(ERR.DOMAIN); return Math.log(x); },
  "10^(": (x) => Math.pow(10, x),
  "e^(": (x) => Math.exp(x),
  "√(": (x) => { if (x < 0) fail(ERR.DOMAIN); return Math.sqrt(x); },
  "∛(": (x) => Math.cbrt(x),
  "abs(": (x) => Math.abs(x),
};

function postfix(op, v) {
  switch (op) {
    case "!": return factorial(v);
    case "²": return check(v * v);
    case "³": return check(v * v * v);
    default: // "⁻¹"
      if (v === 0) fail(ERR.DIV0);
      return check(1 / v);
  }
}

function binary(n, ctx) {
  const a = ev(n.l, ctx);
  // "200 + 10%" means 200 + 10% of 200; "200 - 10%" likewise.
  if ((n.op === "+" || n.op === "-") && n.r.t === "pct") {
    const delta = a * (ev(n.r.a, ctx) / 100);
    const r = n.op === "+" ? a + delta : a - delta;
    return check(addSub(a, delta, r));
  }
  const b = ev(n.r, ctx);
  switch (n.op) {
    case "+": return check(addSub(a, b, a + b));
    case "-": return check(addSub(a, b, a - b));
    case "*": return check(a * b);
    case "/":
      if (b === 0) fail(a === 0 ? ERR.UNDEF : ERR.DIV0);
      return check(a / b);
    case "^": return power(a, b);
    default: return root(a, b); // "root"
  }
}

function ev(n, ctx) {
  switch (n.t) {
    case "num": return check(n.v);
    case "neg": return check(-ev(n.a, ctx));
    case "pct": return check(ev(n.a, ctx) / 100);
    case "post": return postfix(n.op, ev(n.a, ctx));
    case "fn": return check(FN[n.f](ev(n.a, ctx), ctx));
    default: return binary(n, ctx);
  }
}

/**
 * Evaluate tokens to a number. Missing closing parentheses are added.
 * Throws CalcError with code DIV0 | UNDEF | DOMAIN | OVERFLOW | SYNTAX.
 */
export function evaluate(tokens, opts = {}) {
  const angle = ANGLES.includes(opts.angle) ? opts.angle : "DEG";
  try {
    return check(ev(parse(tokens), { angle }));
  } catch (e) {
    if (e instanceof CalcError) throw e;
    if (e instanceof RangeError) throw new CalcError("SYNTAX", "Expression too complex");
    throw e;
  }
}

/** Non-throwing wrapper: { ok: true, value } or { ok: false, error }. */
export function tryEvaluate(tokens, opts) {
  try {
    return { ok: true, value: evaluate(tokens, opts) };
  } catch (e) {
    if (e instanceof CalcError) return { ok: false, error: e };
    throw e;
  }
}

// ------------------------------------------------------------ formatting ---

/** Display a number with up to 15 significant digits, e.g. "0.3", "1E+21". */
export function format(v) {
  if (!Number.isFinite(v)) return "Error";
  if (v === 0) return "0";
  let s = v.toPrecision(15);
  if (s.includes("e")) {
    let [m, ex] = s.split("e");
    if (m.includes(".")) m = m.replace(/\.?0+$/, "");
    return m + "E" + (ex[0] === "-" ? ex : "+" + ex.replace(/^\+/, ""));
  }
  if (s.includes(".")) s = s.replace(/\.?0+$/, "");
  return s;
}

/** Full-precision token for a computed value (so 1/3*3 stays exactly 1). */
export function numberToToken(v) {
  return v === 0 ? "0" : String(v).replace("e", "E");
}

const GLYPH = { "*": "×", "/": "÷", "-": "−", root: "ʸ√" };

/** Human-readable form of a token list. */
export function toDisplay(tokens) {
  let out = "";
  tokens.forEach((t, i) => {
    const k = kind(t);
    if (k === "num" || isNumberToken(t)) {
      out += (t.length > 16 && k === "num" ? format(Number(t)) : t).replace(/^-/, "−");
      return;
    }
    if (k === "bin") {
      const pk = i === 0 ? null : kind(tokens[i - 1]);
      const unary = pk === null || pk === "bin" || pk === "open" || pk === "func";
      const g = GLYPH[t] || t;
      out += unary || t === "^" ? g : ` ${g} `;
      return;
    }
    out += t;
  });
  return out.trimEnd();
}
