import JSZip from 'jszip';

const esc = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const local = node => node.localName;
const children = (node, styles) => [...node.childNodes].map(child => renderNode(child, styles)).join('');

function renderNode(node, styles) {
  if (node.nodeType === Node.TEXT_NODE) return esc(node.nodeValue);
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const name = local(node);
  if (name === 'h') return `<h${Math.min(3, Math.max(1, Number(node.getAttribute('text:outline-level')) || 1))}>${children(node, styles)}</h${Math.min(3, Math.max(1, Number(node.getAttribute('text:outline-level')) || 1))}>`;
  if (name === 'p') return `<p>${children(node, styles)}</p>`;
  if (name === 'span') {
    let html = children(node, styles);
    const tags = styles[node.getAttribute('text:style-name')];
    for (const tag of Array.isArray(tags) ? tags : []) html = `<${tag}>${html}</${tag}>`;
    return html;
  }
  if (name === 'list') {
    const tag = styles[node.getAttribute('text:style-name')] === 'ordered' ? 'ol' : 'ul';
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
  const styles = {};
  const styleSources = [xml];
  if (zip.file('styles.xml')) {
    const styleXml = new DOMParser().parseFromString(await zip.file('styles.xml').async('string'), 'application/xml');
    styleSources.push(styleXml);
  }
  for (const styleSource of styleSources) {
    for (const node of styleSource.getElementsByTagName('*')) {
      if (node.localName === 'list-style') {
        styles[node.getAttribute('style:name')] = [...node.children].some(child => child.localName === 'list-level-style-number') ? 'ordered' : 'unordered';
        continue;
      }
      if (node.localName !== 'style') continue;
      const props = [...node.children].find(child => child.localName === 'text-properties');
      if (!props) continue;
      const tags = [];
      if (props.getAttribute('fo:font-weight') === 'bold') tags.push('strong');
      if (props.getAttribute('fo:font-style') === 'italic') tags.push('em');
      if (props.getAttribute('style:text-underline-style') === 'solid') tags.push('u');
      if (props.getAttribute('style:text-line-through-style') === 'solid') tags.push('s');
      styles[node.getAttribute('style:name')] = tags;
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

function toOdt(node, pictures) {
  if (node.nodeType === Node.TEXT_NODE) return esc(node.nodeValue);
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const name = node.localName.toLowerCase();
  const content = [...node.childNodes].map(child => toOdt(child, pictures)).join('');
  if (/^h[1-6]$/.test(name)) return `<text:h text:outline-level="${name[1]}">${content}</text:h>`;
  if (name === 'p' || name === 'div') return `<text:p>${content}</text:p>`;
  if (name === 'br') return '<text:line-break/>';
  if (name === 'ul' || name === 'ol') return `<text:list text:style-name="${name === 'ol' ? 'Numbered' : 'Bulleted'}">${content}</text:list>`;
  if (name === 'li') return `<text:list-item>${content.includes('<text:p') ? content : `<text:p>${content}</text:p>`}</text:list-item>`;
  if (name === 'table') {
    pictures.tableCount = (pictures.tableCount || 0) + 1;
    return `<table:table table:name="Table${pictures.tableCount}">${content}</table:table>`;
  }
  if (name === 'tr') return `<table:table-row>${content}</table:table-row>`;
  if (name === 'td' || name === 'th') return `<table:table-cell office:value-type="string">${content || '<text:p/>'}</table:table-cell>`;
  if (name === 'a') return `<text:a xlink:href="${esc(node.getAttribute('href') || '')}" xlink:type="simple">${content}</text:a>`;
  if (name === 'img') {
    const src = node.getAttribute('src') || '';
    const match = /^data:image\/(png|jpeg|gif|webp);base64,([a-z0-9+/=]+)$/i.exec(src);
    if (!match) return '';
    const extension = match[1] === 'jpeg' ? 'jpg' : match[1];
    const path = `Pictures/image${pictures.length + 1}.${extension}`;
    pictures.push({ path, data: match[2], mime: `image/${match[1]}` });
    return `<draw:frame draw:name="Image${pictures.length}" text:anchor-type="as-char" svg:width="10cm" svg:height="7cm"><draw:image xlink:href="${path}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/></draw:frame>`;
  }
  const style = { strong: 'Bold', b: 'Bold', em: 'Italic', i: 'Italic', u: 'Underline', s: 'Strike', strike: 'Strike', code: 'Code' }[name];
  if (style) return `<text:span text:style-name="${style}">${content}</text:span>`;
  if (name === 'blockquote') return `<text:p text:style-name="Quote">${content}</text:p>`;
  if (name === 'pre') return `<text:p text:style-name="Code">${esc(node.textContent)}</text:p>`;
  if (name === 'hr') return '<text:p>────────────────────</text:p>';
  return content;
}

export async function exportOdt(html) {
  const zip = new JSZip();
  zip.file('mimetype', 'application/vnd.oasis.opendocument.text', { compression: 'STORE' });
  const holder = document.createElement('div');
  holder.innerHTML = html;
  const pictures = [];
  const content = [...holder.childNodes].map(node => toOdt(node, pictures)).join('');
  const xmlns = 'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"';
  zip.file('content.xml', `<?xml version="1.0" encoding="UTF-8"?><office:document-content ${xmlns} office:version="1.2"><office:automatic-styles/><office:body><office:text>${content}</office:text></office:body></office:document-content>`);
  zip.file('styles.xml', `<?xml version="1.0" encoding="UTF-8"?><office:document-styles ${xmlns} xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" office:version="1.2"><office:styles><style:style style:name="Bold" style:family="text"><style:text-properties fo:font-weight="bold"/></style:style><style:style style:name="Italic" style:family="text"><style:text-properties fo:font-style="italic"/></style:style><style:style style:name="Underline" style:family="text"><style:text-properties style:text-underline-style="solid"/></style:style><style:style style:name="Strike" style:family="text"><style:text-properties style:text-line-through-style="solid"/></style:style><text:list-style style:name="Bulleted"><text:list-level-style-bullet text:level="1" text:bullet-char="•"/></text:list-style><text:list-style style:name="Numbered"><text:list-level-style-number text:level="1" style:num-format="1"/></text:list-style></office:styles></office:document-styles>`);
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
