/* firstrun.js: the folder picker dialog (GET /api/folders, read-only), the
   three-step welcome on the Scan screen for a first run (pick folder, scan,
   Try Beat Starter), and the time-left wording for scans. */
'use strict';

/* ---------- Time left: "about 3 min left" ---------- */
function fmtLeft(s) {
  if (s === null || s === undefined || !Number.isFinite(+s)) return '';
  s = Math.max(0, +s);
  if (s < 10) return 'a few seconds left';
  if (s < 60) return `about ${Math.ceil(s / 10) * 10} s left`;
  if (s < 3600) return `about ${Math.max(1, Math.round(s / 60))} min left`;
  const h = Math.floor(s / 3600), m = Math.round((s - h * 3600) / 60);
  return `about ${h} h${m ? ' ' + m + ' min' : ''} left`;
}
function progressLeft(p) {
  if (!p || !p.running) return '';
  return p.eta_s !== null && p.eta_s !== undefined ? fmtLeft(p.eta_s) : 'working out time left…';
}

/* ---------- Fixture routes (fixture mode only; memory only) ---------- */
window.FixtureExt = window.FixtureExt || [];
(function () {
  const HOME = 'C:\\Users\\Producer';
  const TREE = {
    '': { folders: ['C:\\', 'D:\\'], audio: 0 },
    'C:\\': { folders: ['Program Files', 'Users', 'Windows'], audio: 0 },
    'D:\\': { folders: ['Music', 'Projects'], audio: 0 },
    'D:\\Music': { folders: ['Sample Packs', 'Stems'], audio: 2 },
    'D:\\Music\\Sample Packs': { folders: ['Halcyon Drums - Breaks Cabinet', 'Lowtide Sounds - Night Bus', 'Mothership Audio - 808 Gravity'], audio: 0 },
    'C:\\Users': { folders: ['Producer', 'Public'], audio: 0 },
    [HOME]: { folders: ['Desktop', 'Documents', 'Downloads', 'Music'], audio: 0 },
    [HOME + '\\Music']: { folders: ['Ableton', 'Sample Packs'], audio: 3 },
    [HOME + '\\Music\\Sample Packs']: { folders: ['Kiln Records - Fired Keys', 'Static Garden - Tape Hiss Botany'], audio: 0 },
  };
  const join = (a, b) => (a.endsWith('\\') ? a : a + '\\') + b;
  const parent = (p) => { if (/^[A-Z]:\\$/.test(p)) return ''; const i = p.lastIndexOf('\\'); return i <= 2 ? p.slice(0, 3) : p.slice(0, i); };
  const places = [['Home', HOME], ['Music', HOME + '\\Music'], ['Documents', HOME + '\\Documents'], ['Desktop', HOME + '\\Desktop'], ['Downloads', HOME + '\\Downloads']]
    .map(([label, path]) => ({ label, path }));
  window.FixtureExt.push((method, path, q, body, st, { ok, err }) => {
    if (path !== '/api/folders' || method !== 'GET') return null;
    let p = String(q.get('path') || '').trim().replace(/^"|"$/g, '').replace(/\//g, '\\');
    if (p.length > 3) p = p.replace(/\\+$/, '');
    if (/^[a-z]:\\?$/i.test(p)) p = p[0].toUpperCase() + ':\\';
    const node = TREE[p] || (p ? { folders: [], audio: 4 } : null);
    if (p && !TREE[p] && !TREE[parent(p)]) return err(400, 'Folder not found: ' + p);
    if (!p) return ok({ path: '', parent: null, audio_files: 0, truncated: false, folders: node.folders.map((d) => ({ name: d, path: d })), places });
    return ok({ path: p, parent: parent(p), audio_files: node.audio, truncated: false, folders: node.folders.map((n) => ({ name: n, path: join(p, n) })), places });
  });
})();

/* ---------- Folder picker ---------- */
const FolderPicker = {
  cur: null,
  init() {
    const d = document.createElement('dialog');
    d.className = 'dlg dlg-folder'; d.id = 'folder-dlg'; d.setAttribute('aria-labelledby', 'fp-h');
    d.innerHTML = `<form method="dialog" class="dlg-body">
        <h2 id="fp-h">Choose your sample folder</h2>
        <p>Open folders until you are in the one that holds your sample packs, then press <em>Use this folder</em>. Nothing is changed.</p>
        <div class="fp-places" id="fp-places" role="group" aria-label="Places"></div>
        <div class="fp-bar">
          <button type="button" class="btn btn-small" id="fp-up" aria-label="Up one folder" title="Up one folder">↑ Up</button>
          <input class="input input-path mono" id="fp-path" type="text" spellcheck="false" autocomplete="off" aria-label="Folder path" placeholder="Or paste a folder path and press Enter">
          <button type="button" class="btn btn-small" id="fp-go">Go</button>
        </div>
        <ul class="fp-list" id="fp-list" aria-label="Folders" aria-live="polite"></ul>
        <p class="fp-info" id="fp-info"></p>
        <p class="dlg-err" id="fp-err" role="alert" hidden></p>
        <div class="dlg-actions"><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn btn-primary" type="button" id="fp-use" disabled>Use this folder</button></div>
      </form>`;
    document.body.appendChild(d);
    this.dlg = d;
    $('#fp-up').addEventListener('click', () => { if (this.cur) this.load(this.cur.parent || ''); });
    $('#fp-go').addEventListener('click', () => this.load($('#fp-path').value));
    $('#fp-path').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); this.load($('#fp-path').value); } });
    $('#fp-use').addEventListener('click', () => { if (this.cur && this.cur.path) { this.result = this.cur.path; d.close('ok'); } });
    $('#fp-list').addEventListener('click', (ev) => { const b = ev.target.closest('[data-path]'); if (b) this.load(b.dataset.path); });
    $('#fp-places').addEventListener('click', (ev) => { const b = ev.target.closest('[data-path]'); if (b) this.load(b.dataset.path); });
    d.addEventListener('close', () => { const r = d.returnValue === 'ok' ? this.result : null; const res = this.resolve; this.resolve = null; if (res) res(r); });
  },
  /* Opens the picker; resolves to the chosen folder path, or null when cancelled. */
  open(start) {
    if (!this.dlg) this.init();
    this.result = null; this.dlg.returnValue = '';
    $('#fp-err').hidden = true;
    this.dlg.showModal();
    this.load(start || '', true);
    return new Promise((res) => { this.resolve = res; });
  },
  async load(path, fallback) {
    const err = $('#fp-err');
    let j;
    try { j = await API.get('/api/folders', { path: (path || '').trim() }); err.hidden = true; }
    catch (e) {
      if (fallback && path) return this.load('', false);
      err.textContent = e.message; err.hidden = false; $('#fp-path').focus(); return;
    }
    this.cur = j;
    $('#fp-path').value = j.path;
    $('#fp-up').disabled = j.parent === null;
    $('#fp-use').disabled = !j.path;
    $('#fp-places').innerHTML = `<button type="button" class="fp-place${j.path === '' ? ' on' : ''}" data-path="">This PC</button>` +
      (j.places || []).map((p) => `<button type="button" class="fp-place${p.path === j.path ? ' on' : ''}" data-path="${esc(p.path)}" title="${esc(p.path)}">${esc(p.label)}</button>`).join('');
    $('#fp-list').innerHTML = j.folders.length
      ? j.folders.map((f) => `<li><button type="button" class="fp-item" data-path="${esc(f.path)}" title="${esc(f.path)}"><svg aria-hidden="true"><use href="#i-folder"/></svg><span class="ellip">${esc(f.name)}</span><svg class="fp-chev" aria-hidden="true"><use href="#i-chev"/></svg></button></li>`).join('')
      : `<li class="fp-none">${j.path ? 'No folders inside this one.' : 'No drives found.'}</li>`;
    const n = j.folders.length;
    $('#fp-info').textContent = !j.path ? 'Pick a drive or a place to start.'
      : `${j.audio_files ? Fmt.num(j.audio_files) + ' audio file' + (j.audio_files === 1 ? '' : 's') + ' right here · ' : ''}${Fmt.num(n)} folder${n === 1 ? '' : 's'}${j.truncated ? ' (list cut short)' : ''}. Packs inside subfolders are found too.`;
    $('#fp-list').scrollTop = 0;
  },
};

/* ---------- First-run welcome on the Scan screen ---------- */
const Welcome = {
  active: false,
  init() {
    $('#wl-pick').addEventListener('click', () => this.pick());
    $('#wl-scan').addEventListener('click', () => Scan.start());
    $('#wl-starter').addEventListener('click', () => { this.finish(); Shell.go('starter'); });
    App.on('state', (s) => {
      // A true first run: nothing scanned and no folder saved or passed in (--root).
      if (s && !this.seen) { this.seen = true; this.active = !s.has_inventory && !s.root; }
      this.render();
    });
  },
  async pick() {
    const cur = $('#scan-root').value.trim();
    const p = await FolderPicker.open(cur);
    if (!p) return;
    $('#scan-root').value = p;
    Scan.edited = true;
    this.render();
    $('#wl-scan').focus();
  },
  finish() { this.active = false; this.render(); },
  render() {
    const box = $('#welcome'); if (!box) return;
    const s = App.state || {};
    const on = this.active;
    box.hidden = !on;
    $('#scan-form').hidden = on;
    $('#scan-eyebrow').textContent = on ? 'Welcome' : 'Scan';
    $('#scan-h').textContent = on ? 'Set up in three steps' : 'Scan';
    $('#scan-sub').textContent = on ? 'Pick the folder with your sample packs. The app only reads it; nothing in it is moved or changed.'
      : 'Read every file under your packs folder and work out what each sound is.';
    if (!on) return;
    const p = Shell.progress || {};
    const scanning = !!(p.running && p.job === 'scan');
    const folder = $('#scan-root').value.trim();
    const scanned = !!s.has_inventory;
    const step = !folder ? 1 : (scanned && !scanning ? 3 : 2);
    [1, 2, 3].forEach((n) => {
      const li = $('#wl-' + n);
      li.classList.toggle('is-done', n < step);
      li.classList.toggle('is-current', n === step);
      if (n === step) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
    });
    $('#wl-folder').textContent = folder || 'The folder that holds your sample packs. Subfolders are included.';
    $('#wl-folder').classList.toggle('mono', !!folder);
    $('#wl-folder').classList.toggle('ellip', !!folder);
    $('#wl-folder').title = folder;
    $('#wl-pick').textContent = folder ? 'Change folder…' : 'Choose folder…';
    $('#wl-pick').classList.toggle('btn-primary', step === 1);
    const wscan = $('#wl-scan');
    wscan.disabled = !folder || scanning;
    wscan.classList.toggle('btn-primary', step === 2 && !scanning);
    wscan.textContent = scanning ? 'Scanning…' : (scanned ? 'Scan again' : 'Start scan');
    $('#wl-scan-text').textContent = scanning
      ? [p.step && p.steps ? `Step ${p.step} of ${p.steps}` : '', progressLeft(p)].filter(Boolean).join(' · ')
      : scanned ? (s.counts ? `Done: ${Fmt.num(s.counts.files)} files found. Nothing was changed.` : 'Done. Nothing was changed.')
        : 'Reads every file to find tempo, key and type. Nothing in the folder is changed.';
    const ws = $('#wl-starter');
    ws.disabled = !scanned || scanning;
    ws.classList.toggle('btn-primary', step === 3);
  },
};

App.features.push({
  init() { FolderPicker.init(); },
  afterInit() {
    Welcome.init();
    $('#scan-pick').addEventListener('click', async () => {
      const p = await FolderPicker.open($('#scan-root').value.trim());
      if (p) { $('#scan-root').value = p; Scan.edited = true; $('#scan-start').focus(); }
    });
    const sp = $('#set-pick');
    if (sp) sp.addEventListener('click', async () => {
      const p = await FolderPicker.open($('#set-root').value.trim());
      if (p) { $('#set-root').value = p; Settings.rootEdited = true; $('#set-switch').focus(); }
    });
  },
});
