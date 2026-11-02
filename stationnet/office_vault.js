// The derived OFFICE key is shared by OFFICE programs in this browser tab. It is
// never sent to the server. A new sign-in gets a new CSRF token and cannot reuse
// a previous sign-in's tab copy. If the user opts in, a second copy stays in this
// browser for that user until LOCK or DISCONNECT (see station.js) removes it.
const STORAGE_KEY = 'stationnet.office.unlock.v1';
const REMEMBERED_KEY = 'stationnet.office.remembered.v1';

function encodeKey(key) { return btoa(String.fromCharCode(...key)); }

function decodeKey(value) {
  const bytes = Uint8Array.from(atob(value), char => char.charCodeAt(0));
  if (bytes.length !== 32) throw new Error('Invalid OFFICE key.');
  return bytes;
}

function writeTabKey(key, csrf) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ csrf, user: document.body.dataset.userId, key: encodeKey(key) }));
  } catch { /* Manual unlock remains available when tab storage is disabled. */ }
}

function forgetTabKey() {
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Tab storage is unavailable. */ }
}

function forgetRememberedKey() {
  try { localStorage.removeItem(REMEMBERED_KEY); } catch { /* Browser storage is unavailable. */ }
}

function readTabKey(csrf) {
  try {
    const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY));
    if (!value) return null;
    if (value.csrf !== csrf || value.user !== document.body.dataset.userId) { forgetTabKey(); return null; }
    return decodeKey(value.key);
  } catch { forgetTabKey(); return null; }
}

function readRememberedKey() {
  try {
    const value = JSON.parse(localStorage.getItem(REMEMBERED_KEY));
    if (!value) return null;
    if (value.user !== document.body.dataset.userId) { forgetRememberedKey(); return null; }
    return decodeKey(value.key);
  } catch { forgetRememberedKey(); return null; }
}

export function rememberOfficeKey(key, csrf, persist = false) {
  writeTabKey(key, csrf);
  if (!persist) { forgetRememberedKey(); return; }
  try {
    localStorage.setItem(REMEMBERED_KEY, JSON.stringify({ user: document.body.dataset.userId, key: encodeKey(key) }));
  } catch { /* The key stays in this tab when browser storage is disabled. */ }
}

export function readOfficeKey(csrf) {
  const tabKey = readTabKey(csrf);
  if (tabKey) return tabKey;
  const remembered = readRememberedKey();
  if (remembered) writeTabKey(remembered, csrf);
  return remembered;
}

export function forgetOfficeKey() {
  forgetTabKey();
  forgetRememberedKey();
}
