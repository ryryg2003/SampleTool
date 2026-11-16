/* findsounds.js: Find Sounds view. Search Freesound (CC0 by default) through the local
   server, audition the MP3 preview, open the sound page, download into _Freesound.
   The browser never calls freesound.org; the API key stays on the server. */
'use strict';

const FS_INSTRUMENTS = ['kick', 'snare', 'clap', 'hihat', 'cymbal', 'tom', 'percussion', '808', 'bass', 'synth', 'pad', 'lead',
  'piano', 'guitar', 'strings', 'brass', 'vocal', 'fx', 'riser', 'impact', 'foley', 'ambience', 'texture'];
const FS_KEYS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const FS_SORTS = [['score', 'Best match'], ['downloads_desc', 'Most downloaded'], ['rating_desc', 'Top rated'], ['created_desc', 'Newest'], ['duration_asc', 'Shortest']];
const FS_LIC = { cc0: ['CC0', 'Creative Commons 0: no attribution needed, commercial use OK'], by: ['CC BY', 'Attribution: credit the author when you release music that uses it'] };

const FindSounds = {
  started: false,
  status: null,
  res: null,
  page: 1,
  busy: false,
  err: null,
  saved: Store.get('fs.saved', {}),
  attribution: Store.get('fs.attr', false),
  mode: Store.get('fs.mode', ''),
  playing: null,

  show() {
    if (!this.started) { this.started = true; this.render(); }
    this.refreshStatus();
  },

  render() {
    const sel = (id, label, opts) => `<select class="input" id="${id}" aria-label="${label}"><option value="">${label}</option>${opts}</select>`;
    $('#view-find').innerHTML = `
      <header class="view-head fs-head">
        <div>
          <h1 id="find-h">Find Sounds</h1>
          <p class="view-sub">Search Freesound for free sounds. Downloads land in <code>_Freesound</code> inside your packs folder; scan and review to file them into your library.</p>
        </div>
        <div class="fs-quota" id="fs-quota" aria-live="polite"></div>
      </header>
      <div class="fs-body">
        <div class="fs-main">
          <div id="fs-nokey" hidden></div>
          <div id="fs-searchui">
            <form class="fs-search" id="fs-form" role="search">
              <div class="search search-lg"><svg aria-hidden="true"><use href="#i-search"/></svg><input class="input" id="fs-q" type="search" placeholder="Search Freesound: kick, vinyl crackle, rain, 808…" aria-label="Search Freesound" autocomplete="off" spellcheck="false"><kbd>/</kbd></div>
              <button class="btn btn-primary" id="fs-go" type="submit">Search</button>
            </form>
            <div class="filters fs-filters" id="fs-filters" aria-label="Filters">
              ${sel('fs-tag', 'Instrument', FS_INSTRUMENTS.map((t) => `<option value="${t}">${t}</option>`).join(''))}
              <select class="input" id="fs-type" aria-label="Loop or one-shot"><option value="">Loop / one-shot</option><option value="oneshot">One-shot (≤ 3 s)</option><option value="loop">Loop (tagged loop)</option></select>
              ${sel('fs-key', 'Key', FS_KEYS.map((k) => `<option value="${k}">${k} major</option><option value="${k}m">${k} minor</option>`).join(''))}
              <span class="bpm-range"><input class="input mono" id="fs-bpm-min" type="number" min="0" max="999" placeholder="BPM" aria-label="Minimum BPM"><span aria-hidden="true">–</span><input class="input mono" id="fs-bpm-max" type="number" min="0" max="999" placeholder="max" aria-label="Maximum BPM"></span>
              <span class="bpm-range"><input class="input mono" id="fs-dur-min" type="number" min="0" step="0.1" placeholder="sec" aria-label="Minimum length in seconds"><span aria-hidden="true">–</span><input class="input mono" id="fs-dur-max" type="number" min="0" step="0.1" placeholder="max" aria-label="Maximum length in seconds"></span>
              <select class="input" id="fs-sort" aria-label="Sort">${FS_SORTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select>
            </div>
            <div class="chips-row fs-lic" aria-label="Licenses">
              <span class="fs-lic-k">License</span>
              <button type="button" class="fs-tog on" id="fs-cc0" aria-pressed="true" disabled title="${FS_LIC.cc0[1]}. Always on."><b>CC0</b> always on</button>
              <button type="button" class="fs-tog" id="fs-attr" aria-pressed="false" title="${FS_LIC.by[1]}"><b>Attribution</b> <span id="fs-attr-s">off</span></button>
              <span class="fs-nc" title="Sounds licensed Attribution NonCommercial cannot be used in released music, so the app never shows them.">NonCommercial: never shown</span>
              <span class="count mono" id="fs-count"></span>
            </div>
            <div id="fs-banner"></div>
            <div class="fs-results panel" id="fs-results" aria-live="polite"></div>
            <div class="fs-pager" id="fs-pager" hidden>
              <button class="btn btn-small" id="fs-prev">Previous page</button>
              <span class="mono dim" id="fs-page"></span>
              <button class="btn btn-small" id="fs-next">Next page</button>
            </div>
          </div>
        </div>
        <aside class="fs-side panel" id="fs-side" aria-label="Download settings"></aside>
      </div>
      <audio id="fs-audio" preload="none"></audio>`;
    this.audio = $('#fs-audio');
    $('#fs-form').addEventListener('submit', (ev) => { ev.preventDefault(); this.search(1); });
    ['#fs-tag', '#fs-type', '#fs-key', '#fs-sort'].forEach((s) => $(s).addEventListener('change', () => { this.markSet(); if (this.res || $('#fs-q').value.trim()) this.search(1); }));
    ['#fs-bpm-min', '#fs-bpm-max', '#fs-dur-min', '#fs-dur-max'].forEach((s) => $(s).addEventListener('input', debounce(() => { this.markSet(); if (this.res) this.search(1); }, 500)));
    $('#fs-attr').addEventListener('click', () => { this.attribution = !this.attribution; Store.set('fs.attr', this.attribution); this.renderLic(); if (this.res) this.search(1); });
    $('#fs-prev').addEventListener('click', () => this.search(this.page - 1));
    $('#fs-next').addEventListener('click', () => this.search(this.page + 1));
    $('#fs-results').addEventListener('click', (ev) => this.resultsClick(ev));
    this.audio.addEventListener('ended', () => { this.playing = null; this.renderRows(); });
    this.audio.addEventListener('error', () => { if (this.playing !== null) { toast('Could not play this preview.', 'err'); this.playing = null; this.renderRows(); } });
    App.on('player', () => this.stopAudio());
    App.on('playstate', (p) => { if (p) this.stopAudio(); });
    window.addEventListener('focus', () => { if (App.view === 'find' && this.waitingOAuth) this.refreshStatus(); });
    this.renderLic();
    this.renderResults();
  },

  markSet() { $$('#fs-filters .input').forEach((el) => el.classList.toggle('is-set', !!el.value && el.id !== 'fs-sort')); },

  renderLic() {
    const b = $('#fs-attr');
    b.classList.toggle('on', this.attribution);
    b.setAttribute('aria-pressed', this.attribution ? 'true' : 'false');
    $('#fs-attr-s').textContent = this.attribution ? 'on' : 'off';
  },

  params(page) {
    const v = (s) => $(s).value.trim();
    return { q: v('#fs-q'), tag: v('#fs-tag'), type: v('#fs-type'), key: v('#fs-key'), bpm_min: v('#fs-bpm-min'), bpm_max: v('#fs-bpm-max'),
      dur_min: v('#fs-dur-min'), dur_max: v('#fs-dur-max'), sort: v('#fs-sort'), attribution: this.attribution ? '1' : '', page };
  },

  /* ---------- status, quota, key ---------- */
  async refreshStatus() {
    try { this.status = await API.get('/api/freesound/status'); } catch (e) { this.status = null; this.statusErr = e.message; }
    this.renderStatus();
    return this.status;
  },

  renderStatus() {
    const s = this.status;
    const noKey = !s || !s.has_key;
    $('#fs-nokey').hidden = !noKey;
    $('#fs-searchui').hidden = noKey;
    if (noKey) this.renderNoKey();
    this.renderQuota();
    this.renderSide();
    if (this.waitingOAuth && s && s.oauth && s.oauth.connected) { this.waitingOAuth = false; toast('Freesound account connected. Original downloads are on.'); }
  },

  renderQuota() {
    const s = this.status, box = $('#fs-quota');
    if (!s) { box.innerHTML = this.statusErr ? `<span class="err small">${esc(this.statusErr)}</span>` : ''; return; }
    const q = s.quota || {};
    const used = q.day_used || 0, lim = q.day_limit || 2000;
    const pct = Math.min(100, used / lim * 100);
    const online = s.online === false ? '<span class="fs-led off" title="Last request could not reach freesound.org"></span>Offline' : s.online ? '<span class="fs-led on"></span>Online' : '<span class="fs-led"></span>Idle';
    box.innerHTML = `
      <div class="fs-q-row"><span class="fs-q-k">Today</span><span class="mono" id="fs-q-day">${Fmt.num(used)} / ${Fmt.num(lim)}</span><span class="fs-q-k">requests</span><span class="fs-q-on">${online}</span></div>
      <div class="meter fs-q-meter" role="meter" aria-label="Daily Freesound requests used" aria-valuemin="0" aria-valuemax="${lim}" aria-valuenow="${used}"><i style="width:${pct}%"></i></div>
      <div class="fs-q-row small dim"><span class="mono" id="fs-q-min">${q.minute_remaining ?? '—'}</span>&nbsp;left this minute · <span class="mono">${q.downloads_day_used || 0}/${q.downloads_day_limit || 500}</span>&nbsp;downloads</div>
      ${q.retry_after_s ? `<div class="fs-q-row fs-q-wait">Rate limited, try again in <span class="mono">${q.retry_after_s}</span> s</div>` : ''}`;
  },

  renderNoKey() {
    const box = $('#fs-nokey');
    if (box.dataset.r) return;
    box.dataset.r = '1';
    box.innerHTML = `
      <div class="panel fs-empty">
        <h2 class="sub-h">Connect Freesound to search</h2>
        <p>Freesound is a free library of Creative Commons sounds. Searching and previewing need a free API key from your Freesound account. The key stays on this PC in the app's data folder; it is never sent to this page or stored in the app's files.</p>
        <ol class="fs-steps">
          <li>Sign in at freesound.org and open <a href="https://freesound.org/apiv2/apply" target="_blank" rel="noopener noreferrer">freesound.org/apiv2/apply</a>.</li>
          <li>Create a credential and copy its <b>Client secret / API key</b>.</li>
          <li>Paste it here, or in <a href="#settings" id="fs-to-settings">Settings</a>.</li>
        </ol>
        <form class="fs-keyform" id="fs-keyform">
          <input class="input mono" id="fs-key-in" type="password" autocomplete="off" spellcheck="false" placeholder="API key" aria-label="Freesound API key">
          <button class="btn btn-primary" type="submit">Save key</button>
        </form>
        <p class="hint">Without a key or internet connection the rest of the app works as usual.</p>
      </div>`;
    $('#fs-to-settings').addEventListener('click', (ev) => { ev.preventDefault(); location.hash = 'settings'; });
    $('#fs-keyform').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const key = $('#fs-key-in').value.trim();
      if (!key) { $('#fs-key-in').focus(); return; }
      try {
        const r = await API.post('/api/freesound/key', { key });
        $('#fs-key-in').value = '';
        toast('Freesound key saved (' + (r.key_hint || 'hidden') + ').');
        delete box.dataset.r;
        await this.refreshStatus();
        $('#fs-q').focus();
      } catch (e) { toast('Could not save key: ' + e.message, 'err'); }
    });
  },

  dlMode() {
    const s = this.status, conn = !!(s && s.oauth && s.oauth.connected);
    if (this.mode === 'original' && conn) return 'original';
    if (this.mode === 'preview') return 'preview';
    return conn ? 'original' : 'preview';
  },

  renderSide() {
    const s = this.status, side = $('#fs-side');
    if (!s || !s.has_key) { side.innerHTML = this.sideAbout(); return; }
    const o = s.oauth || {}, mode = this.dlMode();
    const root = (App.state && App.state.root) || '';
    const dest = root ? root.replace(/[\\/]+$/, '') + (root.includes('/') ? '/' : '\\') + '_Freesound' : '_Freesound';
    side.innerHTML = `
      <h2 class="sub-h">Downloads</h2>
      <div class="fs-modes" role="radiogroup" aria-label="Download quality">
        <label class="fs-mode${mode === 'original' ? ' on' : ''}"><input type="radio" name="fs-mode" value="original" ${mode === 'original' ? 'checked' : ''} ${o.connected ? '' : 'disabled'}> <span><b>Original file</b><br><span class="dim small">Full quality. Needs your Freesound account connected below.</span></span></label>
        <label class="fs-mode${mode === 'preview' ? ' on' : ''}"><input type="radio" name="fs-mode" value="preview" ${mode === 'preview' ? 'checked' : ''}> <span><b>MP3 preview</b> <span class="fs-lq">lower quality</span><br><span class="dim small">About 128 kbps. Needs only the API key. Fine for sketching; get the original before you release.</span></span></label>
      </div>
      <div class="fs-acct">
        <h3 class="fs-h3">Freesound account</h3>
        ${o.connected ? `<p><span class="fs-led on"></span>Connected. Original downloads enabled.</p><button class="btn btn-small btn-ghost" id="fs-disc">Disconnect</button>` :
        o.available ? `<p class="small">Freesound requires you to sign in (OAuth2) before the app can fetch original files.</p>
          <button class="btn btn-small" id="fs-connect">Connect account…</button>
          <details class="fs-adv"><summary>Redirect URL and code sign-in</summary>
            <p class="small dim">Your credential's redirect URL must be exactly:</p>
            <div class="path mono fs-redir">${esc(o.redirect_uri || '')}</div>
            <p class="small dim">If you registered Freesound's own code page instead, sign in here and paste the code it shows:</p>
            <button class="btn btn-small" id="fs-manual">Sign in for a code…</button>
            <form class="fs-keyform" id="fs-codeform"><input class="input mono" id="fs-code" placeholder="Code" aria-label="Authorization code" autocomplete="off"><button class="btn btn-small" type="submit">Use code</button></form>
          </details>` :
        `<p class="small">To download original files, add your credential's <b>Client ID</b> (same page as the API key). Until then, downloads save the MP3 preview.</p>
          <form class="fs-keyform" id="fs-cidform"><input class="input mono" id="fs-cid" placeholder="Client ID" aria-label="Freesound client ID" autocomplete="off"><button class="btn btn-small" type="submit">Save</button></form>`}
      </div>
      <div class="fs-where">
        <h3 class="fs-h3">Where files go</h3>
        <div class="path mono">${esc(dest)}\\&lt;uploader&gt;\\</div>
        <p class="small dim">Each sound gets a <code>.license.txt</code> next to it with the source, author and license. Nothing is overwritten and nothing enters <code>Samples</code> until you scan, review and approve.</p>
      </div>
      ${this.sideAbout(true)}
      <p class="small"><a href="#settings" id="fs-side-settings">API key and account settings</a></p>`;
    $$('input[name=fs-mode]', side).forEach((r) => r.addEventListener('change', () => { this.mode = r.value; Store.set('fs.mode', r.value); this.renderSide(); }));
    const on = (sel, fn) => { const el = $(sel, side); if (el) el.addEventListener(el.tagName === 'FORM' ? 'submit' : 'click', fn); };
    on('#fs-side-settings', (ev) => { ev.preventDefault(); location.hash = 'settings'; });
    on('#fs-disc', async () => { try { await API.post('/api/freesound/oauth/disconnect', {}); this.refreshStatus(); } catch (e) { toast(e.message, 'err'); } });
    on('#fs-connect', () => this.connect('local'));
    on('#fs-manual', () => this.connect('manual'));
    on('#fs-codeform', async (ev) => {
      ev.preventDefault();
      try { await API.post('/api/freesound/oauth/code', { code: $('#fs-code').value.trim() }); toast('Freesound account connected.'); this.refreshStatus(); } catch (e) { toast('Sign-in failed: ' + e.message, 'err'); }
    });
    on('#fs-cidform', async (ev) => {
      ev.preventDefault();
      const cid = $('#fs-cid').value.trim(); if (!cid) return;
      try { await API.post('/api/freesound/key', { client_id: cid }); toast('Client ID saved.'); this.refreshStatus(); } catch (e) { toast(e.message, 'err'); }
    });
  },

  sideAbout(compact) {
    return `<div class="fs-about${compact ? ' compact' : ''}"><h3 class="fs-h3">Licenses</h3>
      <p class="small"><span class="fs-badge fs-cc0">CC0</span> No credit needed, commercial use OK. <span class="fs-badge fs-by">CC BY</span> Credit the author (the license file has the line to copy). NonCommercial sounds are never shown.</p>
      <p class="small dim">A CC0 label can still be wrong (re-uploads happen). Prefer building blocks over recognisable loops.</p></div>`;
  },

  async connect(mode) {
    try {
      const r = await API.post('/api/freesound/oauth/start', { mode });
      this.waitingOAuth = true;
      window.open(r.authorize_url, '_blank', 'noopener');
      toast(mode === 'local' ? 'Approve access in the browser tab that opened, then come back here.' : 'Approve access, then paste the code Freesound shows.');
    } catch (e) { toast('Could not start sign-in: ' + e.message, 'err'); }
  },

  /* ---------- search ---------- */
  async search(page) {
    if (page < 1) return;
    const seq = (this.seq = (this.seq || 0) + 1);
    this.busy = true; this.err = null; this.page = page;
    this.renderResults();
    try {
      const r = await API.get('/api/freesound/search', this.params(page));
      if (seq !== this.seq) return;
      this.res = r;
      if (this.status) this.status.quota = r.quota || this.status.quota;
      if (this.status) this.status.online = true;
    } catch (e) {
      if (seq !== this.seq) return;
      this.err = e;
      if (e.status === 409) { await this.refreshStatus(); }
    }
    this.busy = false;
    if (seq !== this.seq) return;
    if (this.err && this.err.status !== 409) await this.refreshStatus(); else this.renderQuota();
    this.renderResults();
    $('#fs-results').scrollTop = 0;
  },

  renderBanner() {
    const e = this.err, box = $('#fs-banner');
    if (!e) { box.innerHTML = ''; clearInterval(this.tick); return; }
    if (e.status === 429) {
      const q = (this.status && this.status.quota) || {};
      let left = q.retry_after_s || 60;
      box.innerHTML = `<div class="banner fs-banner fs-b-wait" role="alert"><strong>Rate limited.</strong> <span>Try again in <b class="mono" id="fs-wait">${left}</b> s. Used today: <span class="mono">${Fmt.num(q.day_used || 0)} / ${Fmt.num(q.day_limit || 2000)}</span>. The app pauses all Freesound requests until then.</span></div>`;
      clearInterval(this.tick);
      this.tick = setInterval(() => { left -= 1; const el = $('#fs-wait'); if (!el || left <= 0) { clearInterval(this.tick); if (el) { this.err = null; this.renderResults(); this.refreshStatus(); } return; } el.textContent = left; }, 1000);
    } else if (e.status === 503 || e.status === 0) {
      box.innerHTML = `<div class="banner fs-banner fs-b-off" role="alert"><strong>Offline.</strong> <span>${esc(e.message)} Your library, scanning and review keep working without a connection.</span><button class="btn btn-small" id="fs-retry">Try again</button></div>`;
      $('#fs-retry').addEventListener('click', () => this.search(this.page));
    } else {
      box.innerHTML = `<div class="banner fs-banner fs-b-err" role="alert"><strong>Search failed.</strong> <span>${esc(e.message)}</span><button class="btn btn-small" id="fs-retry">Try again</button></div>`;
      $('#fs-retry').addEventListener('click', () => this.search(this.page));
    }
  },

  renderResults() {
    this.renderBanner();
    const box = $('#fs-results'), r = this.res;
    const pager = $('#fs-pager');
    if (this.busy) {
      box.innerHTML = '<div class="fs-state"><div class="loading" aria-label="Searching"><i></i><i></i><i></i><span>Searching Freesound…</span></div></div>';
      return;
    }
    if (!r) {
      pager.hidden = true; $('#fs-count').textContent = '';
      box.innerHTML = this.err ? '' : `<div class="fs-state"><div><strong>Search for a sound</strong><p>Type what you need and press Enter. Results show CC0 sounds only${this.attribution ? ' plus Attribution' : ''}; switch on <em>Attribution</em> above for more.</p></div></div>`;
      return;
    }
    $('#fs-count').textContent = Fmt.num(r.count) + (r.count === 1 ? ' sound' : ' sounds');
    pager.hidden = !(r.next || r.page > 1);
    $('#fs-prev').disabled = r.page <= 1;
    $('#fs-next').disabled = !r.next;
    const pages = Math.max(1, Math.ceil(r.count / (r.page_size || 30)));
    $('#fs-page').textContent = `Page ${r.page} of ${Fmt.num(pages)}`;
    if (!r.results.length) {
      box.innerHTML = `<div class="fs-state"><div><strong>No sounds found</strong><p>${r.dropped ? `${r.dropped} result(s) were hidden because of their license. ` : ''}Try fewer filters, another word${this.attribution ? '' : ', or switch on Attribution'}.</p></div></div>`;
      return;
    }
    box.innerHTML = `<div class="fs-row fs-th" aria-hidden="true"><span></span><span>Name</span><span>Uploader</span><span class="num">Length</span><span class="num">BPM</span><span>Key</span><span>License</span><span></span></div>
      <ul class="fs-list" aria-label="Freesound results">${r.results.map((x) => this.rowHtml(x)).join('')}</ul>
      ${r.dropped ? `<p class="hint fs-dropped">${r.dropped} result(s) on this page hidden: license not allowed.</p>` : ''}`;
  },

  renderRows() { if (this.res && !this.busy) { const st = $('#fs-results').scrollTop; this.renderResults(); $('#fs-results').scrollTop = st; } },

  rowHtml(x) {
    const lic = FS_LIC[x.license] || [x.license, ''];
    const on = this.playing === x.id;
    const saved = this.saved[x.id];
    return `<li class="fs-row${on ? ' is-playing' : ''}" data-id="${x.id}">
      <button type="button" class="ib fs-play" data-act="play" aria-label="${on ? 'Stop' : 'Play'} preview of ${esc(x.name)}" title="${x.has_preview ? 'Preview (MP3)' : 'No preview'}" ${x.has_preview ? '' : 'disabled'}><svg><use href="#i-${on ? 'stop' : 'play'}"/></svg></button>
      <span class="fs-name"><span class="nm ellip" title="${esc(x.name)}">${esc(x.name)}</span><span class="fs-tags ellip">${esc((x.tags || []).slice(0, 6).join(' · '))}</span></span>
      <span class="ellip fs-user" title="${esc(x.username)}">${esc(x.username)}</span>
      <span class="num mono">${x.duration_s != null ? Fmt.dur(x.duration_s) : ''}</span>
      <span class="num mono">${x.bpm ? esc(Fmt.bpm(x.bpm)) : ''}</span>
      <span class="mono fs-keyc ellip">${esc(x.key || '')}</span>
      <span><a class="fs-badge fs-${esc(x.license)}" href="${esc(x.license_url || '#')}" target="_blank" rel="noopener noreferrer" title="${esc(lic[1])}">${esc(lic[0])}</a></span>
      <span class="fs-acts">
        <a class="btn btn-small btn-ghost" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer" title="Open the sound page in your browser">Open on Freesound</a>
        ${saved ? `<button type="button" class="btn btn-small fs-saved" data-act="reveal" title="${esc(saved)}">Saved</button>` : `<button type="button" class="btn btn-small" data-act="dl" title="${this.dlMode() === 'original' ? 'Download the original file' : 'Download the MP3 preview (lower quality)'}">${this.dlMode() === 'original' ? 'Download' : 'Download MP3'}</button>`}
      </span>
    </li>`;
  },

  resultsClick(ev) {
    const b = ev.target.closest('[data-act]'); if (!b) return;
    const li = b.closest('.fs-row'); const id = +li.dataset.id;
    const x = this.res && this.res.results.find((r) => r.id === id); if (!x) return;
    if (b.dataset.act === 'play') this.togglePlay(x);
    else if (b.dataset.act === 'dl') this.download(x, b);
    else if (b.dataset.act === 'reveal') reveal({ path: this.saved[id] });
  },

  stopAudio() {
    if (this.audio && !this.audio.paused) this.audio.pause();
    if (this.playing !== null) { this.playing = null; this.renderRows(); }
  },

  togglePlay(x) {
    if (this.playing === x.id) { this.stopAudio(); return; }
    if (API.fixture) { toast('Fixture mode: previews play with a real Freesound key.'); this.playing = x.id; this.renderRows(); return; }
    try { if (Player.audio && !Player.audio.paused) Player.stop(); } catch (e) { /* player not ready */ }
    this.audio.src = x.preview_url;
    this.playing = x.id;
    const p = this.audio.play();
    if (p && p.catch) p.catch((e) => { if (e.name !== 'AbortError') { toast('Preview failed: ' + e.message, 'err'); this.playing = null; this.renderRows(); } });
    this.renderRows();
  },

  async download(x, btn) {
    const mode = this.dlMode();
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
      const r = await API.post('/api/freesound/download', { id: x.id, mode });
      this.saved[x.id] = r.path; Store.set('fs.saved', this.saved);
      if (this.status && r.quota) this.status.quota = r.quota;
      this.renderQuota();
      toast(`Saved ${r.name}${mode === 'preview' ? ' (MP3 preview)' : ''} with its license file to _Freesound. Scan to add it to your library.`);
    } catch (e) {
      if (e.status === 429) { this.err = e; await this.refreshStatus(); this.renderBanner(); }
      toast(e.status === 409 ? e.message : 'Download failed: ' + e.message, 'err');
    }
    this.renderRows();
  },
};

App.features.push({
  init() {
    App.registerView('find', FindSounds, 'Find Sounds');
    App.keyHandlers.push((ev) => {
      if (App.view !== 'find' || ev.key !== '/' || ev.ctrlKey || ev.altKey || ev.metaKey) return false;
      const el = $('#fs-q'); if (!el || el.offsetParent === null) return false;
      ev.preventDefault(); el.focus(); el.select(); return true;
    });
  },
});

/* ---------- Fixture endpoints (?fixture=1) ---------- */
(function () {
  const fx = { key: '', cid: '', connected: false, used: 0, dl: 0, min: 50 };
  const users = ['field_rec', 'drumdrops', 'klankbeeld', 'modularsamples', 'InspectorJ', 'Erokia', 'qubodup', 'LudwigMueller'];
  const words = ['Kick', 'Snare', 'Clap', 'Hihat closed', 'Vinyl crackle', 'Rain on window', 'Sub 808', 'Pad swell', 'Riser', 'Impact hit', 'Foley step', 'Tom low',
    'Shaker loop', 'Bass pluck', 'Synth stab', 'Vocal chop', 'Glass tap', 'Door slam', 'Ambience room', 'Noise sweep'];
  const KEYS = ['C minor', 'A minor', 'F major', 'G major', 'D minor', null];
  const all = [];
  for (let i = 0; i < 64; i++) {
    const w = words[i % words.length];
    const lic = i % 9 === 4 ? 'nc' : i % 3 === 1 ? 'by' : 'cc0';
    const loop = /loop|Rain|Ambience|Pad/.test(w);
    all.push({ id: 700100 + i, name: `${w} ${String(i + 1).padStart(2, '0')}.wav`, username: users[i % users.length], duration_s: loop ? 4 + (i % 7) * 1.7 : 0.15 + (i % 9) * 0.21,
      license: lic, license_name: lic, license_url: lic === 'cc0' ? 'https://creativecommons.org/publicdomain/zero/1.0/' : 'https://creativecommons.org/licenses/by/4.0/',
      bpm: loop ? 80 + (i % 6) * 10 : null, key: loop ? KEYS[i % KEYS.length] : null, tags: [w.split(' ')[0].toLowerCase(), loop ? 'loop' : 'one-shot', 'drum'].slice(0, 3),
      url: `https://freesound.org/people/${users[i % users.length]}/sounds/${700100 + i}/`, has_preview: true, preview_url: `/api/freesound/preview/${700100 + i}` });
  }
  const quota = () => ({ minute_remaining: fx.min, day_used: fx.used, day_limit: 2000, downloads_day_used: fx.dl, downloads_day_limit: 500, retry_after_s: fx.wait || null });
  const status = () => ({ has_key: !!fx.key, key_hint: fx.key ? fx.key.slice(0, 4) + '…' + fx.key.slice(-4) : null,
    oauth: { connected: fx.connected, available: !!fx.cid, client_id_set: !!fx.cid, redirect_uri: 'http://127.0.0.1:8765/api/freesound/oauth/callback' }, quota: quota(), online: fx.used ? true : null });
  window.FixtureExt.push((method, p, q, body, st, h) => {
    if (!p.startsWith('/api/freesound/')) return null;
    if (p === '/api/freesound/status') return h.ok(status());
    if (p === '/api/freesound/key' && method === 'POST') {
      if ('key' in body) fx.key = String(body.key || '').trim();
      if ('client_id' in body) { fx.cid = String(body.client_id || '').trim(); fx.connected = false; }
      return h.ok({ ok: true, has_key: !!fx.key, key_hint: status().key_hint });
    }
    if (p === '/api/freesound/search') {
      if (!fx.key) return { status: 409, body: { error: 'Add your Freesound API key to search.', code: 'no_key' } };
      const text = (q.get('q') || '').toLowerCase();
      if (text === 'ratelimit') { fx.wait = 42; return { status: 429, body: { error: 'Rate limited by Freesound, try again in 42 s.', code: 'rate_limited', retry_after_s: 42, quota: quota() } }; }
      if (text === 'offline') return { status: 503, body: { error: 'Could not reach freesound.org (no network).', code: 'offline' } };
      fx.wait = 0; fx.used++; fx.min = Math.max(0, fx.min - 1);
      const attr = q.get('attribution') === '1', tag = q.get('tag'), type = q.get('type');
      const words2 = text.split(/\s+/).filter(Boolean);
      let rs = all.filter((x) => x.license === 'cc0' || (attr && x.license === 'by'));
      rs = rs.filter((x) => words2.every((w) => (x.name + ' ' + x.tags.join(' ')).toLowerCase().includes(w)));
      if (tag) rs = rs.filter((x) => x.tags.includes(tag) || x.name.toLowerCase().includes(tag));
      if (type === 'loop') rs = rs.filter((x) => x.tags.includes('loop'));
      if (type === 'oneshot') rs = rs.filter((x) => x.duration_s <= 3);
      const page = +(q.get('page') || 1), size = 30;
      return h.ok({ count: rs.length, page, page_size: size, next: page * size < rs.length, previous: page > 1, results: rs.slice((page - 1) * size, page * size), dropped: 0, quota: quota() });
    }
    if (p === '/api/freesound/download' && method === 'POST') {
      const x = all.find((s) => s.id === +body.id);
      if (!x) return h.err(404, 'Not found');
      if (body.mode === 'original' && !fx.connected) return { status: 409, body: { error: 'Connect your Freesound account to download original files.', code: 'oauth_required' } };
      fx.dl++;
      const name = body.mode === 'preview' ? x.name.replace(/\.\w+$/, '') + ' (preview).mp3' : x.name;
      const path = `${st.root}\\_Freesound\\${x.username}\\${name}`;
      return h.ok({ ok: true, id: x.id, mode: body.mode, name, path, sidecar: path + '.license.txt', folder: `${st.root}\\_Freesound\\${x.username}`, quota: quota() });
    }
    if (p === '/api/freesound/oauth/start' && method === 'POST') return h.ok({ authorize_url: 'about:blank', state: 'fixture', redirect_uri: status().oauth.redirect_uri });
    if (p === '/api/freesound/oauth/code' && method === 'POST') { fx.connected = true; return h.ok({ ok: true, connected: true }); }
    if (p === '/api/freesound/oauth/disconnect' && method === 'POST') { fx.connected = false; return h.ok({ ok: true, connected: false }); }
    return null;
  });
})();
