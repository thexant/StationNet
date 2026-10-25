// The derived OFFICE key is shared by OFFICE programs in this browser tab only.
// It is never sent to the server. A new sign-in gets a new CSRF token and cannot
// reuse a previous sign-in's cached key.
const STORAGE_KEY = 'stationnet.office.unlock.v1';

export function rememberOfficeKey(key, csrf) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      csrf,
      user: document.body.dataset.userId,
      key: btoa(String.fromCharCode(...key)),
    }));
  } catch { /* Manual unlock remains available when tab storage is disabled. */ }
}

export function readOfficeKey(csrf) {
  try {
    const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY));
    if (!value || value.csrf !== csrf || value.user !== document.body.dataset.userId) {
      forgetOfficeKey();
      return null;
    }
    const bytes = Uint8Array.from(atob(value.key), char => char.charCodeAt(0));
    if (bytes.length !== 32) throw new Error('Invalid OFFICE key.');
    return bytes;
  } catch { forgetOfficeKey(); return null; }
}

export function forgetOfficeKey() {
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Tab storage is unavailable. */ }
}
