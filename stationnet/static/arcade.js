document.addEventListener('DOMContentLoaded', () => {
  const shell = document.querySelector('[data-arcade-player]');
  if (!shell) return;
  const stage = shell.querySelector('[data-player-stage]');
  const play = shell.querySelector('[data-play]');
  const isolated = 'requiresIsolation' in shell.dataset;
  play?.addEventListener('click', () => {
    if (isolated && !window.crossOriginIsolated) {
      // Threaded cores need SharedArrayBuffer, which browsers only expose on
      // cross-origin isolated pages served over HTTPS or localhost.
      const notice = document.createElement('p');
      notice.className = 'muted';
      notice.textContent = 'This game requires StationNet to be opened over HTTPS (or on localhost) in a browser with WebGL2.';
      stage.replaceChildren(notice);
      return;
    }
    const frame = document.createElement('iframe');
    frame.src = shell.dataset.playerUrl;
    frame.title = `${shell.dataset.gameTitle} emulator`;
    frame.allow = isolated ? 'fullscreen; gamepad; cross-origin-isolated' : 'fullscreen; gamepad';
    frame.allowFullscreen = true;
    frame.setAttribute('loading', 'eager');
    stage.replaceChildren(frame);
    frame.focus();
  }, {once: true});
});
