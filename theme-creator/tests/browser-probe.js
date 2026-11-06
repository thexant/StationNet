/* Browser regression checks. Production index.html never loads this file. */
(async function () {
  const results = [];
  const $ = id => document.getElementById(id);
  const assert = (condition, name) => { if (!condition) throw new Error(name); results.push(name); };
  const set = (id, value) => { $(id).value = value; $(id).dispatchEvent(new Event('input', {bubbles: true})); };
  const tick = () => new Promise(resolve => setTimeout(resolve, 60));
  const main = $('station-preview');
  const mode = new URLSearchParams(location.search).get('probe');
  async function importFile(contents, name = 'test.json') {
    const transfer = new DataTransfer();
    transfer.items.add(new File([contents], name, {type: 'application/json'}));
    $('import-file').files = transfer.files;
    $('import-file').dispatchEvent(new Event('change', {bubbles: true}));
    await tick();
  }
  try {
    assert(document.querySelectorAll('.colour-row').length === 15, 'All 15 colour controls render');
    assert(document.querySelectorAll('#service-list > a').length === 13, 'Service directory renders');
    assert(document.documentElement.scrollWidth <= innerWidth, 'No horizontal page overflow at ' + innerWidth + 'px');
    assert($('editor').getBoundingClientRect().width === (innerWidth >= 1100 ? 360 : document.documentElement.clientWidth), 'Desktop sidebar / mobile drawer sizing');
    const mobile = innerWidth < 1100;
    if (mobile) {
      assert(getComputedStyle($('editor-body')).display === 'none', 'Mobile drawer starts collapsed');
      $('drawer-toggle').click();
      const bounds = $('editor').getBoundingClientRect();
      assert(bounds.height <= innerHeight * .7 + 1, 'Open drawer leaves at least 30% of preview visible');
      assert($('drawer-toggle').getAttribute('aria-expanded') === 'true' && !$('editor-body').inert, 'Open drawer is accessible');
      $('editor').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      assert($('drawer-toggle').getAttribute('aria-expanded') === 'false' && $('editor-body').inert, 'Escape collapses drawer and removes hidden controls from focus');
      $('drawer-toggle').click();
    }
    if (mode === 'restore') {
      assert($('theme-name').value === 'Coastal Relay' && $('theme-author').value === 'Test Pilot', 'Metadata restored on a fresh page load');
      assert($('hex-site-green').value === '#123456', 'Colour edits restored on a fresh page load');
      assert(!$('include-masthead').checked, 'Section selection restored on a fresh page load');
    } else if (mode === 'corrupt') {
      assert($('app-message').textContent.includes('could not be restored'), 'Corrupt stored draft reports a recoverable error');
      assert(main.style.getPropertyValue('--green') === '#00ed45', 'Corrupt draft falls back to defaults');
    } else if (mode === 'blocked') {
      assert($('storage-status').textContent.includes('unavailable'), 'Blocked storage is reported');
      set('hex-site-green', '#5a98b0');
      assert(main.style.getPropertyValue('--green') === '#5a98b0', 'Editing works with blocked storage');
      set('theme-name', 'No Storage');
      set('theme-author', 'Test Pilot');
      const blobs = [];
      URL.createObjectURL = blob => { blobs.push(blob); return 'blob:test'; };
      HTMLAnchorElement.prototype.click = function () {};
      $('export-theme').click();
      assert(blobs.length === 1 && JSON.parse(await blobs[0].text()).name === 'No Storage', 'Export works with blocked storage');
    } else if (mode === 'edit') {
      const editorBackground = getComputedStyle($('editor')).backgroundColor;
      set('hex-site-green', '#123456');
      assert(main.style.getPropertyValue('--green') === '#123456', 'Hex changes update the preview immediately');
      assert(getComputedStyle($('editor')).backgroundColor === editorBackground, 'Palette changes do not recolour the editor');
      set('hex-site-green', '#bad');
      assert(main.style.getPropertyValue('--green') === '#123456' && !$('error-site-green').hidden, 'Invalid hex preserves last valid preview and shows an error');
      $('export-theme').click();
      assert(document.activeElement.id === 'hex-site-green', 'Export focuses an invalid colour');
      set('picker-site-green', '#969696');
      assert($('hex-site-green').value === '#969696' && $('error-site-green').hidden, 'Picker synchronizes hex input and clears error');
      assert(main.style.getPropertyValue('--surface-field') === '#040404', 'Derived surfaces use ties-to-even rounding');
      $('details-tab').click();
      set('theme-name', 'Coastal Relay');
      set('theme-author', 'Test Pilot');
      assert($('theme-id').value === 'coastal-relay', 'ID follows the theme name');
      set('theme-id', 'custom-id');
      set('theme-name', 'Changed Name');
      assert($('theme-id').value === 'custom-id', 'Manually edited ID is preserved');
      $('palette-tab').click();
      $('preset').value = 'violet';
      $('apply-preset').click();
      assert($('theme-name').value === 'Changed Name' && $('theme-id').value === 'custom-id', 'Preset preserves metadata');
      assert(main.style.getPropertyValue('--green') === ThemeData.presets.violet.green, 'Preset applies its palette');
      $('details-tab').click();
      $('include-site').click();
      assert(main.style.getPropertyValue('--green') === '#00ed45', 'Excluded interface previews default colours');
      $('include-site').click();
      assert(main.style.getPropertyValue('--green') === ThemeData.presets.violet.green, 'Reincluded interface restores edits');
      const downloads = [];
      URL.createObjectURL = blob => { downloads.push({blob}); return 'blob:test'; };
      HTMLAnchorElement.prototype.click = function () { if (this.download) downloads.at(-1).filename = this.download; };
      for (const [site, masthead] of [[true, true], [true, false], [false, true]]) {
        if ($('include-site').checked !== site) $('include-site').click();
        if ($('include-masthead').checked !== masthead) $('include-masthead').click();
        $('export-theme').click();
        const downloaded = downloads.at(-1);
        const json = JSON.parse(await downloaded.blob.text());
        assert(('site' in json) === site && ('masthead' in json) === masthead && downloaded.filename === 'custom-id.json', 'Downloaded JSON sections and filename: ' + site + '/' + masthead);
      }
      $('include-masthead').click();
      const previous = downloads.length;
      $('export-theme').click();
      assert(downloads.length === previous && !$('sections-error').hidden, 'Export requires at least one section');
      $('include-site').click();
      set('theme-id', 'template-theme');
      $('export-theme').click();
      assert(downloads.length === previous && !$('id-error').hidden, 'Reserved ID cannot download');
      set('theme-id', 'custom-id');
      const imported = {id: 'imported', name: 'Imported Theme', author: 'Tester', masthead: {'masthead': '#112233', 'masthead-shadow': '#445566', '_comment_masthead': 'Allowed comment'}};
      await importFile(JSON.stringify(imported));
      assert($('theme-name').value === 'Imported Theme' && !$('include-site').checked && $('include-masthead').checked, 'Partial JSON import replaces metadata and selected sections');
      assert(main.style.getPropertyValue('--masthead-color') === '#112233' && main.style.getPropertyValue('--green') === '#00ed45', 'Partial import previews included palette and missing defaults');
      await importFile('{broken');
      assert($('theme-name').value === 'Imported Theme' && $('app-message').dataset.error === 'true', 'Failed JSON import preserves draft');
      await importFile('x'.repeat(16385));
      assert($('theme-name').value === 'Imported Theme' && $('app-message').textContent.includes('16 KiB'), 'Oversize import preserves draft');
      await importFile(new Uint8Array([0xff, 0xfe, 0x7b]));
      assert($('app-message').textContent.includes('UTF-8') && $('theme-name').value === 'Imported Theme', 'Invalid UTF-8 import preserves draft');
      window.confirm = () => false;
      $('reset-theme').click();
      assert($('theme-name').value === 'Imported Theme', 'Reset cancellation keeps draft');
      window.confirm = () => true;
      $('reset-theme').click();
      assert($('theme-name').value === '' && main.style.getPropertyValue('--green') === '#00ed45', 'Confirmed reset restores defaults and metadata');
      assert(localStorage.getItem('stationnet-theme-creator.draft.v1') === null, 'Confirmed reset clears stored draft');
      $('palette-tab').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
      assert(document.activeElement.id === 'details-tab' && !$('details-panel').hidden, 'Keyboard tab navigation selects and focuses panel');
      set('theme-name', 'Coastal Relay');
      set('theme-author', 'Test Pilot');
      set('hex-site-green', '#123456');
      $('include-masthead').click();
      assert(JSON.parse(localStorage.getItem('stationnet-theme-creator.draft.v1')).site.green === '#123456', 'Draft saved for next browser launch');
      $('palette-tab').click();
    }
    if (mode === 'default' && mobile) $('drawer-toggle').click();
    assert(browserErrors.length === 0, 'No uncaught browser errors: ' + browserErrors.join('; '));
    // Recheck after all controls, validation messages, and drawer transitions.
    assert(document.documentElement.scrollWidth <= innerWidth, 'No horizontal overflow after editing');
    document.body.dataset.testStatus = 'passed';
  } catch (error) { document.body.dataset.testStatus = 'failed'; results.push('FAIL: ' + error.stack); }
  const output = document.createElement('pre');
  output.id = 'browser-results';
  output.hidden = true;
  output.textContent = JSON.stringify({status: document.body.dataset.testStatus, width: innerWidth, height: innerHeight, results});
  document.body.append(output);
})();
