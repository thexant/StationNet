import JSZip from 'jszip';

const NS = 'http://schemas.openxmlformats.org/package/2006/relationships';
const U = 12700; // 960 x 540 px at 96 dpi in EMUs
const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const xml = text => new DOMParser().parseFromString(text, 'application/xml');
const all = (node, name) => [...node.getElementsByTagName('*')].filter(item => item.localName === name);
const child = (node, name) => [...node.children].find(item => item.localName === name);
const attr = (node, name, fallback = '') => node?.getAttribute(name) ?? fallback;
const num = (node, name, fallback = 0) => Number(attr(node, name, fallback));
const color = (node, fallback = '#142631') => { const value = all(node, 'srgbClr')[0]?.getAttribute('val'); return value ? `#${value.slice(-6)}` : fallback; };
const path = (base, target) => { const parts = (base.split('/').slice(0, -1).join('/') + '/' + target).split('/'); const out = []; for (const part of parts) if (part === '..') out.pop(); else if (part && part !== '.') out.push(part); return out.join('/'); };
const textXml = object => {
  const lines = String(object.text || '').split('\n');
  return `<p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>${lines.map((line, index) => `<a:p><a:pPr algn="${object.align === 'center' ? 'ctr' : object.align === 'right' ? 'r' : 'l'}"${object.list === 'bullet' ? '><a:buChar char="•"/></a:pPr>' : object.list === 'number' ? `><a:buAutoNum type="arabicPeriod" startAt="${index + 1}"/></a:pPr>` : '/>'}<a:r><a:rPr lang="en-US" sz="${Math.round((object.fontSize || 28) * 75)}" b="${object.bold ? 1 : 0}" i="${object.italic ? 1 : 0}" u="${object.underline ? 'sng' : 'none'}"><a:solidFill><a:srgbClr val="${(object.color || '#142631').slice(1)}"/></a:solidFill><a:latin typeface="${esc(object.font || 'Arial')}"/></a:rPr><a:t xml:space="preserve">${esc(line)}</a:t></a:r></a:p>`).join('')}</p:txBody>`;
};
const transform = object => `<a:xfrm rot="${Math.round((object.rotation || 0) * 60000)}"><a:off x="${Math.round(object.x * U)}" y="${Math.round(object.y * U)}"/><a:ext cx="${Math.round(Math.max(1, object.w) * U)}" cy="${Math.round(Math.max(1, object.h) * U)}"/></a:xfrm>`;
const shapeXml = (object, id) => {
  const preset = object.type === 'ellipse' ? 'ellipse' : object.type === 'line' ? 'line' : 'rect';
  const fill = object.type === 'text' || object.type === 'line' ? '<a:noFill/>' : `<a:solidFill><a:srgbClr val="${(object.fill || '#00b8aa').slice(1)}"/></a:solidFill>`;
  const outline = object.type === 'line' ? `<a:ln w="25400"><a:solidFill><a:srgbClr val="${(object.fill || '#00b8aa').slice(1)}"/></a:solidFill></a:ln>` : '<a:ln><a:noFill/></a:ln>';
  return `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="Object ${id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>${transform(object)}<a:prstGeom prst="${preset}"><a:avLst/></a:prstGeom>${fill}${outline}</p:spPr>${object.type === 'text' ? textXml(object) : ''}</p:sp>`;
};
const pictureXml = (object, id, rid) => `<p:pic><p:nvPicPr><p:cNvPr id="${id}" name="Picture ${id}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>${transform(object)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>`;
const tableXml = (object, id) => {
  const rows = object.cells || [['', ''], ['', '']];
  const cols = Math.max(1, ...rows.map(row => row.length));
  const cellWidth = Math.round(object.w * U / cols);
  return `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="${id}" name="Table ${id}"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="${Math.round(object.x * U)}" y="${Math.round(object.y * U)}"/><a:ext cx="${Math.round(object.w * U)}" cy="${Math.round(object.h * U)}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="0"/><a:tblGrid>${Array.from({ length: cols }, () => `<a:gridCol w="${cellWidth}"/>`).join('')}</a:tblGrid>${rows.map(row => `<a:tr h="${Math.round(object.h * U / rows.length)}">${Array.from({ length: cols }, (_, col) => `<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="1800"/><a:t>${esc(row[col] || '')}</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc>`).join('')}</a:tr>`).join('')}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>`;
};
const relsXml = entries => `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${NS}">${entries.map(([id, type, target]) => `<Relationship Id="${id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/${type}" Target="${target}"/>`).join('')}</Relationships>`;

export async function exportPptx(data) {
  const zip = new JSZip(), media = [], content = [];
  zip.file('_rels/.rels', relsXml([['rId1', 'officeDocument', 'ppt/presentation.xml']]));
  zip.file('ppt/_rels/presentation.xml.rels', relsXml([['rId1', 'slideMaster', 'slideMasters/slideMaster1.xml'], ...data.slides.map((_, i) => [`rId${i + 2}`, 'slide', `slides/slide${i + 1}.xml`])]));
  zip.file('ppt/presentation.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>${data.slides.map((_, i) => `<p:sldId id="${256 + i}" r:id="rId${i + 2}"/>`).join('')}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>`);
  zip.file('ppt/slideMasters/slideMaster1.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="dk1" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="lt2"/></p:sldMaster>`);
  zip.file('ppt/slideMasters/_rels/slideMaster1.xml.rels', relsXml([['rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml']]));
  zip.file('ppt/slideLayouts/slideLayout1.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sldLayout>`);
  zip.file('ppt/slideLayouts/_rels/slideLayout1.xml.rels', relsXml([['rId1', 'slideMaster', '../slideMasters/slideMaster1.xml']]));
  for (let i = 0; i < data.slides.length; i++) {
    const slide = data.slides[i], relationships = [['rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml'], ['rId2', 'notesSlide', `../notesSlides/notesSlide${i + 1}.xml`]];
    let shapes = '', objectId = 2, rid = 3;
    if (slide.backgroundImage) {
      const extension = slide.backgroundImage.match(/^data:image\/(png|jpeg|webp)/)?.[1] || 'png';
      const filename = `image${media.length + 1}.${extension === 'jpeg' ? 'jpg' : extension}`;
      zip.file(`ppt/media/${filename}`, slide.backgroundImage.split(',')[1], { base64: true }); media.push(filename);
      relationships.push([`rId${rid}`, 'image', `../media/${filename}`]);
      shapes += pictureXml({ x: 0, y: 0, w: 960, h: 540 }, objectId++, `rId${rid++}`);
    }
    for (const object of slide.elements) {
      if (object.type === 'image' && object.src?.startsWith('data:image/')) {
        const extension = object.src.match(/^data:image\/(png|jpeg|webp)/)?.[1] || 'png';
        const filename = `image${media.length + 1}.${extension === 'jpeg' ? 'jpg' : extension}`;
        zip.file(`ppt/media/${filename}`, object.src.split(',')[1], { base64: true }); media.push(filename);
        relationships.push([`rId${rid}`, 'image', `../media/${filename}`]);
        shapes += pictureXml(object, objectId++, `rId${rid++}`);
      } else if (object.type === 'table') shapes += tableXml(object, objectId++);
      else shapes += shapeXml(object, objectId++);
    }
    const bg = (slide.background || '#ffffff').slice(1);
    zip.file(`ppt/slides/slide${i + 1}.xml`, `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="${bg}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>${shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>${slide.transition === 'fade' ? '<p:transition><p:fade/></p:transition>' : ''}</p:sld>`);
    zip.file(`ppt/slides/_rels/slide${i + 1}.xml.rels`, relsXml(relationships));
    zip.file(`ppt/notesSlides/notesSlide${i + 1}.xml`, `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Notes Placeholder"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>${esc(slide.notes || '')}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>`);
    zip.file(`ppt/notesSlides/_rels/notesSlide${i + 1}.xml.rels`, relsXml([['rId1', 'slide', `../slides/slide${i + 1}.xml`]]));
  }
  content.push('<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>', '<Default Extension="xml" ContentType="application/xml"/>', '<Default Extension="png" ContentType="image/png"/>', '<Default Extension="jpg" ContentType="image/jpeg"/>', '<Default Extension="webp" ContentType="image/webp"/>', '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>', '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>', '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>');
  for (let i = 0; i < data.slides.length; i++) { content.push(`<Override PartName="/ppt/slides/slide${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>`); content.push(`<Override PartName="/ppt/notesSlides/notesSlide${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>`); }
  zip.file('[Content_Types].xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">${content.join('')}</Types>`);
  return zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
}

export async function importPptx(file) {
  const zip = await JSZip.loadAsync(file), read = async name => zip.file(name)?.async('string');
  const files = Object.values(zip.files);
  if (files.reduce((size, part) => size + (part._data?.uncompressedSize || 0), 0) > 80 * 1024 * 1024) throw new Error('The expanded PPTX is too large.');
  const presentation = xml(await read('ppt/presentation.xml') || '');
  if (all(presentation, 'parsererror').length || !all(presentation, 'sldId').length) throw new Error('This file has no readable PPTX slides.');
  if (all(presentation, 'sldId').length > 200) throw new Error('PPTX files are limited to 200 slides.');
  const relDoc = xml(await read('ppt/_rels/presentation.xml.rels') || '');
  const rels = new Map(all(relDoc, 'Relationship').map(item => [attr(item, 'Id'), attr(item, 'Target')]));
  const size = all(presentation, 'sldSz')[0], scaleX = 960 / num(size, 'cx', 12192000), scaleY = 540 / num(size, 'cy', 6858000);
  const data = { title: file.name.replace(/\.pptx$/i, ''), slides: [], theme: 'station' };
  const dropped = { charts: 0, smartArt: 0, animations: 0, media: 0, formatting: 0, unsupported: 0, images: 0 };
  for (const slideRef of all(presentation, 'sldId')) {
    const target = rels.get(attr(slideRef, 'r:id'));
    if (!target) continue;
    const slidePath = path('ppt/presentation.xml', target), slideDoc = xml(await read(slidePath) || '');
    const slideRelsPath = path(slidePath, `_rels/${slidePath.split('/').pop()}.rels`);
    const slideRels = new Map(all(xml(await read(slideRelsPath) || ''), 'Relationship').map(item => [attr(item, 'Id'), { type: attr(item, 'Type').split('/').pop(), path: path(slidePath, attr(item, 'Target')) }]));
    const slide = { id: crypto.randomUUID(), layout: 'blank', background: '#ffffff', backgroundImage: null, notes: '', transition: all(slideDoc, 'fade').length ? 'fade' : 'none', elements: [] };
    const bg = all(slideDoc, 'bgPr')[0]; if (bg) slide.background = color(bg, '#ffffff');
    if (all(slideDoc, 'bg').some(item => all(item, 'blip').length)) dropped.images++;
    if (all(slideDoc, 'transition').length && !all(slideDoc, 'fade').length) dropped.animations++;
    const tree = all(slideDoc, 'spTree')[0];
    if (tree) for (const item of [...tree.children]) {
      const type = item.localName;
      if (type === 'sp') {
        const xf = all(item, 'xfrm')[0], off = child(xf || item, 'off'), ext = child(xf || item, 'ext');
        if (!off || !ext) { dropped.unsupported++; continue; }
        const preset = attr(all(item, 'prstGeom')[0], 'prst', 'rect');
        const text = all(item, 'p').map(p => all(p, 't').map(t => t.textContent).join('')).join('\n');
        if (new Set(all(item, 'rPr').map(run => new XMLSerializer().serializeToString(run))).size > 1 || all(item, 'gradFill').length || all(item, 'custGeom').length) dropped.formatting++;
        const shapeType = text ? 'text' : preset === 'ellipse' ? 'ellipse' : preset === 'line' ? 'line' : 'rect';
        if (!['rect', 'ellipse', 'line'].includes(preset)) dropped.unsupported++;
        const run = all(item, 'rPr')[0], para = all(item, 'pPr')[0];
        slide.elements.push({ id: crypto.randomUUID(), type: shapeType, x: num(off, 'x') * scaleX, y: num(off, 'y') * scaleY, w: Math.max(12, num(ext, 'cx') * scaleX), h: Math.max(6, num(ext, 'cy') * scaleY), rotation: num(xf, 'rot') / 60000, text, fill: color(child(item, 'spPr') || item, '#00b8aa'), color: color(run || item, '#142631'), font: attr(all(run || item, 'latin')[0], 'typeface', 'Arial'), fontSize: Math.round(num(run, 'sz', 2100) / 75), bold: attr(run, 'b') === '1', italic: attr(run, 'i') === '1', underline: attr(run, 'u') === 'sng', align: ({ ctr: 'center', r: 'right' })[attr(para, 'algn')] || 'left', list: all(item, 'buChar').length ? 'bullet' : all(item, 'buAutoNum').length ? 'number' : 'none' });
      } else if (type === 'pic') {
        const xf = all(item, 'xfrm')[0], off = child(xf || item, 'off'), ext = child(xf || item, 'ext'), blip = all(item, 'blip')[0];
        const rel = slideRels.get(attr(blip, 'r:embed'));
        if (!off || !ext || !rel || !zip.file(rel.path)) { dropped.images++; continue; }
        const extName = rel.path.split('.').pop().toLowerCase();
        if (!['png', 'jpg', 'jpeg', 'webp'].includes(extName)) { dropped.images++; continue; }
        const content = await zip.file(rel.path).async('base64');
        slide.elements.push({ id: crypto.randomUUID(), type: 'image', x: num(off, 'x') * scaleX, y: num(off, 'y') * scaleY, w: num(ext, 'cx') * scaleX, h: num(ext, 'cy') * scaleY, rotation: num(xf, 'rot') / 60000, src: `data:image/${extName === 'jpg' ? 'jpeg' : extName};base64,${content}` });
      } else if (type === 'graphicFrame') {
        if (all(item, 'chart').length) dropped.charts++;
        else if (all(item, 'tbl').length) { const xf = all(item, 'xfrm')[0], off = child(xf || item, 'off'), ext = child(xf || item, 'ext'); const cells = all(item, 'tr').map(row => all(row, 'tc').map(cell => all(cell, 't').map(t => t.textContent).join(''))); if (off && ext) slide.elements.push({ id: crypto.randomUUID(), type: 'table', x: num(off, 'x') * scaleX, y: num(off, 'y') * scaleY, w: num(ext, 'cx') * scaleX, h: num(ext, 'cy') * scaleY, rotation: 0, cells, fontSize: 20, color: '#142631' }); }
        else if (all(item, 'relIds').length) dropped.smartArt++;
        else dropped.unsupported++;
      } else if (!['nvGrpSpPr', 'grpSpPr'].includes(type)) dropped.unsupported++;
    }
    for (const rel of slideRels.values()) if (rel.type === 'notesSlide' && zip.file(rel.path)) {
      const notesDoc = xml(await read(rel.path)); const body = all(notesDoc, 'sp').find(sp => all(sp, 'ph').some(ph => attr(ph, 'type') === 'body'));
      if (body) slide.notes = all(body, 't').map(t => t.textContent).join('\n');
    }
    dropped.animations += all(slideDoc, 'timing').length;
    dropped.media += all(slideDoc, 'video').length + all(slideDoc, 'audio').length;
    data.slides.push(slide);
  }
  if (!data.slides.length) throw new Error('No slides could be imported.');
  const report = [`Imported ${data.slides.length} slide${data.slides.length === 1 ? '' : 's'}.`];
  const labels = { charts: 'chart(s)', smartArt: 'SmartArt object(s)', animations: 'animation or transition set(s)', media: 'media object(s)', formatting: 'complex text or shape format(s)', unsupported: 'unsupported object(s) or shape style(s)', images: 'image(s) with unsupported or missing data, including slide backgrounds' };
  for (const [key, count] of Object.entries(dropped)) if (count) report.push(`Dropped or simplified ${count} ${labels[key]}.`);
  if (report.length === 1) report.push('No unsupported objects were detected. Some complex formatting may still differ.');
  return { data, report: report.join('\n') };
}
