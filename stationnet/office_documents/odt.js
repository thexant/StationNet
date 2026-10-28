import JSZip from 'jszip';
import { cssProperty, fontName, fontStack } from './styles.js';

const esc = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const local = node => node.localName;
const children = (node, styles) => [...node.childNodes].map(child => renderNode(child, styles)).join('');
const round = value => Math.round(value * 100) / 100;
const alignIn = { start: 'left', left: 'left', center: 'center', end: 'right', right: 'right', justify: 'justify' };
const alignOut = { left: 'start', center: 'center', right: 'end', justify: 'justify' };
const blockClasses = { Title: 'doc-title', Subtitle: 'doc-subtitle' };
const quoteStyles = new Set(['Quotations', 'Quote']);
const INDENT_PT = 24; // One editor indent step (2em) at 12pt.

function toPt(value) {
  const match = /^(-?\d+(?:\.\d+)?)(pt|px|in|cm|mm|em|rem|%)?$/.exec(String(value || '').trim());
  if (!match) return null;
  return Number(match[1]) * { pt: 1, px: 0.75, in: 72, cm: 72 / 2.54, mm: 72 / 25.4, em: 12, rem: 12, '%': 0.12 }[match[2] || 'pt'];
}
const cssText = css => Object.entries(css).map(([property, value]) => `${property}: ${value}`).join('; ');
const attrs = (className, css) => `${className ? ` class="${className}"` : ''}${Object.keys(css).length ? ` style="${esc(cssText(css))}"` : ''}`;

function readStyle(node) {
  const style = { parent: node.getAttribute('style:parent-style-name'), tags: [], text: {}, block: {} };
  for (const props of node.children) {
    if (props.localName === 'text-properties') {
      if (props.getAttribute('fo:font-weight') === 'bold') style.tags.push('strong');
      if (props.getAttribute('fo:font-style') === 'italic') style.tags.push('em');
      if (props.getAttribute('style:text-underline-style') === 'solid') style.tags.push('u');
      if (props.getAttribute('style:text-line-through-style') === 'solid') style.tags.push('s');
      const family = props.getAttribute('fo:font-family') || props.getAttribute('style:font-name');
      if (family) style.text['font-family'] = fontStack(family);
      const size = toPt(props.getAttribute('fo:font-size'));
      if (size > 0) style.text['font-size'] = `${round(size)}pt`;
    } else if (props.localName === 'paragraph-properties') {
      const align = alignIn[props.getAttribute('fo:text-align')];
      if (align) style.block['text-align'] = align;
      const margin = toPt(props.getAttribute('fo:margin-left'));
      if (margin > 0) style.block['margin-left'] = `${Math.min(8, Math.round(margin / INDENT_PT)) * 2}em`;
      const lineHeight = /^(\d+(?:\.\d+)?)%$/.exec(props.getAttribute('fo:line-height') || '');
      if (lineHeight) style.block['line-height'] = String(round(lineHeight[1] / 100));
    }
  }
  return style;
}

// Follows parent styles so automatic styles (P1, T1) inherit named ones like Title.
function resolveStyle(styles, name) {
  const resolved = { tags: [], text: {}, block: {}, className: '', quote: false };
  const chain = [];
  for (let current = name; current && styles[current] && chain.length < 8 && !chain.includes(current); current = styles[current].parent) chain.push(current);
  for (const current of chain.reverse()) {
    const style = styles[current];
    resolved.tags = style.tags.length ? style.tags : resolved.tags;
    Object.assign(resolved.text, style.text);
    const { 'margin-left': margin, ...block } = style.block;
    Object.assign(resolved.block, quoteStyles.has(current) ? block : style.block);
    if (blockClasses[current]) resolved.className = blockClasses[current];
    if (quoteStyles.has(current)) resolved.quote = true;
  }
  if (blockClasses[name]) resolved.className = blockClasses[name];
  if (quoteStyles.has(name)) resolved.quote = true;
  return resolved;
}

function renderNode(node, styles) {
  if (node.nodeType === Node.TEXT_NODE) return esc(node.nodeValue);
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const name = local(node);
  if (name === 'h') {
    const level = Math.min(3, Math.max(1, Number(node.getAttribute('text:outline-level')) || 1));
    const style = resolveStyle(styles.styles, node.getAttribute('text:style-name'));
    return `<h${level}${attrs('', style.block)}>${children(node, styles)}</h${level}>`;
  }
  if (name === 'p') {
    const style = resolveStyle(styles.styles, node.getAttribute('text:style-name'));
    const html = `<p${attrs(style.className, style.block)}>${children(node, styles)}</p>`;
    return style.quote ? `<blockquote>${html}</blockquote>` : html;
  }
  if (name === 'span') {
    let html = children(node, styles);
    const style = resolveStyle(styles.styles, node.getAttribute('text:style-name'));
    if (Object.keys(style.text).length) html = `<span${attrs('', style.text)}>${html}</span>`;
    for (const tag of style.tags) html = `<${tag}>${html}</${tag}>`;
    return html;
  }
  if (name === 'list') {
    const tag = styles.lists[node.getAttribute('text:style-name')] === 'ordered' ? 'ol' : 'ul';
    return `<${tag}>${children(node, styles)}</${tag}>`;
  }
  if (name === 'list-item') return `<li>${children(node, styles)}</li>`;
  if (name === 'table') return `<table>${children(node, styles)}</table>`;
  if (name === 'table-row') return `<tr>${children(node, styles)}</tr>`;
  if (name === 'table-cell') return `<td>${children(node, styles)}</td>`;
  if (name === 'a') return `<a href="${esc(node.getAttribute('xlink:href') || '')}">${children(node, styles)}</a>`;
  if (name === 's') return ' '.repeat(Math.min(100, Number(node.getAttribute('text:c')) || 1));
  if (name === 'tab') return '\t';
  if (name === 'line-break') return '<br>';
  if (name === 'image') return `<img src="${esc(node.getAttribute('xlink:href') || '')}" alt="">`;
  return children(node, styles);
}

export async function importOdt(buffer) {
  const zip = await JSZip.loadAsync(buffer);
  const entry = zip.file('content.xml');
  if (!entry) throw new Error('The ODT file has no document content.');
  const source = await entry.async('string');
  if (source.length > 20_000_000) throw new Error('The ODT document is too large.');
  const xml = new DOMParser().parseFromString(source, 'application/xml');
  if (xml.querySelector('parsererror')) throw new Error('The ODT document is invalid.');
  const body = [...xml.getElementsByTagName('*')].find(node => node.localName === 'text' && node.parentNode?.localName === 'body');
  if (!body) throw new Error('The ODT document has no text body.');
  const styles = { styles: {}, lists: {} };
  const styleSources = [];
  if (zip.file('styles.xml')) {
    const styleXml = new DOMParser().parseFromString(await zip.file('styles.xml').async('string'), 'application/xml');
    styleSources.push(styleXml);
  }
  styleSources.push(xml); // Automatic styles in content.xml override shared ones.
  for (const styleSource of styleSources) {
    for (const node of styleSource.getElementsByTagName('*')) {
      if (node.localName === 'list-style') {
        styles.lists[node.getAttribute('style:name')] = [...node.children].some(child => child.localName === 'list-level-style-number') ? 'ordered' : 'unordered';
      } else if (node.localName === 'style') {
        styles.styles[node.getAttribute('style:name')] = readStyle(node);
      }
    }
  }
  let html = children(body, styles);
  for (const image of html.matchAll(/src="(Pictures\/[^"<>]+)"/g)) {
    const picture = zip.file(image[1]);
    if (!picture) continue;
    const bytes = await picture.async('base64');
    const mime = /\.png$/i.test(image[1]) ? 'image/png' : /\.gif$/i.test(image[1]) ? 'image/gif' : 'image/jpeg';
    html = html.replaceAll(`src="${image[1]}"`, `src="data:${mime};base64,${bytes}"`);
  }
  return html;
}

function autoStyle(context, family, properties, parent) {
  const key = `${family}|${parent || ''}|${properties}`;
  if (!context.auto.has(key)) {
    const name = `${family === 'paragraph' ? 'P' : 'T'}${context.auto.size + 1}`;
    context.auto.set(key, { name, xml: `<style:style style:name="${name}" style:family="${family}"${parent ? ` style:parent-style-name="${parent}"` : ''}>${properties}</style:style>` });
  }
  return context.auto.get(key).name;
}

function paragraphStyle(node, context, base) {
  const props = [];
  const align = alignOut[cssProperty(node, 'text-align')];
  if (align) props.push(`fo:text-align="${align}"`);
  const margin = toPt(cssProperty(node, 'margin-left'));
  if (margin > 0) props.push(`fo:margin-left="${round(margin)}pt"`);
  const lineHeight = Number(cssProperty(node, 'line-height'));
  if (lineHeight > 0) props.push(`fo:line-height="${Math.round(lineHeight * 100)}%"`);
  if (!props.length) return base;
  return autoStyle(context, 'paragraph', `<style:paragraph-properties ${props.join(' ')}/>`, base);
}

function textStyle(node, context) {
  const props = [];
  const family = fontName(cssProperty(node, 'font-family'));
  if (family) props.push(`fo:font-family="${esc(family)}"`);
  const size = toPt(cssProperty(node, 'font-size'));
  if (size > 0) props.push(`fo:font-size="${round(size)}pt"`);
  return props.length ? autoStyle(context, 'text', `<style:text-properties ${props.join(' ')}/>`) : null;
}

const styleName = name => name ? ` text:style-name="${name}"` : '';

function toOdt(node, context) {
  if (node.nodeType === Node.TEXT_NODE) return esc(node.nodeValue);
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const name = node.localName.toLowerCase();
  if (name === 'blockquote') context.quote++;
  const content = [...node.childNodes].map(child => toOdt(child, context)).join('');
  if (name === 'blockquote') {
    context.quote--;
    return [...node.childNodes].some(child => child.nodeType === Node.ELEMENT_NODE) ? content : `<text:p text:style-name="Quotations">${content}</text:p>`;
  }
  if (/^h[1-6]$/.test(name)) return `<text:h text:outline-level="${name[1]}"${styleName(paragraphStyle(node, context))}>${content}</text:h>`;
  if (name === 'p' || name === 'div') {
    const base = node.classList.contains('doc-title') ? 'Title' : node.classList.contains('doc-subtitle') ? 'Subtitle' : context.quote ? 'Quotations' : null;
    return `<text:p${styleName(paragraphStyle(node, context, base))}>${content}</text:p>`;
  }
  if (name === 'br') return '<text:line-break/>';
  if (name === 'ul' || name === 'ol') return `<text:list text:style-name="${name === 'ol' ? 'Numbered' : 'Bulleted'}">${content}</text:list>`;
  if (name === 'li') return `<text:list-item>${content.includes('<text:p') ? content : `<text:p>${content}</text:p>`}</text:list-item>`;
  if (name === 'table') {
    context.tables++;
    return `<table:table table:name="Table${context.tables}">${content}</table:table>`;
  }
  if (name === 'tr') return `<table:table-row>${content}</table:table-row>`;
  if (name === 'td' || name === 'th') return `<table:table-cell office:value-type="string">${content || '<text:p/>'}</table:table-cell>`;
  if (name === 'a') return `<text:a xlink:href="${esc(node.getAttribute('href') || '')}" xlink:type="simple">${content}</text:a>`;
  if (name === 'img') {
    const src = node.getAttribute('src') || '';
    const match = /^data:image\/(png|jpeg|gif|webp);base64,([a-z0-9+/=]+)$/i.exec(src);
    if (!match) return '';
    const pictures = context.pictures;
    const extension = match[1] === 'jpeg' ? 'jpg' : match[1];
    const path = `Pictures/image${pictures.length + 1}.${extension}`;
    pictures.push({ path, data: match[2], mime: `image/${match[1]}` });
    return `<draw:frame draw:name="Image${pictures.length}" text:anchor-type="as-char" svg:width="10cm" svg:height="7cm"><draw:image xlink:href="${path}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/></draw:frame>`;
  }
  if (name === 'span') {
    const style = textStyle(node, context);
    return style ? `<text:span text:style-name="${style}">${content}</text:span>` : content;
  }
  const style = { strong: 'Bold', b: 'Bold', em: 'Italic', i: 'Italic', u: 'Underline', s: 'Strike', strike: 'Strike', code: 'Code' }[name];
  if (style) return `<text:span text:style-name="${style}">${content}</text:span>`;
  if (name === 'pre') return `<text:p text:style-name="Code">${esc(node.textContent)}</text:p>`;
  if (name === 'hr') return '<text:p>────────────────────</text:p>';
  return content;
}

export async function exportOdt(html) {
  const zip = new JSZip();
  zip.file('mimetype', 'application/vnd.oasis.opendocument.text', { compression: 'STORE' });
  const holder = document.createElement('div');
  holder.innerHTML = html;
  const context = { pictures: [], tables: 0, quote: 0, auto: new Map() };
  const pictures = context.pictures;
  const content = [...holder.childNodes].map(node => toOdt(node, context)).join('');
  const automatic = [...context.auto.values()].map(style => style.xml).join('');
  const xmlns = 'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"';
  zip.file('content.xml', `<?xml version="1.0" encoding="UTF-8"?><office:document-content ${xmlns} office:version="1.2"><office:automatic-styles>${automatic}</office:automatic-styles><office:body><office:text>${content}</office:text></office:body></office:document-content>`);
  zip.file('styles.xml', `<?xml version="1.0" encoding="UTF-8"?><office:document-styles ${xmlns} office:version="1.2"><office:styles><style:style style:name="Title" style:family="paragraph" style:class="chapter"><style:paragraph-properties fo:margin-bottom="0.1in"/><style:text-properties fo:font-size="26pt" fo:font-weight="bold"/></style:style><style:style style:name="Subtitle" style:family="paragraph" style:class="chapter"><style:paragraph-properties fo:margin-bottom="0.15in"/><style:text-properties fo:font-size="15pt" fo:color="#555555"/></style:style><style:style style:name="Quotations" style:family="paragraph"><style:paragraph-properties fo:margin-left="0.4in" fo:margin-right="0.4in"/><style:text-properties fo:font-style="italic"/></style:style><style:style style:name="Bold" style:family="text"><style:text-properties fo:font-weight="bold"/></style:style><style:style style:name="Italic" style:family="text"><style:text-properties fo:font-style="italic"/></style:style><style:style style:name="Underline" style:family="text"><style:text-properties style:text-underline-style="solid"/></style:style><style:style style:name="Strike" style:family="text"><style:text-properties style:text-line-through-style="solid"/></style:style><text:list-style style:name="Bulleted"><text:list-level-style-bullet text:level="1" text:bullet-char="•"/></text:list-style><text:list-style style:name="Numbered"><text:list-level-style-number text:level="1" style:num-format="1"/></text:list-style></office:styles></office:document-styles>`);
  zip.file('meta.xml', '<?xml version="1.0" encoding="UTF-8"?><office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" office:version="1.2"><office:meta/></office:document-meta>');
  let manifest = '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>';
  for (const file of ['content.xml','styles.xml','meta.xml']) manifest += `<manifest:file-entry manifest:full-path="${file}" manifest:media-type="text/xml"/>`;
  for (const picture of pictures) {
    zip.file(picture.path, picture.data, { base64: true });
    manifest += `<manifest:file-entry manifest:full-path="${picture.path}" manifest:media-type="${picture.mime}"/>`;
  }
  zip.file('META-INF/manifest.xml', `${manifest}</manifest:manifest>`);
  return zip.generateAsync({ type: 'blob', mimeType: 'application/vnd.oasis.opendocument.text', compression: 'DEFLATE' });
}
