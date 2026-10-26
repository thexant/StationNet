/* Calculator V2 — safe expression evaluator. No eval/Function/DOM needed. */
export function formatNumber(value) {
  if (!Number.isFinite(value)) throw Error("Result is outside numeric range");
  const rounded = Number(value.toPrecision(13));
  return Object.is(rounded, -0) ? "0" : String(rounded);
}

export function calculate(source, angle = "DEG", ans = 0) {
  if (typeof source !== "string" || source.length > 180) throw Error("Expression too long");
  if (!source.trim()) throw Error("Enter an expression");
  const tokens = [];
  let i = 0;
  while (i < source.length) {
    if (/\s/.test(source[i])) { i++; continue; }
    const rest = source.slice(i);
    const number = rest.match(/^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?/);
    if (number) {
      tokens.push({ type: "number", value: Number(number[0]) });
      i += number[0].length;
      continue;
    }
    const word = rest.match(/^(?:asin|acos|atan|sqrt|sin|cos|tan|abs|exp|log|ln|Ans|pi)\b/);
    if (word) {
      tokens.push({ type: word[0] === "pi" ? "π" : word[0] });
      i += word[0].length;
      continue;
    }
    const char = source[i];
    if ("+-×÷*/^()%!πe−".includes(char)) {
      tokens.push({ type: char === "*" ? "×" : char === "/" ? "÷" : char === "−" ? "-" : char });
      i++;
      continue;
    }
    throw Error(`Invalid character: ${char}`);
  }
  let at = 0;
  const peek = () => tokens[at]?.type;
  const take = () => tokens[at++];
  const finite = v => {
    if (!Number.isFinite(v)) throw Error("Undefined or overflowing result");
    return v;
  };
  const item = (value, percent = false) => ({ value: finite(value), percent });
  const functions = new Set(["sin", "cos", "tan", "asin", "acos", "atan", "sqrt", "ln", "log", "exp", "abs"]);
  const toRad = n => angle === "DEG" ? n * Math.PI / 180 : n;
  const fromRad = n => angle === "DEG" ? n * 180 / Math.PI : n;
  const apply = (name, value) => {
    if (name === "sin") return Math.sin(toRad(value));
    if (name === "cos") return Math.cos(toRad(value));
    if (name === "tan") {
      const r = toRad(value);
      if (Math.abs(Math.cos(r)) < 1e-12) throw Error("Tangent is undefined at this angle");
      return Math.tan(r);
    }
    if (name === "asin") return fromRad(Math.asin(value));
    if (name === "acos") return fromRad(Math.acos(value));
    if (name === "atan") return fromRad(Math.atan(value));
    if (name === "sqrt") return Math.sqrt(value);
    if (name === "ln") return Math.log(value);
    if (name === "log") return Math.log10(value);
    if (name === "exp") return Math.exp(value);
    if (name === "abs") return Math.abs(value);
    throw Error("Unknown function");
  };
  const factorial = value => {
    if (!Number.isInteger(value) || value < 0 || value > 170)
      throw Error("Factorial requires an integer from 0 to 170");
    let out = 1;
    for (let k = 2; k <= value; k++) out *= k;
    return out;
  };
  function expr(minPower = 0) {
    let left;
    const token = take();
    if (!token) throw Error("Incomplete expression");
    if (token.type === "number") left = item(token.value);
    else if (token.type === "π") left = item(Math.PI);
    else if (token.type === "e") left = item(Math.E);
    else if (token.type === "Ans") left = item(ans);
    else if (token.type === "(") {
      left = expr();
      if (take()?.type !== ")") throw Error("Missing closing parenthesis");
    } else if (token.type === "+" || token.type === "-") {
      const operand = expr(30); // exponent binds tighter than unary sign
      left = item(token.type === "-" ? -operand.value : operand.value);
    } else if (functions.has(token.type)) {
      if (take()?.type !== "(") throw Error(`Expected '(' after ${token.type}`);
      const argument = expr();
      if (take()?.type !== ")") throw Error("Missing closing parenthesis");
      left = item(apply(token.type, argument.value));
    } else throw Error(`Unexpected ${token.type}`);

    for (;;) {
      const next = peek();
      if ((next === "!" || next === "%") && 60 >= minPower) {
        take();
        left = next === "!" ? item(factorial(left.value)) : item(left.value / 100, true);
        continue;
      }
      const powers = { "+": 10, "-": 10, "×": 20, "÷": 20, "^": 40 };
      const bp = powers[next];
      if (bp === undefined || bp < minPower) break;
      take();
      const right = expr(next === "^" ? bp : bp + 1);
      if (next === "+") left = item(left.value + (right.percent ? left.value * right.value : right.value));
      if (next === "-") left = item(left.value - (right.percent ? left.value * right.value : right.value));
      if (next === "×") left = item(left.value * right.value);
      if (next === "÷") {
        if (right.value === 0) throw Error("Division by zero");
        left = item(left.value / right.value);
      }
      if (next === "^") left = item(Math.pow(left.value, right.value));
    }
    return left;
  }
  const answer = expr().value;
  if (at !== tokens.length) throw Error(`Unexpected ${peek()}`);
  return finite(answer);
}
