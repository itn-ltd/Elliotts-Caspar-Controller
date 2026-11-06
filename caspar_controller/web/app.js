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
const SIGN_OUT_ICON = '<svg class="icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">'
  + '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/></svg>';
const CONSOLE_ICON = '<svg class="icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">'
  + '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/></svg>';
const topbar = document.querySelector('header.topbar');
if (topbar) {
  topbar.innerHTML = `
    <a class="brand" href="/"><span class="itn-logo">ITN</span><span class="brand-name">Caspar Controller</span></a>
    <nav class="topnav">${NAV.map(([key, href, label]) =>
      key === topbar.dataset.page ? `<a href="${href}" class="active" aria-current="page">${label}</a>`
        : `<a href="${href}">${label}</a>`).join('')}</nav>
    <div class="topbar-right">
      <span class="topbar-server" data-server-name hidden></span>
      <button class="signout" id="sign-out" hidden>${SIGN_OUT_ICON}Sign out</button>
      <button class="icon-btn" id="console-toggle" title="Console" aria-label="Console" hidden>${CONSOLE_ICON}</button>
    </div>`;
  document.getElementById('sign-out').onclick = () =>
    fetch('/logout', {method: 'POST'}).then(() => { location.href = '/login'; });
}

// Sign out shows only once a password is set: before that there's no sign-in to leave.
// Settings passes what it already loaded; other pages ask.
function showSignOut(passwordSet) {
  const button = document.getElementById('sign-out');
  if (button) button.hidden = !passwordSet;
}
if (topbar && topbar.dataset.page !== 'settings')
  api('/api/config').then(cfg => showSignOut(cfg.password_set)).catch(() => {});

// Tabs (Settings): the open tab is kept in the URL, so a reload or a link returns to it.
function openTab(key) {
  const tabs = [...document.querySelectorAll('.tab[data-tab]')];
  if (!tabs.length) return;
  if (!tabs.some(t => t.dataset.tab === key)) key = tabs[0].dataset.tab;
  tabs.forEach(t => {
    const on = t.dataset.tab === key;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on);
    t.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll('.tab-panel[data-tab]').forEach(panel => { panel.hidden = panel.dataset.tab !== key; });
  history.replaceState(null, '', '#' + key);
}
document.querySelectorAll('.tab[data-tab]').forEach((t, i, tabs) => {
  t.onclick = () => openTab(t.dataset.tab);
  t.onkeydown = e => {  // Left/Right move between tabs, as screen-reader users expect
    const step = {ArrowRight: 1, ArrowLeft: -1}[e.key];
    if (!step) return;
    const next = tabs[(i + step + tabs.length) % tabs.length];
    openTab(next.dataset.tab);
    next.focus();
  };
});
openTab(location.hash.slice(1));

// The server name (from /api/health, open before sign-in) goes in the tab title and header.
fetch('/api/health').then(r => r.json()).then(h => {
  document.querySelectorAll('[data-version]').forEach(el => { el.textContent = 'v' + h.version; });
  const name = (h.server_name || '').trim();
  if (!name) return;
  document.title = document.title.replace('Caspar Controller', name);
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

// A suggested NDI name: this controller's name, then the output's. Characters names can't
// contain (+ & # and the like, which break trigger URLs) are left out.
function suggestedNdi(server, name) {
  return `${server} ${name}`.replace(/[^\w .()'-]/g, '').replace(/\s+/g, ' ').trim();
}

// Styled dropdowns. Each <select> gets a button and a list; the select stays hidden underneath
// as the source of truth, so page code that sets .value, rewrites the options or listens for
// change keeps working unchanged. Lists over 10 options get a search box.
const Dropdown = (() => {
  const proto = HTMLSelectElement.prototype;
  const native = {value: Object.getOwnPropertyDescriptor(proto, 'value'),
    selectedIndex: Object.getOwnPropertyDescriptor(proto, 'selectedIndex')};
  const CHEVRON = '<svg class="dd-chevron" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  let open = null;  // {select, button, list, items, active, timer}

  // A disabled option with no value is a heading, like "─── Test Patterns ───".
  const isHeading = o => o.disabled && !o.value;
  const startsWith = (item, key) => item.textContent.toLowerCase().startsWith(key.toLowerCase());

  function label(sel) {
    const button = sel._dropdown, o = sel.options[sel.selectedIndex];
    button.innerHTML = `<span class="dd-label${o && !o.value ? ' dd-placeholder' : ''}">${escapeHtml(o ? o.text : '')}</span>${CHEVRON}`;
    button.disabled = sel.disabled;
    button.title = sel.title;
  }

  function enhance(sel) {
    if (sel._dropdown || sel.multiple) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'dd-button';
    button.style.cssText = sel.style.cssText;  // inline widths, flex and the like
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    sel._dropdown = button;
    sel.classList.add('dd-native');
    sel.tabIndex = -1;
    sel.after(button);
    for (const [prop, d] of Object.entries(native)) {
      Object.defineProperty(sel, prop, {configurable: true, get() { return d.get.call(this); },
        set(v) { d.set.call(this, v); label(this); }});
    }
    new MutationObserver(() => { label(sel); if (open && open.select === sel) render(); })
      .observe(sel, {childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'title']});
    sel.addEventListener('change', () => label(sel));
    button.addEventListener('click', () => (open && open.select === sel ? close() : show(sel)));
    button.addEventListener('keydown', e => {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); e.stopPropagation(); show(sel); }
    });
    label(sel);
  }

  function render() {
    const {select, list} = open;
    const search = list.querySelector('.dd-search');
    const query = search ? search.value.trim().toLowerCase() : '';
    const box = list.querySelector('.dd-options');
    box.innerHTML = '';
    open.items = [];
    [...select.options].forEach((o, i) => {
      if (isHeading(o)) {
        if (!query) box.insertAdjacentHTML('beforeend', `<div class="dd-group">${escapeHtml(o.text.replace(/─/g, '').trim())}</div>`);
        return;
      }
      if (query && !o.text.toLowerCase().includes(query)) return;
      const item = document.createElement('div');
      item.className = 'dd-option' + (o.disabled ? ' dd-disabled' : '') + (o.value ? '' : ' dd-placeholder');
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(i === select.selectedIndex));
      item.textContent = o.text;
      item.dataset.index = i;
      if (!o.disabled) {
        item.addEventListener('mousedown', e => e.preventDefault());  // keep focus in the search box
        item.addEventListener('click', () => choose(i));
        item.addEventListener('mousemove', () => activate(open.items.indexOf(item), false));
        open.items.push(item);
      }
      box.appendChild(item);
    });
    if (!open.items.length) box.innerHTML = '<div class="dd-empty">No matches</div>';
    const current = open.items.findIndex(it => Number(it.dataset.index) === select.selectedIndex);
    activate(current >= 0 ? current : 0, true);
  }

  function activate(n, scroll) {
    if (!open.items.length) return;
    open.active = Math.max(0, Math.min(n, open.items.length - 1));
    open.items.forEach((it, i) => it.classList.toggle('active', i === open.active));
    if (scroll) open.items[open.active].scrollIntoView({block: 'nearest'});
  }

  // Fixed to the viewport, so a card or a scrolling tab panel can't clip it; opens upwards
  // when there's more room above.
  function place() {
    const {button, list} = open;
    const r = button.getBoundingClientRect();
    const below = innerHeight - r.bottom - 12, above = r.top - 12;
    const up = below < 240 && above > below;
    list.style.minWidth = r.width + 'px';
    list.style.left = Math.max(8, Math.min(r.left, innerWidth - list.offsetWidth - 8)) + 'px';
    list.style.maxHeight = Math.min(340, up ? above : below) + 'px';
    list.style.top = up ? '' : r.bottom + 4 + 'px';
    list.style.bottom = up ? innerHeight - r.top + 4 + 'px' : '';
  }

  function show(sel) {
    close();
    const button = sel._dropdown;
    const list = document.createElement('div');
    list.className = 'dd-list';
    list.setAttribute('role', 'listbox');
    const searchable = sel.options.length > 10;
    list.innerHTML = (searchable ? '<input class="dd-search" type="text" placeholder="Search…" aria-label="Search">' : '')
      + '<div class="dd-options"></div>';
    document.body.appendChild(list);
    button.setAttribute('aria-expanded', 'true');
    // Live updates can rebuild a card: close if this select goes, and follow it if it moves.
    open = {select: sel, button, list, items: [], active: 0,
      timer: setInterval(() => (sel.isConnected ? place() : close()), 250)};
    render();
    place();
    const search = list.querySelector('.dd-search');
    if (search) { search.addEventListener('input', render); search.focus(); }
  }

  function choose(i) {
    const {select, button} = open;
    close();
    button.focus();
    if (select.selectedIndex === i) return;
    native.selectedIndex.set.call(select, i);
    select.dispatchEvent(new Event('change', {bubbles: true}));  // runs inline onchange too
  }

  function close() {
    if (!open) return;
    clearInterval(open.timer);
    open.list.remove();
    open.button.setAttribute('aria-expanded', 'false');
    open = null;
  }

  document.addEventListener('keydown', e => {
    if (!open) return;
    const typing = e.target.classList && e.target.classList.contains('dd-search');
    const keys = {
      ArrowDown: () => activate(open.active + 1, true),
      ArrowUp: () => activate(open.active - 1, true),
      Enter: () => open.items.length && choose(Number(open.items[open.active].dataset.index)),
      Escape: () => { const b = open.button; close(); b.focus(); },
      Tab: close,
    };
    if (keys[e.key]) {
      if (e.key !== 'Tab') e.preventDefault();
      e.stopPropagation();  // a page's own Enter handler mustn't fire too
      keys[e.key]();
    } else if (!typing && e.key.length === 1) {  // type-ahead: the next option starting with it
      const next = open.items.findIndex((it, i) => i > open.active && startsWith(it, e.key));
      const n = next >= 0 ? next : open.items.findIndex(it => startsWith(it, e.key));
      if (n >= 0) activate(n, true);
    }
  }, true);
  document.addEventListener('mousedown', e => {
    if (open && !open.list.contains(e.target) && !open.button.contains(e.target)) close();
  });
  addEventListener('resize', close);
  document.addEventListener('scroll', e => { if (open && !open.list.contains(e.target)) close(); }, true);

  // Every select on the page, now and as pages add them.
  const enhanceIn = root => root.querySelectorAll && root.querySelectorAll('select').forEach(enhance);
  enhanceIn(document);
  new MutationObserver(records => records.forEach(r => r.addedNodes.forEach(n => {
    if (n.tagName === 'SELECT') enhance(n); else enhanceIn(n);
  }))).observe(document.body, {childList: true, subtree: true});
  return {close};
})();
