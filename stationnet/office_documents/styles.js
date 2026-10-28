import { Extension, getStyleProperty } from '@tiptap/core';
import TextAlign from '@tiptap/extension-text-align';

// Shared by the editor toolbar, the sanitizer, and the exporters.
export const FONTS = [
  { label: 'Default font', value: '' },
  { label: 'Arial', value: 'Arial, Helvetica, sans-serif', group: 'Sans-serif' },
  { label: 'Verdana', value: 'Verdana, Geneva, sans-serif', group: 'Sans-serif' },
  { label: 'Trebuchet MS', value: '"Trebuchet MS", Helvetica, sans-serif', group: 'Sans-serif' },
  { label: 'Georgia', value: 'Georgia, serif', group: 'Serif' },
  { label: 'Times New Roman', value: '"Times New Roman", Times, serif', group: 'Serif' },
  { label: 'Palatino', value: '"Palatino Linotype", Palatino, "Book Antiqua", serif', group: 'Serif' },
  { label: 'Courier New', value: '"Courier New", Courier, monospace', group: 'Monospace' },
  { label: 'Consolas', value: 'Consolas, "Lucida Console", monospace', group: 'Monospace' },
];
export const FONT_SIZES = ['', '8pt', '9pt', '10pt', '11pt', '12pt', '14pt', '16pt', '18pt', '20pt', '24pt', '28pt', '36pt', '48pt'];
export const LINE_HEIGHTS = [{ label: 'Single', value: '' }, { label: '1.15', value: '1.15' }, { label: '1.5', value: '1.5' }, { label: 'Double', value: '2' }];
export const BLOCK_STYLES = {
  title: { className: 'doc-title', css: 'font-size: 26pt; font-weight: bold; line-height: 1.15' },
  subtitle: { className: 'doc-subtitle', css: 'font-size: 15pt; color: #555555' },
};
export const BLOCK_CLASSES = new Set(Object.values(BLOCK_STYLES).map(style => style.className));
const MAX_INDENT = 8;

// StationNet's CSP blocks inline styles in parsed markup, which leaves element.style empty,
// so formatting is always read from the style attribute text instead.
export const cssProperty = (element, property) => getStyleProperty(element, property) || '';

const firstFamily = value => String(value || '').split(',')[0].trim().replace(/^["']|["']$/g, '');
export const fontName = firstFamily;

// Turns a bare family name (from ODT, for example) into a known stack when possible.
export function fontStack(name) {
  const family = firstFamily(name);
  const known = FONTS.find(font => font.value && firstFamily(font.value).toLowerCase() === family.toLowerCase());
  if (known) return known.value;
  return /\s/.test(family) ? `"${family}"` : family;
}

export function fontValue(fontFamily) {
  if (!fontFamily) return '';
  return FONTS.find(font => font.value && firstFamily(font.value).toLowerCase() === firstFamily(fontFamily).toLowerCase())?.value ?? null;
}

const number = (value, min, max) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= min && parsed <= max;
};
const declarations = {
  'text-align': value => /^(left|right|center|justify)$/i.test(value) ? value.toLowerCase() : null,
  'font-family': value => value.length <= 200 && /^[\w\s,"'-]+$/.test(value) ? value : null,
  'font-size': value => {
    const match = /^(\d{1,3}(?:\.\d+)?)(pt|px|em|rem|%)$/i.exec(value);
    return match && number(match[1], 1, 200) ? `${match[1]}${match[2].toLowerCase()}` : null;
  },
  'line-height': value => number(value, 1, 3) && /^\d(\.\d+)?$/.test(value) ? value : null,
  'margin-left': value => {
    const match = /^(\d{1,2}(?:\.\d+)?)em$/i.exec(value);
    return match && number(match[1], 0, MAX_INDENT * 2) ? `${match[1]}em` : null;
  },
};

// Keeps only the formatting declarations the editor understands; returns null when nothing is left.
export function sanitizeStyle(value) {
  const kept = [];
  for (const part of String(value || '').split(';')) {
    const index = part.indexOf(':');
    if (index < 0) continue;
    const property = part.slice(0, index).trim().toLowerCase();
    const clean = declarations[property]?.(part.slice(index + 1).trim());
    if (clean) kept.push(`${property}: ${clean}`);
  }
  return kept.length ? kept.join('; ') : null;
}

function changeIndent(delta) {
  return ({ tr, state, dispatch }) => {
    let changed = false;
    state.doc.nodesBetween(state.selection.from, state.selection.to, (node, pos) => {
      if (node.type.name !== 'paragraph' && node.type.name !== 'heading') return true;
      const indent = Math.max(0, Math.min(MAX_INDENT, (node.attrs.indent || 0) + delta));
      if (indent !== (node.attrs.indent || 0)) {
        if (dispatch) tr.setNodeMarkup(pos, undefined, { ...node.attrs, indent });
        changed = true;
      }
      return false;
    });
    return changed;
  };
}

// Paragraph-level formatting: Title/Subtitle styles, indentation, and line spacing.
export const ParagraphFormat = Extension.create({
  name: 'paragraphFormat',
  addGlobalAttributes() {
    return [
      {
        types: ['paragraph'],
        attributes: {
          blockStyle: {
            default: null,
            keepOnSplit: false,
            parseHTML: element => Object.keys(BLOCK_STYLES).find(name => element.classList.contains(BLOCK_STYLES[name].className)) ?? null,
            renderHTML: attributes => BLOCK_STYLES[attributes.blockStyle] ? { class: BLOCK_STYLES[attributes.blockStyle].className } : {},
          },
        },
      },
      {
        types: ['paragraph', 'heading'],
        attributes: {
          indent: {
            default: 0,
            parseHTML: element => Math.max(0, Math.min(MAX_INDENT, Math.round((parseFloat(cssProperty(element, 'margin-left')) || 0) / 2))),
            renderHTML: attributes => attributes.indent ? { style: `margin-left: ${attributes.indent * 2}em` } : {},
          },
          lineHeight: {
            default: null,
            parseHTML: element => cssProperty(element, 'line-height') || null,
            renderHTML: attributes => attributes.lineHeight ? { style: `line-height: ${attributes.lineHeight}` } : {},
          },
        },
      },
    ];
  },
  addCommands() {
    return {
      indent: () => props => props.editor.isActive('listItem') ? props.commands.sinkListItem('listItem') : changeIndent(1)(props),
      outdent: () => props => props.editor.isActive('listItem') ? props.commands.liftListItem('listItem') : changeIndent(-1)(props),
      setLineHeight: lineHeight => ({ chain }) => chain().updateAttributes('paragraph', { lineHeight: lineHeight || null }).updateAttributes('heading', { lineHeight: lineHeight || null }).run(),
    };
  },
});

export const DocumentTextAlign = TextAlign.extend({
  addGlobalAttributes() {
    const { types, alignments, defaultAlignment } = this.options;
    return [{
      types,
      attributes: {
        textAlign: {
          default: defaultAlignment,
          parseHTML: element => {
            const alignment = cssProperty(element, 'text-align').toLowerCase();
            return alignments.includes(alignment) ? alignment : defaultAlignment;
          },
          renderHTML: attributes => alignments.includes(attributes.textAlign) ? { style: `text-align: ${attributes.textAlign}` } : {},
        },
      },
    }];
  },
});
