function toast(msg, type='info') {
  const c = document.getElementById('toast-container');
  if (!c) return;
  const t = document.createElement('div');
  t.className = 'toast toast-' + type;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

function escapeHtml(value) {
  const map = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
  return String(value ?? '').replace(/[&<>"']/g, c => map[c]);
}

function options(values, current) {
  return values.map(v => `<option value="${escapeHtml(v)}"${v === current ? ' selected' : ''}>${escapeHtml(v)}</option>`).join('');
}

function api(url, method='GET', body=null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  return fetch(url, opts).then(r => {
    if (r.status === 401) { location.href = '/login'; throw new Error('signed out'); }
    if (r.status === 403 && url !== '/api/password') { location.href = '/change-password'; throw new Error('password change required'); }
    if (r.ok) return r.json();
    return r.json().catch(() => ({})).then(d => {
      throw new Error(typeof d.detail === 'string' ? d.detail : 'Request failed (' + r.status + ')');
    });
  });
}

// The header every page shares, as in the other company apps: logo, app name and nav on the
// left; this controller's server name and Sign out on the right.
const NAV = [['dashboard', '/', 'Dashboard'], ['triggers', '/triggers', 'Triggers'], ['settings', '/settings', 'Settings']];
const SIGN_OUT_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
  + '<path d="M16 17l5-5-5-5M21 12H9"/></svg>';
const topbar = document.querySelector('header.topbar');
if (topbar) {
  topbar.innerHTML = `
    <a class="brand" href="/"><span class="itn-logo">ITN</span><span class="brand-name">Caspar Controller</span></a>
    <nav class="topnav">${NAV.map(([key, href, label]) =>
      `<a href="${href}" class="${key === topbar.dataset.page ? 'active' : ''}">${label}</a>`).join('')}</nav>
    <div class="topbar-right">
      <span class="topbar-server" data-server-name hidden></span>
      <button class="signout" id="sign-out" hidden>${SIGN_OUT_ICON}Sign out</button>
    </div>`;
  document.getElementById('sign-out').onclick = () =>
    fetch('/logout', {method: 'POST'}).then(() => { location.href = '/login'; });
  // Only once a password is set: before that there's no sign-in to leave.
  api('/api/config').then(cfg => { document.getElementById('sign-out').hidden = !cfg.password_set; }).catch(() => {});
}

// Tabs (Settings): the open tab is kept in the URL, so a reload or a link returns to it.
function openTab(key) {
  const tabs = [...document.querySelectorAll('.tab[data-tab]')];
  if (!tabs.length) return;
  if (!tabs.some(t => t.dataset.tab === key)) key = tabs[0].dataset.tab;
  tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === key));
  document.querySelectorAll('.tab-panel[data-tab]').forEach(panel => { panel.hidden = panel.dataset.tab !== key; });
  history.replaceState(null, '', '#' + key);
}
document.querySelectorAll('.tab[data-tab]').forEach(t => { t.onclick = () => openTab(t.dataset.tab); });
openTab(location.hash.slice(1));

// The server name (from /api/health, open before sign-in) goes in the tab title and header.
fetch('/api/health').then(r => r.json()).then(h => {
  const name = (h.server_name || '').trim();
  if (!name) return;
  document.title = document.title.replace("Elliott's Caspar Controller", name);
  document.querySelectorAll('[data-server-name]').forEach(el => { el.textContent = name; el.hidden = false; });
}).catch(() => {});

// Sign-in and change-password forms: submit() is the page's own; Enter calls it.
function submitPassword(url, password) {
  fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password})})
    .then(r => r.json().then(d => ({ok:r.ok, d})))
    .then(({ok, d}) => { if (ok) location.href = '/'; else document.getElementById('form-error').textContent = d.detail; });
}
document.addEventListener('keydown', e => { if (e.key === 'Enter' && typeof submit === 'function') submit(); });

// A clip <select>'s options: clips first, then the test patterns, keeping the current choice.
function clipOptions(clips, current) {
  const regular = clips.filter(c => !c.startsWith('TEST_'));
  const tests = clips.filter(c => c.startsWith('TEST_'));
  return '<option value="">— pick a clip —</option>' + options(regular, current)
    + (tests.length ? '<option disabled>─── Test Patterns ───</option>' + options(tests, current) : '');
}

// The page's one /api/events stream, shared by the dashboard and the console panel.
// Live.on(name, fn) for "status", "log", "console" or "open"; Live.follow(id) picks the
// instance whose console lines it carries (null: none).
const Live = (() => {
  const handlers = {status: [], log: [], console: [], open: []};
  let source = null, consoleId = null;
  function connect() {
    if (source) source.close();
    source = new EventSource('/api/events' + (consoleId ? '?console=' + consoleId : ''));
    ['status', 'log', 'console'].forEach(name => source.addEventListener(name, e => {
      const data = JSON.parse(e.data);
      handlers[name].forEach(fn => fn(data));
    }));
    source.onopen = () => handlers.open.forEach(fn => fn());
    // Closed (not reconnecting) means the server refused it: reload, and sign in if asked.
    source.onerror = () => { if (source.readyState === EventSource.CLOSED) location.reload(); };
  }
  return {
    on(name, fn) { handlers[name].push(fn); if (!source) connect(); },
    follow(id) { if (id !== consoleId) { consoleId = id; if (source) connect(); } },
  };
})();

// Copy to the clipboard, with a fallback for plain HTTP from another PC (no clipboard API
// there). Says so if nothing was copied.
function copyText(text, done = 'Copied') {
  const ok = () => toast(done, 'success');
  const fail = () => toast('Copy failed: select the text and copy it', 'warning');
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(ok, fail);
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch (e) {}
  ta.remove();
  (copied ? ok : fail)();
}
