// Console side panel: each instance's CasparCG output, live. Any page that loads this gets it.

const Console = (() => {
  const store = {
    get: k => { try { return localStorage.getItem('console_' + k); } catch (e) { return null; } },
    set: (k, v) => { try { localStorage.setItem('console_' + k, v); } catch (e) {} },
  };
  let instances = [], current = Number(store.get('tab')) || null, lines = [];
  let follow = true, stream = null;
  const seen = {};  // inst id -> console line count already read

  document.body.insertAdjacentHTML('beforeend', `
    <button id="console-toggle" class="btn btn-secondary btn-sm">Console</button>
    <aside id="console-panel" hidden>
      <div class="console-bar">
        <div id="console-tabs"></div>
        <button class="btn btn-secondary btn-sm" id="console-close" title="Close">×</button>
      </div>
      <div class="console-bar">
        <input id="console-search" placeholder="Search">
        <button class="btn btn-secondary btn-sm" id="console-follow">Pause</button>
        <button class="btn btn-secondary btn-sm" id="console-copy">Copy</button>
        <button class="btn btn-secondary btn-sm" id="console-download">Download</button>
        <button class="btn btn-secondary btn-sm" id="console-pin" title="Keep open beside the page">Pin</button>
      </div>
      <pre id="console-lines"></pre>
      <form id="console-command" class="console-bar">
        <input id="console-input" placeholder="Type into CasparCG's console (signed-in operators)">
        <button class="btn btn-primary btn-sm">Send</button>
      </form>
    </aside>`);
  const $ = id => document.getElementById(id);
  const panel = $('console-panel');

  function visible() {
    const q = $('console-search').value.toLowerCase();
    return q ? lines.filter(l => l.toLowerCase().includes(q)) : lines;
  }

  function render() {
    const box = $('console-lines');
    box.textContent = visible().join('\n');
    if (follow) box.scrollTop = box.scrollHeight;
  }

  function dotClass(status) {
    if (status === 'live') return 'dot-live';
    if (status === 'failed' || status === 'stopped') return 'dot-' + status;
    return 'dot-starting';  // restarting (n/5), starting
  }

  function renderTabs() {
    $('console-tabs').innerHTML = instances.map(inst => {
      const unread = inst.id === current ? 0 : Math.max(0, inst.console_lines - (seen[inst.id] || 0));
      return `<button class="console-tab ${inst.id === current ? 'active' : ''}" data-id="${inst.id}">
        <span class="dot ${dotClass(inst.status)}"></span>${escapeHtml(inst.name)}
        ${inst.status !== 'live' ? `<small>${escapeHtml(inst.status)}</small>` : ''}
        ${unread ? `<span class="console-unread">${unread > 999 ? '999+' : unread}</span>` : ''}</button>`;
    }).join('');
  }

  function connect() {
    if (stream) stream.close();
    lines = [];
    render();
    // Without a tab open, the stream still brings status for the tab dots.
    stream = new EventSource('/api/events' + (current ? '?console=' + current : ''));
    stream.addEventListener('status', e => {
      instances = JSON.parse(e.data).instances;
      instances.forEach(i => { if (!(i.id in seen)) seen[i.id] = i.console_lines; });  // unread: since opening
      if (!current && instances.length) return select(instances[0].id);
      const mine = instances.find(i => i.id === current);
      if (mine) seen[current] = mine.console_lines;
      renderTabs();
    });
    stream.addEventListener('console', e => {
      lines = lines.concat(JSON.parse(e.data).lines).slice(-5000);
      render();
    });
    stream.onopen = () => { lines = []; };  // a reconnect resends everything still kept
  }

  function select(id) {
    current = id;
    store.set('tab', id);
    connect();
  }

  function setOpen(open) {
    panel.hidden = !open;
    $('console-toggle').hidden = open;
    store.set('open', open ? '1' : '');
    if (open && !stream) connect();
    if (!open && stream) { stream.close(); stream = null; }
  }

  function setPinned(pinned) {
    document.body.classList.toggle('console-pinned', pinned);
    $('console-pin').classList.toggle('btn-primary', pinned);
    store.set('pinned', pinned ? '1' : '');
  }

  $('console-toggle').onclick = () => setOpen(true);
  $('console-close').onclick = () => setOpen(false);
  $('console-tabs').onclick = e => { const tab = e.target.closest('.console-tab'); if (tab) select(Number(tab.dataset.id)); };
  $('console-search').oninput = render;
  $('console-follow').onclick = () => {
    follow = !follow;
    $('console-follow').textContent = follow ? 'Pause' : 'Follow';
    render();
  };
  $('console-copy').onclick = () => navigator.clipboard.writeText(visible().join('\n'))
    .then(() => toast('Console copied', 'success'));
  $('console-download').onclick = () => {
    const name = (instances.find(i => i.id === current) || {}).name || 'console';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\r\n')], {type: 'text/plain'}));
    a.download = name + '.log';
    a.click();
    URL.revokeObjectURL(a.href);
  };
  $('console-pin').onclick = () => setPinned(!document.body.classList.contains('console-pinned'));
  $('console-command').onsubmit = e => {
    e.preventDefault();
    const command = $('console-input').value.trim();
    if (!command || !current) return;
    api('/api/instance/' + current + '/console', 'POST', {command})
      .then(() => { $('console-input').value = ''; })
      .catch(err => toast(err.message, 'error'));
  };
  $('console-lines').onscroll = () => {
    const box = $('console-lines');
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
    if (atEnd !== follow) { follow = atEnd; $('console-follow').textContent = follow ? 'Pause' : 'Follow'; }
  };

  setPinned(store.get('pinned') === '1');
  setOpen(store.get('open') === '1');
  return {open: id => { setOpen(true); select(id); }};
})();
