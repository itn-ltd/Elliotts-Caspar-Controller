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

// The server name (from /api/health, open before sign-in) goes in the tab title, nav and heading.
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
