(() => {
  const form = document.querySelector("[data-audio-bulk-form]");
  if (!form) return;
  const boxes = [...document.querySelectorAll("[data-audio-select]")];
  const all = form.querySelector("[data-audio-select-all]");
  const count = form.querySelector("[data-audio-selection-count]");
  const remove = form.querySelector("[data-audio-delete-selected]");
  const update = () => {
    const selected = boxes.filter(box => box.checked).length;
    count.textContent = `${selected} SELECTED`;
    remove.disabled = selected === 0;
    all.checked = selected === boxes.length;
    all.indeterminate = selected > 0 && selected < boxes.length;
  };
  all.addEventListener("change", () => {
    boxes.forEach(box => { box.checked = all.checked; });
    update();
  });
  boxes.forEach(box => box.addEventListener("change", update));
  form.addEventListener("submit", event => {
    const selected = boxes.filter(box => box.checked).length;
    if (!selected || !window.confirm(`Permanently delete ${selected} selected audio ${selected === 1 ? "file" : "files"}? Playlist entries will remain marked as missing.`)) {
      event.preventDefault();
    }
  });
  update();
})();
