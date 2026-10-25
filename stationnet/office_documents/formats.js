import DOMPurify from 'dompurify';
import * as mammoth from 'mammoth';
import { marked } from 'marked';
import TurndownService from 'turndown';
import { gfm } from 'turndown-plugin-gfm';
import { convertHtmlToDocx } from 'dom-docx/browser';
import { jsPDF } from 'jspdf';
import { convertRtfToHtml } from 'rtf-to-html-converter';
import { importOdt, exportOdt } from './odt.js';

const allowedTags = ['p','div','br','h1','h2','h3','h4','strong','b','em','i','u','s','strike','ul','ol','li','blockquote','pre','code','hr','a','img','table','thead','tbody','tr','th','td','span'];
const allowedAttributes = ['href','src','alt','title','colspan','rowspan','style'];
DOMPurify.addHook('uponSanitizeAttribute', (_node, data) => {
  if (data.attrName === 'style') {
    const match = /^\s*text-align:\s*(left|right|center|justify)\s*;?\s*$/i.exec(data.attrValue || '');
    if (match) data.attrValue = `text-align: ${match[1].toLowerCase()}`;
    else data.keepAttr = false;
  }
});

export function cleanHtml(input) {
  const safe = DOMPurify.sanitize(input, {
    ALLOWED_TAGS: allowedTags,
    ALLOWED_ATTR: allowedAttributes,
    ALLOW_DATA_ATTR: false,
  });
  const wrapper = document.createElement('div');
  wrapper.innerHTML = safe;
  wrapper.querySelectorAll('img').forEach(image => {
    if (!/^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=]+$/i.test(image.getAttribute('src') || '')) image.remove();
  });
  wrapper.querySelectorAll('a').forEach(link => {
    if (!/^(https?:\/\/|mailto:|#)/i.test(link.getAttribute('href') || '')) link.removeAttribute('href');
  });
  return wrapper.innerHTML;
}

export async function importFile(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  let html;
  if (ext === 'docx') {
    const result = await mammoth.convertToHtml({ arrayBuffer: await file.arrayBuffer() }, {
      convertImage: mammoth.images.imgElement(image => image.read('base64').then(data => ({ src: `data:${image.contentType};base64,${data}` }))),
    });
    html = result.value;
  } else if (ext === 'odt') {
    html = await importOdt(await file.arrayBuffer());
  } else if (ext === 'md') {
    html = marked.parse(await file.text());
  } else if (ext === 'txt') {
    const escaped = (await file.text()).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
    html = escaped.split(/\r?\n\r?\n/).map(part => `<p>${part.replaceAll(/\r?\n/g,'<br>')}</p>`).join('');
  } else if (ext === 'html' || ext === 'htm') {
    html = await file.text();
  } else if (ext === 'rtf') {
    html = convertRtfToHtml(await file.text());
  } else {
    throw new Error('Unsupported import format.');
  }
  return cleanHtml(html);
}

function filename(title, extension) {
  return `${(title || 'Untitled document').replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').slice(0, 100)}.${extension}`;
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export async function exportFile(format, title, html, text) {
  const clean = cleanHtml(html);
  if (format === 'docx') {
    const blob = await convertHtmlToDocx(`<!doctype html><html><head><meta charset="utf-8"></head><body>${clean}</body></html>`, { styleSource: 'inline' });
    download(blob, filename(title, 'docx'));
  } else if (format === 'odt') {
    download(await exportOdt(clean), filename(title, 'odt'));
  } else if (format === 'html') {
    download(new Blob([`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title.replaceAll('&','&amp;').replaceAll('<','&lt;')}</title></head><body>${clean}</body></html>`], { type: 'text/html;charset=utf-8' }), filename(title, 'html'));
  } else if (format === 'md') {
    const turndown = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced' });
    turndown.use(gfm);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = clean;
    wrapper.querySelectorAll('th,td').forEach(cell => {
      const nodes = [...cell.childNodes].filter(node => node.nodeType !== Node.TEXT_NODE || node.textContent.trim());
      if (nodes.length && nodes.every(node => node.nodeType === Node.ELEMENT_NODE && node.localName === 'p')) {
        cell.innerHTML = nodes.map(node => node.innerHTML).join('<br>');
      }
    });
    download(new Blob([turndown.turndown(wrapper.innerHTML)], { type: 'text/markdown;charset=utf-8' }), filename(title, 'md'));
  } else if (format === 'txt') {
    download(new Blob([text], { type: 'text/plain;charset=utf-8' }), filename(title, 'txt'));
  } else if (format === 'pdf') {
    const holder = document.createElement('div');
    holder.className = 'documents-pdf-source';
    holder.innerHTML = `<h1></h1>${clean}`;
    holder.firstElementChild.textContent = title;
    document.body.append(holder);
    try {
      const pdf = new jsPDF({ unit: 'pt', format: 'a4' });
      await pdf.html(holder, { x: 36, y: 36, width: 523, windowWidth: 800, autoPaging: 'text', margin: [36,36,36,36] });
      pdf.save(filename(title, 'pdf'));
    } finally {
      holder.remove();
    }
  }
}
