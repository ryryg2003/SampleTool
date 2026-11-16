/* settings.js: Settings view (collection folder, port, playback defaults, density,
   Freesound key, app info), row density for every VTable, the version line in the
   keyboard dialog, and the "already open" toast after a second launch. */
'use strict';

/* ---------- Density: one place decides row height for every virtual table ---------- */
const Density = {
  tables: new Set(),
  delta: { comfortable: 0, compact: -5 },
  rh(base) { return Math.max(20, base + (this.delta[App.density] || 0)); },
  apply(d) {
    App.density = d === 'compact' ? 'compact' : 'comfortable';
    document.body.classList.toggle('density-compact', App.density === 'compact');
    document.body.classList.toggle('density-comfortable', App.density !== 'compact');
    document.documentElement.style.setProperty('--density-rh-delta', (this.delta[App.density] || 0) + 'px');
    this.tables.forEach((t) => { try { t.render(); } catch (e) { /* table not ready */ } });
    App.emit('density', App.density);
  },
};
App.density = 'comfortable';
App.applyDensity = (d) => Density.apply(d);
if (typeof VTable === 'function' && !VTable.prototype._densityWrapped) {
  const render = VTable.prototype.render;
  VTable.prototype.render = function () {
    Density.tables.add(this);
    const rh = Density.rh(this.o.rowHeight);
    if (rh !== this.rh) {
      const top = this.body.scrollTop / this.rh; // keep the same row at the top
      this.rh = rh;
      this.el.style.setProperty('--rh', rh + 'px');
      this.body.scrollTop = Math.round(top * rh);
    }
    return render.apply(this, arguments);
  };
  VTable.prototype._densityWrapped = true;
}

/* ---------- Fixture routes (fixture mode only; memory only) ---------- */
window.FixtureExt = window.FixtureExt || [];
(function () {
  const defaults = { root: '', port: 8765, volume: 80, auto_audition: false, density: 'comfortable' };
  let saved = Object.assign({}, defaults);
  window.FixtureExt.push((method, path, query, body, st, { ok, err }) => {
    const payload = () => ({
      settings: Object.assign({}, saved, { root: st.root }), defaults,
      info: { version: '1.0.0', data_dir: st.data_dir, settings_file: st.data_dir + '\\settings.json',
              log_file: st.data_dir + '\\logs\\app.log', port_in_use: 8765, idle_exit: 180 },
      busy: !!(st.progress && st.progress.running), job: null,
    });
    if (path === '/api/settings' && method === 'GET') return ok(payload());
    if (path === '/api/settings' && method === 'POST') {
      const b = body || {};
      if ('root' in b) return err(400, 'Change the collection folder with Switch and scan.');
      if ('port' in b && !(Number.isInteger(+b.port) && +b.port >= 1024 && +b.port <= 65535)) return err(400, 'Port must be between 1024 and 65535');
      if ('volume' in b && !(Number.isInteger(+b.volume) && +b.volume >= 0 && +b.volume <= 100)) return err(400, 'Default volume must be between 0 and 100');
      if ('density' in b && !['compact', 'comfortable'].includes(b.density)) return err(400, 'Density must be compact or comfortable');
      for (const k of Object.keys(b)) if (k in defaults) saved[k] = k === 'port' || k === 'volume' ? +b[k] : b[k];
      return ok(payload());
    }
    if (path === '/api/focus' && method === 'POST') return ok({ ok: true, focus_seq: 1 });
    return null;
  });
})();

/* ---------- Settings view ---------- */
const Settings = {
  data: null,
  busy: false,
  touched: { volume: false, auto: false },

  init() {
    const v = $('#view-settings');
    v.innerHTML = `
      <header class="view-head">
        <h1 id="settings-h">Settings</h1>
        <p class="view-sub">Saved in the app's data folder. Nothing here changes your sample files.</p>
      </header>
      <div class="set-body">
        <section class="panel set-sec" aria-labelledby="set-col-h">
          <h2 class="set-h" id="set-col-h">Collection folder</h2>
          <label class="field-label" for="set-root">Folder that holds your sample packs</label>
          <div class="set-row">
            <input id="set-root" class="input input-path" type="text" spellcheck="false" autocomplete="off">
            <button class="btn btn-primary" id="set-switch" type="button">Switch and scan</button>
          </div>
          <p class="set-help" id="set-root-help">Switching starts a read-only scan of the new folder. The plan and any approval for the current folder are cleared.</p>
          <p class="note note-warn" id="set-busy" hidden></p>
        </section>

        <section class="panel set-sec" aria-labelledby="set-play-h">
          <h2 class="set-h" id="set-play-h">Playback</h2>
          <div class="set-grid">
            <label class="field-label" for="set-vol">Default volume</label>
            <div class="set-ctl">
              <input id="set-vol" type="range" min="0" max="100" step="1" class="set-range" aria-describedby="set-vol-v">
              <output id="set-vol-v" class="mono set-out" for="set-vol">80</output>
            </div>
            <span class="field-label">Arrow keys</span>
            <div class="set-ctl">
              <label class="check"><input id="set-auto" type="checkbox"> Auto-audition when moving with the arrow keys</label>
            </div>
          </div>
          <p class="set-help">Used when the app opens. Changes you make in the player bar last until the window closes.</p>
        </section>

        <section class="panel set-sec" aria-labelledby="set-disp-h">
          <h2 class="set-h" id="set-disp-h">Display</h2>
          <div class="set-grid">
            <span class="field-label" id="set-dens-l">Density</span>
            <div class="set-ctl">
              <div class="seg" role="radiogroup" aria-labelledby="set-dens-l" id="set-density">
                <button type="button" role="radio" data-d="comfortable" aria-checked="true">Comfortable</button>
                <button type="button" role="radio" data-d="compact" aria-checked="false">Compact</button>
              </div>
              <span class="hint" id="set-dens-hint">Row height in lists</span>
            </div>
          </div>
        </section>

        <section class="panel set-sec" aria-labelledby="set-port-h">
          <h2 class="set-h" id="set-port-h">Startup</h2>
          <div class="set-grid">
            <label class="field-label" for="set-port">Port</label>
            <div class="set-ctl">
              <input id="set-port" class="input mono set-port" type="number" min="1024" max="65535" step="1" inputmode="numeric">
              <button class="btn btn-small" id="set-port-save" type="button">Save port</button>
              <span class="hint" id="set-port-now"></span>
            </div>
          </div>
          <p class="set-help">Applies the next time you start the app. Use 1024 to 65535.</p>
          <p class="set-msg err" id="set-port-err" role="alert" hidden></p>
        </section>

        <section class="panel set-sec" aria-labelledby="set-fs-h">
          <h2 class="set-h" id="set-fs-h">Freesound</h2>
          <div id="set-fs"><p class="dim">Checking…</p></div>
        </section>

        <section class="panel set-sec" aria-labelledby="set-about-h">
          <h2 class="set-h" id="set-about-h">About</h2>
          <dl class="set-kv" id="set-about"></dl>
        </section>
      </div>`;
    segKeys($('#set-density'));
    $('#set-switch').addEventListener('click', () => this.confirmSwitch());
    $('#set-root').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); this.confirmSwitch(); } });
    $('#set-root').addEventListener('input', () => { this.rootEdited = true; });
    const vol = $('#set-vol');
    vol.addEventListener('input', () => { $('#set-vol-v').textContent = vol.value; });
    vol.addEventListener('change', () => this.save({ volume: +vol.value }, 'Default volume saved', true));
    $('#set-auto').addEventListener('change', (ev) => this.save({ auto_audition: ev.target.checked }, 'Auto-audition default saved', true));
    $$('#set-density [role=radio]').forEach((b) => b.addEventListener('click', () => {
      this.renderDensity(b.dataset.d);
      Density.apply(b.dataset.d);
      this.save({ density: b.dataset.d }, b.dataset.d === 'compact' ? 'Compact rows' : 'Comfortable rows');
    }));
    $('#set-port-save').addEventListener('click', () => this.savePort());
    $('#set-port').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); this.savePort(); } });
    this.buildDialog();
    App.on('state', (s) => {
      if (s && s.version) $('#app-version').textContent = s.version;
      if (s && !this.rootEdited) $('#set-root').value = s.root || '';
      if (s && s.progress) this.setBusy(s.progress);
    });
  },

  async afterInit() {
    // Mark player controls the user touches this session; saved defaults never override them.
    if (Player.vol) Player.vol.addEventListener('input', () => { this.touched.volume = true; });
    if (Player.auto) Player.auto.addEventListener('change', () => { this.touched.auto = true; });
    if (Player.toggleAuto && !Player._autoWrapped) {
      const t = Player.toggleAuto;
      Player.toggleAuto = function () { Settings.touched.auto = true; return t.apply(this, arguments); };
      Player._autoWrapped = true;
    }
    if (typeof Shell === 'object' && Shell.renderStatus && !Shell._settingsWrapped) {
      const rs = Shell.renderStatus;
      Shell.renderStatus = function (p, err) { const r = rs.apply(this, arguments); if (p && !err) Settings.onProgress(p); return r; };
      Shell._settingsWrapped = true;
    }
    try {
      this.data = await API.get('/api/settings');
      this.applyStartup(this.data.settings);
      this.render();
    } catch (e) { /* older server without settings: keep the player's own values */ }
  },

  /* Defaults from the server, applied once at start unless the user already changed them. */
  applyStartup(s) {
    if (!s) return;
    Density.apply(s.density);
    if (!this.touched.volume && Player.vol && Number.isFinite(+s.volume)) {
      Player.vol.value = s.volume; Player.audio.volume = s.volume / 100;
    }
    if (!this.touched.auto && Player.auto) Player.auto.checked = !!s.auto_audition;
  },

  onProgress(p) {
    if (p.focus_seq !== undefined) {
      if (this.focusSeq !== undefined && p.focus_seq !== this.focusSeq) {
        toast('Sample Organizer is already open. This is the window.');
        try { window.focus(); } catch (e) { /* ignore */ }
      }
      this.focusSeq = p.focus_seq;
    }
    this.setBusy(p);
  },

  setBusy(p) {
    const busy = !!(p && p.running);
    this.busy = busy;
    const b = $('#set-switch'); if (!b) return;
    b.disabled = busy;
    const n = $('#set-busy');
    n.hidden = !busy;
    if (busy) n.textContent = `A task is running (${(JOB_LABEL[p.job] || p.job || 'task').toLowerCase()}). You can switch folders when it finishes or after you cancel it.`;
  },

  async show() {
    this.rootEdited = false;
    if (App.state) $('#set-root').value = App.state.root || '';
    this.render();
    try { this.data = await API.get('/api/settings'); this.render(); }
    catch (e) { $('#set-about').innerHTML = `<dt>Error</dt><dd class="err">${esc(e.message)}</dd>`; }
    this.loadFreesound();
    if (Shell.progress) this.setBusy(Shell.progress);
  },

  renderDensity(d) {
    $$('#set-density [role=radio]').forEach((b) => b.setAttribute('aria-checked', b.dataset.d === d ? 'true' : 'false'));
  },

  render() {
    const d = this.data; if (!d) return;
    const s = d.settings, i = d.info || {};
    $('#set-vol').value = s.volume; $('#set-vol-v').textContent = s.volume;
    $('#set-auto').checked = !!s.auto_audition;
    this.renderDensity(s.density);
    if (document.activeElement !== $('#set-port')) $('#set-port').value = s.port;
    $('#set-port-now').textContent = i.port_in_use ? `Running on ${i.port_in_use} now` : '';
    if (i.version) $('#app-version').textContent = i.version;
    const row = (k, v, actions) => `<dt>${esc(k)}</dt><dd><span class="mono set-path" title="${esc(v)}">${esc(v || '')}</span>${actions || ''}</dd>`;
    const copyBtn = (v) => v ? `<button class="btn btn-small btn-ghost" type="button" data-copy="${esc(v)}">Copy</button>` : '';
    const showBtn = (v) => v ? `<button class="btn btn-small btn-ghost" type="button" data-reveal="${esc(v)}">Show in Explorer</button>` : '';
    $('#set-about').innerHTML =
      row('Version', i.version) +
      row('App data folder', i.data_dir, showBtn(i.data_dir) + copyBtn(i.data_dir)) +
      row('Log file', i.log_file, showBtn(i.log_file) + copyBtn(i.log_file)) +
      row('Settings file', i.settings_file, copyBtn(i.settings_file)) +
      `<dt>Auto quit</dt><dd>${i.idle_exit ? `After ${Math.round(i.idle_exit / 60 * 10) / 10} min with no window open, never while a task runs` : 'Off (the app keeps running until you close its console)'}</dd>`;
    $$('[data-copy]', $('#set-about')).forEach((b) => b.addEventListener('click', () => copyText(b.dataset.copy)));
    $$('[data-reveal]', $('#set-about')).forEach((b) => b.addEventListener('click', () => reveal({ path: b.dataset.reveal })));
  },

  async save(partial, okMsg, playerToo) {
    try {
      this.data = await API.post('/api/settings', partial);
      this.render();
      if (playerToo) {
        // An explicit save is also what the user wants to hear right now.
        if ('volume' in partial && Player.vol) { Player.vol.value = partial.volume; Player.audio.volume = partial.volume / 100; Store.set('volume', partial.volume); }
        if ('auto_audition' in partial && Player.auto) { Player.auto.checked = !!partial.auto_audition; Store.set('auto', !!partial.auto_audition); }
      }
      if (okMsg) toast(okMsg);
      return true;
    } catch (e) { toast('Could not save: ' + e.message, 'err'); return false; }
  },

  async savePort() {
    const el = $('#set-port'), errEl = $('#set-port-err');
    const n = Number(el.value);
    if (!Number.isInteger(n) || n < 1024 || n > 65535) {
      errEl.textContent = 'Enter a whole number from 1024 to 65535.'; errEl.hidden = false; el.setAttribute('aria-invalid', 'true'); el.focus(); return;
    }
    errEl.hidden = true; el.removeAttribute('aria-invalid');
    if (await this.save({ port: n })) toast(`Port ${n} saved. It applies the next time you start the app.`);
  },

  /* ---------- Freesound key (endpoints owned by the Freesound add-on) ---------- */
  async loadFreesound() {
    const box = $('#set-fs');
    let s;
    try { s = await API.get('/api/freesound/status'); }
    catch (e) {
      box.innerHTML = e.status === 404
        ? '<p class="dim" id="set-fs-na">Freesound add-on not available in this version.</p>'
        : `<p class="err">Could not check Freesound: ${esc(e.message)}</p>`;
      return;
    }
    const oauth = s.oauth || {};
    box.innerHTML = `
      <p class="set-fs-status"><span class="set-dot ${s.has_key ? 'on' : ''}" aria-hidden="true"></span>
        ${s.has_key ? `API key saved <span class="mono dim">${esc(s.key_hint || '')}</span>` : 'No API key saved'}
        ${s.online === false ? '<span class="pill">Offline</span>' : ''}
        ${oauth.available ? `<span class="dim">· Account ${oauth.connected ? 'connected' : 'not connected'}</span>` : ''}</p>
      <label class="field-label" for="set-fs-key">API key</label>
      <div class="set-row">
        <input id="set-fs-key" class="input mono set-key" type="password" autocomplete="off" spellcheck="false" placeholder="${s.has_key ? 'Enter a new key to replace it' : 'Paste your Freesound API key'}">
        <button class="btn" id="set-fs-save" type="button">Save key</button>
        ${s.has_key ? '<button class="btn btn-danger-ghost" id="set-fs-clear" type="button">Remove key</button>' : ''}
      </div>
      <p class="set-help">The key is stored only in the app's data folder and is sent only to freesound.org.</p>`;
    $('#set-fs-save').addEventListener('click', () => this.saveKey($('#set-fs-key').value.trim()));
    $('#set-fs-key').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); this.saveKey($('#set-fs-key').value.trim()); } });
    const c = $('#set-fs-clear'); if (c) c.addEventListener('click', () => this.saveKey(''));
  },
  async saveKey(key) {
    try {
      const r = await API.post('/api/freesound/key', { key });
      toast(r.has_key ? 'Freesound key saved' : 'Freesound key removed');
      this.loadFreesound();
    } catch (e) { toast('Could not save the key: ' + e.message, 'err'); }
  },

  /* ---------- Switch collection (reuses POST /api/scan {root}) ---------- */
  buildDialog() {
    const d = document.createElement('dialog');
    d.className = 'dlg'; d.id = 'set-switch-dlg'; d.setAttribute('aria-labelledby', 'set-switch-h');
    d.innerHTML = `<form method="dialog" class="dlg-body">
        <h2 id="set-switch-h">Switch collection folder?</h2>
        <dl class="set-kv">
          <dt>Current</dt><dd class="mono set-path" id="set-sw-old"></dd>
          <dt>New</dt><dd class="mono set-path" id="set-sw-new"></dd>
        </dl>
        <p>The plan and any approval for the current folder are cleared. A read-only scan of the new folder starts right away. Nothing in either folder is moved or changed.</p>
        <p class="dlg-err" id="set-sw-err" hidden></p>
        <div class="dlg-actions"><button class="btn" value="cancel">Cancel</button><button class="btn btn-primary" value="ok" id="set-sw-ok">Switch and scan</button></div>
      </form>`;
    document.body.appendChild(d);
    d.addEventListener('close', () => { if (d.returnValue === 'ok') this.doSwitch(this.pendingRoot); });
  },
  confirmSwitch() {
    const root = $('#set-root').value.trim().replace(/^"|"$/g, '');
    if (!root) { toast('Enter the folder that holds your sample packs', 'err'); $('#set-root').focus(); return; }
    if (this.busy) { toast('A task is running. Switch when it finishes.', 'err'); return; }
    this.pendingRoot = root;
    $('#set-sw-old').textContent = (App.state && App.state.root) || '(none)';
    $('#set-sw-new').textContent = root;
    $('#set-sw-err').hidden = true;
    const d = $('#set-switch-dlg'); d.returnValue = ''; d.showModal();
    $('#set-sw-ok').focus();
  },
  async doSwitch(root) {
    try {
      await API.post('/api/scan', { root });
      this.rootEdited = false;
      toast('Switched. Scanning the new folder (read-only).');
      Shell.pollSoon();
      await App.refreshState();
      Shell.go('scan');
    } catch (e) {
      toast(e.status === 409 ? 'Can’t switch now: ' + e.message : 'Could not switch: ' + e.message, 'err');
    }
  },
};

App.features.push({
  init() { App.registerView('settings', Settings, 'Settings'); Settings.init(); },
  afterInit() { return Settings.afterInit(); },
});
