export const MIN_WIDTH = 4;
export const MIN_HEIGHT = 4;
export const MAX_WIDTH = 80;
export const MAX_HEIGHT = 50;

export const blankCell = () => ({ char: " ", fg: 7, bg: 0 });
export const blankCells = (width, height) => Array.from({ length: height }, () => Array.from({ length: width }, blankCell));
export const cloneCells = (cells) => cells.map(row => row.map(cell => ({ ...cell })));
export const sameCell = (a, b) => a.char === b.char && a.fg === b.fg && a.bg === b.bg;
export const oneCharacter = (value) => Array.from(String(value || " "))[0] || " ";

function printable(character) {
  return character !== "\u001b" && !/[\u0000-\u001f\u007f-\u009f]/u.test(character);
}

export function validateProject(data) {
  if (!data || data.format !== 1 || !Number.isInteger(data.width) || !Number.isInteger(data.height)
      || data.width < MIN_WIDTH || data.width > MAX_WIDTH || data.height < MIN_HEIGHT || data.height > MAX_HEIGHT
      || typeof data.name !== "string" || data.name.length > 60
      || !Array.isArray(data.chars) || !Array.isArray(data.fg) || !Array.isArray(data.bg)
      || data.chars.length !== data.height || data.fg.length !== data.height || data.bg.length !== data.height) {
    throw new Error("Project has an invalid size or structure");
  }
  const cells = blankCells(data.width, data.height);
  for (let y = 0; y < data.height; y += 1) {
    if (typeof data.chars[y] !== "string") throw new Error(`Project row ${y + 1} is invalid`);
    const characters = Array.from(data.chars[y]);
    if (characters.length !== data.width || !Array.isArray(data.fg[y]) || !Array.isArray(data.bg[y])
        || data.fg[y].length !== data.width || data.bg[y].length !== data.width) {
      throw new Error(`Project row ${y + 1} is incomplete`);
    }
    for (let x = 0; x < data.width; x += 1) {
      const fg = data.fg[y][x], bg = data.bg[y][x], char = characters[x];
      if (!printable(char) || !Number.isInteger(fg) || fg < 0 || fg > 15
          || !Number.isInteger(bg) || bg < 0 || bg > 15) {
        throw new Error(`Project cell ${x + 1},${y + 1} is invalid`);
      }
      cells[y][x] = { char, fg, bg };
    }
  }
  return { name: data.name, width: data.width, height: data.height, cells };
}

export function projectPayload(name, cells) {
  return {
    format: 1, name, width: cells[0].length, height: cells.length,
    chars: cells.map(row => row.map(cell => cell.char).join("")),
    fg: cells.map(row => row.map(cell => cell.fg)),
    bg: cells.map(row => row.map(cell => cell.bg))
  };
}

function canvasFromRows(rows) {
  const width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, rows.reduce((largest, row) => Math.max(largest, row.length), 0)));
  const height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, rows.length));
  const cells = blankCells(width, height);
  for (let y = 0; y < Math.min(rows.length, height); y += 1) {
    for (let x = 0; x < Math.min(rows[y].length, width); x += 1) cells[y][x] = rows[y][x];
  }
  return { width, height, cells, clipped: rows.length > MAX_HEIGHT || rows.some(row => row.length > MAX_WIDTH) };
}

export function parseArt(raw, kind, fg = 7, bg = 0) {
  const text = String(raw).replace(/\r\n?/g, "\n");
  if (!text) throw new Error("The artwork is empty");
  if (Array.from(text).length > 120000) throw new Error("Artwork exceeds the 120,000-character import limit");
  const rows = [[]];
  let baseFg = fg % 8, explicitBright = fg >= 8, bold = false, background = bg;
  const put = char => rows[rows.length - 1].push({ char, fg: baseFg + (explicitBright || bold ? 8 : 0), bg: background });
  const newline = () => rows.push([]);
  const chars = Array.from(text);
  for (let i = 0; i < chars.length; i += 1) {
    const char = chars[i];
    if (char === "\n") { newline(); continue; }
    if (char === "\t") { do { put(" "); } while (rows.at(-1).length % 4); continue; }
    if (char === "\u001b") {
      if (kind !== "ansi" || chars[++i] !== "[") throw new Error("Unsupported ANSI escape sequence");
      let body = "";
      while (i + 1 < chars.length && /[0-9;]/.test(chars[i + 1]) && body.length < 80) body += chars[++i];
      if (chars[++i] !== "m") throw new Error("Unsupported ANSI cursor or control sequence");
      for (const part of (body || "0").split(";")) {
        const code = Number(part || 0);
        if (code === 0) { baseFg = 7; explicitBright = false; bold = false; background = 0; }
        else if (code === 1) bold = true;
        else if (code === 22) bold = false;
        else if (code === 39) { baseFg = 7; explicitBright = false; }
        else if (code === 49) background = 0;
        else if (code >= 30 && code <= 37) { baseFg = code - 30; explicitBright = false; }
        else if (code >= 90 && code <= 97) { baseFg = code - 90; explicitBright = true; }
        else if (code >= 40 && code <= 47) background = code - 40;
        else if (code >= 100 && code <= 107) background = code - 100 + 8;
        else throw new Error(`Unsupported ANSI SGR code ${code}`);
      }
      continue;
    }
    if (!printable(char)) throw new Error("Artwork contains an unsupported control character");
    put(char);
  }
  if (rows.length > 1 && !rows.at(-1).length) rows.pop();
  return canvasFromRows(rows);
}

export const plainText = cells => cells.map(row => row.map(cell => cell.char).join("")).join("\n");
const ansiCode = cell => `\u001b[${cell.fg < 8 ? 30 + cell.fg : 90 + cell.fg - 8};${cell.bg < 8 ? 40 + cell.bg : 100 + cell.bg - 8}m`;
export function ansiText(cells) {
  return cells.map(row => {
    let output = "", previous = "";
    for (const cell of row) {
      const code = ansiCode(cell);
      if (code !== previous) { output += code; previous = code; }
      output += cell.char;
    }
    return `${output}\u001b[0m`;
  }).join("\n");
}

export function drawLine(cells, x0, y0, x1, y1, value) {
  let changed = false;
  const dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
  const dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
  let error = dx + dy;
  while (true) {
    if (!sameCell(cells[y0][x0], value)) { cells[y0][x0] = { ...value }; changed = true; }
    if (x0 === x1 && y0 === y1) break;
    const twice = 2 * error;
    if (twice >= dy) { error += dy; x0 += sx; }
    if (twice <= dx) { error += dx; y0 += sy; }
  }
  return changed;
}

export function drawRectangle(cells, x0, y0, x1, y1, value) {
  let changed = false;
  const left = Math.min(x0, x1), right = Math.max(x0, x1);
  const top = Math.min(y0, y1), bottom = Math.max(y0, y1);
  for (let y = top; y <= bottom; y += 1) for (let x = left; x <= right; x += 1) {
    if (x !== left && x !== right && y !== top && y !== bottom) continue;
    if (!sameCell(cells[y][x], value)) { cells[y][x] = { ...value }; changed = true; }
  }
  return changed;
}

export function floodFill(cells, startX, startY, replacement) {
  const target = cells[startY][startX];
  if (sameCell(target, replacement)) return false;
  const width = cells[0].length, height = cells.length, queue = [[startX, startY]];
  while (queue.length) {
    const [x, y] = queue.pop();
    if (x < 0 || y < 0 || x >= width || y >= height || !sameCell(cells[y][x], target)) continue;
    cells[y][x] = { ...replacement };
    queue.push([x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]);
  }
  return true;
}
