// Applies the cached UI shell before the React bundle loads so the page never
// flashes the wrong chrome. `?ui=<version>` overrides the cache.
// Keep the shell table in sync with `src/contexts/uiVersion.ts`.
//
// A separate same-origin file, loaded by a blocking <script src> in index.html's
// <head>, not an inline script: the backend's Content-Security-Policy
// (`script-src 'self'`) blocks inline scripts, which left <html> without its
// `dark` and shell classes until React mounted (JAN-225).
(function () {
  // Every shell is dark; only the per-shell identity class is optional.
  var SHELL_CLASSES = { v1: null, v2: 'ui-v2', v3: 'ui-v3' };
  var root = document.documentElement;
  root.classList.add('dark');

  var known = function (value) {
    return Object.prototype.hasOwnProperty.call(SHELL_CLASSES, value);
  };

  var version = null;
  // The `?ui=` override is resolved on its own so that blocked storage
  // (private mode) can never swallow the rollback escape hatch.
  try {
    var forced = new URLSearchParams(window.location.search).get('ui');
    if (known(forced)) {
      version = forced;
    }
  } catch (error) {
    /* No usable override. */
  }

  if (version === null) {
    try {
      var stored = window.localStorage.getItem('ui_version');
      if (known(stored)) {
        version = stored;
      }
    } catch (error) {
      /* Storage unavailable — the backend stays authoritative. */
    }
  }

  version = version || 'v1';

  Object.keys(SHELL_CLASSES).forEach(function (key) {
    if (SHELL_CLASSES[key]) {
      root.classList.toggle(SHELL_CLASSES[key], key === version);
    }
  });
})();
