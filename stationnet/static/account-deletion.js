document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-account-deletion-form]");
  if (!form) return;
  const name = form.dataset.accountUsername;
  const typed = form.elements.confirmation;
  const proceed = form.querySelector("[data-deletion-proceed]");
  const final = form.querySelector("[data-deletion-final]");
  const checkbox = form.querySelector("[data-deletion-checkbox]");
  const submit = form.querySelector("[data-deletion-submit]");
  const resetFinal = () => {
    final.hidden = true;
    checkbox.checked = false;
    submit.disabled = true;
  };
  typed.addEventListener("input", resetFinal);
  checkbox.addEventListener("change", () => {
    submit.disabled = !checkbox.checked;
  });
  proceed.addEventListener("click", () => {
    if (typed.value !== name) {
      typed.setCustomValidity("Type your username exactly as shown.");
      typed.reportValidity();
      return;
    }
    typed.setCustomValidity("");
    if (!window.confirm("Proceed with account deletion?")) return;
    final.hidden = false;
    checkbox.focus();
  });
  form.addEventListener("submit", (event) => {
    if (typed.value !== name || final.hidden || !checkbox.checked) {
      event.preventDefault();
      resetFinal();
      typed.setCustomValidity("Type your username exactly as shown.");
      typed.reportValidity();
    }
  });
  typed.addEventListener("input", () => typed.setCustomValidity(""));
});
