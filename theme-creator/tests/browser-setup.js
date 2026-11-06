/* Only injected into the temporary copy created by browser_check.py. */
globalThis.browserErrors = [];
addEventListener('error', event => browserErrors.push(event.message));
addEventListener('unhandledrejection', event => browserErrors.push(String(event.reason)));
const probe = new URLSearchParams(location.search).get('probe');
if (probe === 'edit' || probe === 'default') localStorage.removeItem('stationnet-theme-creator.draft.v1');
if (probe === 'corrupt') localStorage.setItem('stationnet-theme-creator.draft.v1', '{broken');
if (probe === 'blocked') Object.defineProperty(window, 'localStorage', {get() { throw new Error('Storage blocked for test.'); }});
