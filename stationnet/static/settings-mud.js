for (const group of document.querySelectorAll('[data-origin-group]')) {
  const boxes = [...group.querySelectorAll('input[type="checkbox"][data-origin-source]')];
  for (const button of group.querySelectorAll('[data-origin-selection]')) {
    button.addEventListener('click', () => {
      for (const box of boxes) {
        const selection = button.dataset.originSelection;
        box.checked = selection === 'all' || box.dataset.originSource === selection ||
          (selection === 'bundled' && box.dataset.bundled === 'true');
      }
    });
  }
}

const originRemoval = document.getElementById('mud-origin-removal');
if (originRemoval) {
  const boxes = [...originRemoval.querySelectorAll('[data-remove-name]')];
  const summary = originRemoval.querySelector('.origin-removal-summary');
  const confirmation = originRemoval.querySelector('[name="confirm_remove"]');
  const update = () => {
    const chosen = boxes.filter(box => box.checked);
    summary.textContent = chosen.length
      ? `Remove ${chosen.length} choice${chosen.length === 1 ? '' : 's'}: ${chosen.map(box => `${box.dataset.removeKind}: ${box.dataset.removeName}`).join('; ')}`
      : 'No imported choices selected.';
    confirmation.checked = false;
  };
  for (const box of boxes) box.addEventListener('change', update);
  originRemoval.addEventListener('submit', event => {
    if (!boxes.some(box => box.checked)) {
      event.preventDefault();
      summary.textContent = 'Select at least one imported race or class.';
    }
  }, true);
}

const skillRemoval = document.getElementById('mud-skill-removal');
if (skillRemoval) {
  const boxes = [...skillRemoval.querySelectorAll('[data-remove-name]')];
  const summary = skillRemoval.querySelector('.origin-removal-summary');
  const confirmation = skillRemoval.querySelector('[name="confirm_remove"]');
  const update = () => {
    const chosen = boxes.filter(box => box.checked);
    const chosenNames = new Set(chosen.map(box => `skill ${box.dataset.removeName}`));
    const lines = chosen.map(box => {
      const blockers = box.dataset.removeDependents.split(', ').filter(name => name && !chosenNames.has(name));
      const holders = Number(box.dataset.removeHolders);
      return `${box.dataset.removeName}${blockers.length ? ` (blocked by ${blockers.join(', ')})` : ''}${holders ? ` (kept inactive for ${holders} character${holders === 1 ? '' : 's'})` : ''}`;
    });
    summary.textContent = chosen.length
      ? `Remove ${chosen.length} skill${chosen.length === 1 ? '' : 's'}: ${lines.join('; ')}`
      : 'No imported skills selected.';
    confirmation.checked = false;
  };
  for (const box of boxes) box.addEventListener('change', update);
  for (const button of skillRemoval.querySelectorAll('[data-skill-removal]')) {
    button.addEventListener('click', () => {
      for (const box of boxes) box.checked = button.dataset.skillRemoval === 'all';
      update();
    });
  }
  skillRemoval.addEventListener('submit', event => {
    if (!boxes.some(box => box.checked)) {
      event.preventDefault();
      summary.textContent = 'Select at least one imported skill.';
    }
  }, true);
}

function showRemovalDetails(details, entries) {
  details.replaceChildren();
  if (!entries) return;
  for (const kind of ['races', 'classes', 'skills']) {
    const line = document.createElement('p');
    const items = entries[kind];
    line.textContent = `${kind}: ${items.length ? items.map(item => `${item.name} (${item.removable ? 'eligible for removal' : 'shared or unavailable; preserved'})`).join(', ') : 'none'}`;
    details.append(line);
  }
}

const worlds = document.getElementById('mud-worlds');
if (worlds) {
  const linked = JSON.parse(worlds.dataset.worldLinked);
  const removal = JSON.parse(worlds.dataset.worldRemoval);
  const removeSelect = document.getElementById('world-remove-id');
  const removeDetails = document.getElementById('world-remove-origins');
  removeSelect?.addEventListener('change', () => {
    showRemovalDetails(removeDetails, removal[removeSelect.value]);
  });

  const exportSelect = document.getElementById('world-export-id');
  exportSelect?.addEventListener('change', () => {
    for (const box of document.querySelectorAll('.world-export-origin')) {
      box.checked = (linked[exportSelect.value]?.[box.name] || []).includes(box.value);
    }
  });

  for (const prefix of ['world-import']) {
    const mode = document.getElementById(`${prefix}-mode`);
    const manual = document.getElementById(`${prefix}-manual`);
    if (!mode || !manual) continue;
    const update = () => { manual.hidden = mode.value !== 'manual'; };
    mode.addEventListener('change', update);
    update();
  }

  const file = document.getElementById('world-import-file');
  const importForm = document.getElementById('world-import-form');
  const problemList = document.getElementById('world-import-problems');
  const upgradePanel = document.getElementById('world-import-upgrade');
  const showProblems = (problems) => {
    problemList.replaceChildren();
    if (!problems?.length) return;
    const list = document.createElement('ul');
    for (const problem of problems) {
      const item = document.createElement('li');
      const where = [problem.file, problem.path].filter(Boolean).join(' ');
      item.textContent = `${problem.severity.toUpperCase()}${where ? ` · ${where}` : ''}: ${problem.message}`;
      list.append(item);
    }
    problemList.append(list);
  };
  const showUpgrade = (upgrade) => {
    upgradePanel.hidden = !upgrade;
    upgradePanel.querySelector('[name="mode"]').value = upgrade ? 'upgrade' : '';
    upgradePanel.querySelector('[name="expected_hash"]').value = upgrade?.expected_hash || '';
    const changes = document.getElementById('world-import-changes');
    changes.replaceChildren();
    if (!upgrade) return;
    const summary = document.createElement('p');
    summary.textContent = `${upgrade.world} is installed with ${upgrade.characters} character(s)` +
      (upgrade.moved_to_start ? `; ${upgrade.moved_to_start} stand in rooms this version removes and will move to its start room.` : '.');
    const list = document.createElement('ul');
    for (const [section, change] of Object.entries(upgrade.changes)) {
      const item = document.createElement('li');
      const parts = ['added', 'changed', 'removed'].filter(key => change[key].length)
        .map(key => `${change[key].length} ${key}`);
      item.textContent = `${section.replace(/^mud_/, '').replaceAll('_', ' ')}: ${parts.join(', ')}`;
      list.append(item);
    }
    changes.append(summary, list);
  };
  file?.addEventListener('change', async () => {
    for (const kind of ['races', 'classes', 'skills']) {
      document.getElementById(`world-import-bundled-${kind}`).replaceChildren();
    }
    showProblems([]);
    showUpgrade(null);
    if (!file.files.length) return;
    const data = new FormData();
    data.append('csrf_token', importForm.querySelector('[name="csrf_token"]').value);
    data.append('world_file', file.files[0]);
    try {
      const response = await fetch(importForm.dataset.previewUrl, {method: 'POST', body: data});
      const result = await response.json();
      showProblems(result.problems);
      if (!response.ok) throw new Error(result.error || 'Could not preview archive.');
      showUpgrade(result.upgrade);
      for (const kind of ['races', 'classes', 'skills']) {
        const container = document.getElementById(`world-import-bundled-${kind}`);
        for (const origin of result.origins[kind]) {
          const label = document.createElement('label');
          label.className = 'switch';
          const box = document.createElement('input');
          box.type = 'checkbox';
          box.name = kind;
          box.value = origin.key;
          box.checked = true;
          label.append(box, document.createTextNode(` ${origin.name} (bundled)`));
          container.append(label);
        }
      }
    } catch (error) {
      const note = document.createElement('small');
      note.textContent = error.message;
      document.getElementById('world-import-bundled-races').append(note);
    }
  });
}

const dungeonRemoval = document.querySelector('[data-dungeon-removal]');
if (dungeonRemoval) {
  const removal = JSON.parse(dungeonRemoval.dataset.dungeonRemoval);
  const select = document.getElementById('dungeon-remove-id');
  const details = document.getElementById('dungeon-remove-origins');
  select.addEventListener('change', () => {
    showRemovalDetails(details, removal[select.value]);
  });
}
