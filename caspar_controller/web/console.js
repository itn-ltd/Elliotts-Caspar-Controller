// Console side panel: each instance's CasparCG log, live. Any page that loads this gets it.

const Console = (() => {
  const store = {
    get: k => { try { return localStorage.getItem('console_' + k); } catch (e) { return null; } },
    set: (k, v) => { try { localStorage.setItem('console_' + k, v); } catch (e) {} },
  };
  let instances = [], current = Number(store.get('tab')) || null, lines = [];
  let follow = true, listening = false;
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

  function setFollow(on) {
    follow = on;
    $('console-follow').textContent = on ? 'Pause' : 'Follow';
  }

  function dotClass(status) {
    if (status === 'live' || status === 'stopped' || status === 'failed') return 'dot-' + status;
    return 'dot-starting';  // crashed (1/5), hung (1/5), restarting (1/5)
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

  function onStatus(data) {
    instances = data.instances;
    instances.forEach(i => { if (!(i.id in seen)) seen[i.id] = i.console_lines; });  // unread: since opening
    // The remembered tab may be an instance that's since been deleted.
    if (instances.length && !instances.some(i => i.id === current)) return select(instances[0].id);
    const mine = instances.find(i => i.id === current);
    if (mine) seen[current] = mine.console_lines;
    renderTabs();
  }

  function listen() {
    if (listening) return;
    listening = true;
    Live.on('status', data => { if (!panel.hidden) onStatus(data); });
    Live.on('console', data => {
      if (panel.hidden) return;
      lines = lines.concat(data.lines).slice(-5000);
      render();
    });
    Live.on('open', () => { lines = []; render(); });  // a (re)connect resends what's kept
  }

  function select(id) {
    current = id;
    store.set('tab', id);
    lines = [];
    render();
    Live.follow(id);
  }

  function setOpen(open) {
    panel.hidden = !open;
    $('console-toggle').hidden = open;
    store.set('open', open ? '1' : '');
    if (open) {
      listen();
      Live.follow(current);
    } else {
      Live.follow(null);  // no console lines while closed
    }
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
  $('console-follow').onclick = () => { setFollow(!follow); render(); };
  $('console-copy').onclick = () => copyText(visible().join('\n'), 'Console copied');
  $('console-download').onclick = () => {
    const name = (instances.find(i => i.id === current) || {}).name || 'console';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\r\n')], {type: 'text/plain'}));
    a.download = name + '.log';
    a.click();
    URL.revokeObjectURL(a.href);
  };
  $('console-pin').onclick = () => setPinned(!document.body.classList.contains('console-pinned'));
  $('console-lines').onscroll = () => {
    const box = $('console-lines');
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
    if (atEnd !== follow) setFollow(atEnd);
  };

  setPinned(store.get('pinned') === '1');
  setOpen(store.get('open') === '1');
  return {open: id => { setOpen(true); select(id); }};
})();
