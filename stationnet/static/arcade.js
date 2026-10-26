document.addEventListener('DOMContentLoaded', () => {
  const shell = document.querySelector('[data-arcade-player]');
  if (!shell) return;
  const stage = shell.querySelector('[data-player-stage]');
  const play = shell.querySelector('[data-play]');
  const fullscreen = shell.querySelector('[data-fullscreen]');
  play?.addEventListener('click', () => {
    const frame = document.createElement('iframe');
    frame.src = shell.dataset.playerUrl;
    frame.title = `${shell.dataset.gameTitle} emulator`;
    frame.allow = 'fullscreen; gamepad';
    frame.allowFullscreen = true;
    frame.setAttribute('loading', 'eager');
    stage.replaceChildren(frame);
    fullscreen.hidden = false;
    frame.focus();
  }, {once: true});
  fullscreen?.addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else shell.requestFullscreen?.();
  });
});
