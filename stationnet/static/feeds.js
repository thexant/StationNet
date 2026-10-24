document.querySelectorAll('[data-feed-counter-input]').forEach((input) => {
  const counter = input.parentElement.querySelector('[data-feed-counter]');
  const update = () => { counter.textContent = Array.from(input.value).length; };
  input.addEventListener('input', update);
  update();
});
