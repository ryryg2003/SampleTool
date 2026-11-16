/* app.js: shell (navigation, status bar, job polling), Scan and Move Log views,
   global keyboard handling, boot. */
'use strict';

/* Arrow-key movement inside a role=radiogroup segmented control. */
function segKeys(group) {
  group.addEventListener('keydown', (ev) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(ev.key)) return;
    const bs = $$('[role=radio]', group); const i = bs.indexOf(document.activeElement); if (i < 0) return;
    ev.preventDefault(); ev.stopPropagation();
    const n = bs[(i + (ev.key === 'ArrowRight' ? 1 : bs.length - 1)) % bs.length]; n.focus(); n.click();
  });
}
const PHASE_LABEL = { walking: 'Finding files', hashing: 'Fingerprinting audio', metadata: 'Reading tags', classifying: 'Classifying sounds', kits: 'Detecting kits', complete: 'Complete', cancelled: 'Cancelled', idle: 'Idle' };
const JOB_LABEL = { scan: 'Scanning', plan: 'Building plan', execute: 'Running plan', export: 'Writing reports', idle: 'Idle' };

const Shell = {
  lastRunning: false,
  lastJob: 'idle',
  init() {
    $$('.rail-btn').forEach((b) => b.addEventListener('click', () => this.go(b.dataset.view)));
    $('#btn-keys').addEventListener('click', () => $('#keys-dlg').showModal());
    window.addEventListener('hashchange', () => this.route());
  },
  go(v) { if (location.hash !== '#' + v) location.hash = v; else this.route(); },
  route() {
    let v = location.hash.replace('#', '');
    if (!['scan', 'review', 'library', 'log'].includes(v)) v = App.state && !App.state.has_inventory ? 'scan' : 'library';
    App.view = v;
    $$('.view').forEach((s) => { s.hidden = s.id !== 'view-' + v; });
    $$('.rail-btn').forEach((b) => { const on = b.dataset.view === v; b.classList.toggle('on', on); if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    document.title = { scan: 'Scan', review: 'Review', library: 'Library', log: 'Move Log' }[v] + ' · Sample Library Organizer';
    ({ scan: Scan, review: Review, library: Library, log: MoveLog })[v].show();
  },
  pollSoon() { clearTimeout(this.pt); this.pt = setTimeout(() => this.poll(), 120); },
  async poll() {
    clearTimeout(this.pt);
    let p;
    try { p = await API.get('/api/progress'); this.offline = false; }
    catch (e) { this.offline = true; this.renderStatus(null, e); this.pt = setTimeout(() => this.poll(), 4000); return; }
    this.progress = p;
    this.renderStatus(p);
    Scan.renderProgress(p);
    if (p.running) this.lastJob = p.job;
    // A fast job can start and finish between two polls; the server's job counter catches that.
    const missedJob = this.lastSeq !== undefined && p.seq !== undefined && p.seq !== this.lastSeq && !p.running;
    if (missedJob) this.lastJob = p.job;
    this.lastSeq = p.seq;
    if ((this.lastRunning || missedJob) && !p.running) {
      const job = this.lastJob;
      await App.refreshState();
      await App.refreshFacets();
      if (job === 'scan') { Library.packsLoaded = false; Library.loadPacks(); Library.table.reset(); Scan.renderSummary(); toast(p.phase === 'cancelled' ? 'Scan cancelled. Nothing was changed.' : 'Scan finished'); }
      App.emit('job-done', job);
    } else if (!this.lastRunning && p.running) {
      App.refreshState();
    }
    this.lastRunning = !!p.running;
    this.pt = setTimeout(() => this.poll(), p.running ? 400 : 4000);
  },
  renderStatus(p, err) {
    const led = $('#sb-led');
    if (err) { led.className = 'sb-led err'; $('#sb-job').textContent = 'Server not responding'; $('#sb-count').textContent = ''; return; }
    led.className = 'sb-led' + (p.running ? ' run' : '') + (p.errors && p.errors.length ? ' warn' : '');
    const phase = PHASE_LABEL[p.phase] || p.phase || '';
    $('#sb-job').textContent = p.running ? `${JOB_LABEL[p.job] || p.job}${phase ? ': ' + phase : ''}` : (p.message || 'Idle');
    $('#sb-count').textContent = p.running && p.total ? `${Fmt.num(p.done)} / ${Fmt.num(p.total)}${p.bytes_total ? '  ' + Fmt.bytes(p.bytes_done) + ' / ' + Fmt.bytes(p.bytes_total) : ''}` : '';
    const nerr = (p.errors || []).length;
    $('#sb-warn').textContent = p.warnings ? `${Fmt.num(p.warnings)} warning${p.warnings === 1 ? '' : 's'}` : '';
    $('#sb-warn').hidden = !p.warnings;
    $('#sb-err').textContent = nerr ? `${nerr} error${nerr === 1 ? '' : 's'}` : '';
    $('#sb-err').hidden = !nerr;
  },
  renderPlanStatus(s) {
    const p = s && s.plan;
    const el = $('#sb-plan');
    if (!p) { el.innerHTML = '<span class="dim">No plan</span>'; return; }
    el.innerHTML = `Plan <span class="mono">${esc(Fmt.hash8(p.plan_hash))}</span> ${p.stale ? '<b class="sb-stale">stale</b>' : p.approved ? '<b class="sb-ok">approved</b>' : '<span class="dim">not approved</span>'}`;
  },
};

/* ================= Scan ================= */
const Scan = {
  init() {
    $('#scan-form').addEventListener('submit', (ev) => { ev.preventDefault(); this.start(); });
    $('#scan-cancel').addEventListener('click', () => this.cancel());
    $('#scan-root').addEventListener('input', () => { this.edited = true; });
    App.on('state', (s) => {
      if (!this.edited) $('#scan-root').value = s.root || '';
      $('#scan-datadir').textContent = s.data_dir ? ` (${s.data_dir})` : '';
      if (App.view === 'scan') this.renderSummary();
    });
  },
  async start() {
    const root = $('#scan-root').value.trim();
    if (!root) { toast('Enter the folder that holds your sample packs', 'err'); $('#scan-root').focus(); return; }
    try {
      await API.post('/api/scan', { root });
      toast('Scan started. Nothing in this folder will be changed.');
      Shell.pollSoon();
    } catch (e) { toast(e.status === 409 ? 'Can’t start: ' + e.message : 'Scan failed to start: ' + e.message, 'err'); }
  },
  async cancel() {
    try { await API.post('/api/cancel'); toast('Cancelling…'); Shell.pollSoon(); } catch (e) { toast('Cancel failed: ' + e.message, 'err'); }
  },
  renderProgress(p) {
    const scanning = p.running && p.job === 'scan';
    const showPanel = scanning || (p.job === 'scan' && !p.running) || (this.everRan && p.phase);
    if (scanning) this.everRan = true;
    $('#scan-start').disabled = !!p.running;
    $('#scan-start').textContent = scanning ? 'Scanning…' : 'Start read-only scan';
    $('#scan-cancel').hidden = !scanning;
    $('#scan-progress').hidden = !showPanel;
    if (!showPanel) return;
    const pct = p.total ? Math.min(100, p.done / p.total * 100) : 0;
    $('#sp-phase').textContent = scanning ? (PHASE_LABEL[p.phase] || p.phase || 'Working') : (PHASE_LABEL[p.phase] || p.phase || 'Finished');
    $('#sp-count').textContent = `${Fmt.num(p.done)} / ${Fmt.num(p.total)} files`;
    $('#sp-bytes').textContent = p.bytes_total ? `${Fmt.bytes(p.bytes_done)} of ${Fmt.bytes(p.bytes_total)}` : '';
    $('#sp-bar').style.width = pct + '%';
    $('#sp-meter').setAttribute('aria-valuenow', String(Math.round(pct)));
    $('#scan-progress').classList.toggle('is-run', scanning);
    $('#sp-current').textContent = scanning ? p.current || '' : (p.message || '');
    $('#sp-current').title = p.current || '';
    $('#sp-warn').textContent = `${Fmt.num(p.warnings || 0)} warning${p.warnings === 1 ? '' : 's'}`;
    const errs = p.errors || [];
    $('#sp-err').textContent = `${errs.length} error${errs.length === 1 ? '' : 's'}`;
    $('#sp-err').classList.toggle('is-zero', !errs.length);
    $('#sp-warn').classList.toggle('is-zero', !p.warnings);
    $('#sp-errors').innerHTML = errs.map((e) => `<li><code class="ellip" title="${esc(e.path)}">${esc(e.path)}</code><span>${esc(e.error)}</span></li>`).join('');
  },
  async renderSummary() {
    const box = $('#scan-summary');
    const s = App.state;
    if (!s || !s.has_inventory) { box.innerHTML = '<div class="empty empty-scan"><strong>No scan yet.</strong><p>Point the scan at the folder that holds your packs. You will review everything before a single file moves.</p></div>'; return; }
    let packs = [];
    try { packs = await API.get('/api/packs'); } catch (e) { /* show what we have */ }
    const ss = s.scan_summary || {};
    const ps = (s.plan && s.plan.summary) || {};
    const agg = packs.reduce((a, p) => ({ files: a.files + p.files, audio: a.audio + p.audio, kits: a.kits + p.kits, bytes: a.bytes + p.bytes }), { files: 0, audio: 0, kits: 0, bytes: 0 });
    const v = (k, fallback) => (ss[k] !== undefined ? ss[k] : ps[k] !== undefined ? ps[k] : fallback);
    const planOnly = (k) => (ss[k] !== undefined || ps[k] !== undefined ? Fmt.num(v(k)) : '—');
    const card = (val, label, sub) => `<div class="card"><span class="card-v mono">${val}</span><span class="card-k">${label}</span>${sub ? `<span class="card-s mono">${sub}</span>` : ''}</div>`;
    box.innerHTML = `<h2 class="sub-h">Last scan</h2><div class="cards cards-scan">
      ${card(Fmt.num(v('total_files', agg.files)), 'files', Fmt.bytes(v('total_bytes', agg.bytes)))}
      ${card(Fmt.num(v('audio_files', agg.audio)), 'recognized audio')}
      ${card(Fmt.num(v('packs', packs.length)), 'packs')}
      ${card(Fmt.num(v('kits', agg.kits)), 'complete kits')}
      ${card(planOnly('duplicate_groups'), 'duplicate groups', ps.duplicate_bytes_avoided ? Fmt.bytes(ps.duplicate_bytes_avoided) + ' redundant' : '')}
      ${card(planOnly('unknown_formats'), 'unknown formats')}
      ${card(planOnly('collisions'), 'name collisions')}
      ${card(planOnly('held'), 'held for review')}
    </div>
    ${!ss.duplicate_groups && !s.plan ? '<p class="hint">Duplicate, collision and hold counts appear once a plan is built.</p>' : ''}
    <div class="next-step"><button class="btn btn-primary" type="button" id="scan-to-lib">Browse library</button><button class="btn" type="button" id="scan-to-review">Review plan</button></div>`;
    $('#scan-to-lib').onclick = () => Shell.go('library');
    $('#scan-to-review').onclick = () => Shell.go('review');
  },
  show() { this.renderSummary(); if (Shell.progress) this.renderProgress(Shell.progress); },
};

/* ================= Move Log ================= */
const LOG_STATUS = { done: 'Done', held: 'Held', failed: 'Failed', skipped: 'Skipped', 'already-done': 'Already done' };
const MoveLog = {
  status: '', q: '',
  init() {
    this.table = new VTable($('#log-table'), {
      label: 'Move log', rowHeight: 30, pageSize: 200,
      columns: [
        { key: 't', label: 'Time', width: '138px', cls: 'mono dim c-time', render: (r) => esc(Fmt.time(r.timestamp)) },
        { key: 's', label: 'Result', width: '96px', render: (r) => `<span class="rs rs-${esc(r.status)}">${esc(LOG_STATUS[r.status] || r.status)}</span>` },
        { key: 'a', label: 'Action', width: '66px', render: (r) => actionBadge(r.action) },
        { key: 'n', label: 'File', width: 'minmax(120px, 1fr)', title: (r) => r.source, render: (r) => `<span class="ellip">${esc(r.name || r.source)}</span>` },
        { key: 'd', label: 'Destination', width: 'minmax(140px, 1.4fr)', cls: 'c-path c-dest', title: (r) => r.dest || '', render: (r) => `<span class="ellip">${esc(relDest(r.dest) || '')}</span>` },
        { key: 'e', label: 'Error / note', width: 'minmax(120px, 1.2fr)', cls: 'c-err', title: (r) => r.error || '', render: (r) => `<span class="ellip">${esc(r.error || '')}</span>` },
        { key: 'x', label: '', sr: 'Actions', width: '52px', cls: 'c-act', render: () => '<button type="button" class="ib" data-act="copy" aria-label="Copy path" title="Copy path" tabindex="-1"><svg><use href="#i-copy"/></svg></button><button type="button" class="ib" data-act="reveal" aria-label="Show in Explorer" title="Show in Explorer" tabindex="-1"><svg><use href="#i-folder"/></svg></button>' },
      ],
      fetch: (offset, limit) => API.get('/api/log', { status: this.status, q: this.q, offset, limit }),
      onTotal: (t) => { $('#log-count').textContent = Fmt.num(t) + ' entries'; },
      onAction: (a, r) => {
        const path = r.status === 'done' || r.status === 'already-done' ? (r.dest || r.source) : r.source;
        if (a === 'copy') copyText(path); else reveal({ path });
      },
      emptyHtml: () => App.state && App.state.execution ? '<div><strong>No entries match.</strong><p>Pick another result filter or clear the search.</p></div>' : '<div><strong>Nothing has run yet.</strong><p>The log fills in when you run an approved plan from Review.</p></div>',
    });
    $$('#log-status [role=radio]').forEach((b) => b.addEventListener('click', () => {
      this.status = b.dataset.s;
      $$('#log-status [role=radio]').forEach((x) => x.setAttribute('aria-checked', x === b ? 'true' : 'false'));
      this.table.reset();
    }));
    segKeys($('#log-status'));
    $('#log-q').addEventListener('input', debounce(() => { this.q = $('#log-q').value.trim(); this.table.reset(); }, 150));
    App.on('state', (s) => { if (App.view === 'log') this.renderTop(s); });
    App.on('job-done', () => { if (this.started) this.table.reset(); });
  },
  renderTop(s) {
    const ex = s && s.execution;
    const vbox = $('#verify'), rbox = $('#reports');
    if (!ex) {
      $('#log-meta').textContent = 'What happened to each file when the approved plan ran.';
      vbox.innerHTML = '<h2 class="sub-h">Verification</h2><p class="dim">Appears after the first approved run.</p>';
      rbox.innerHTML = '<h2 class="sub-h">Reports</h2><p class="dim">Catalog CSVs are written to Samples\\Catalog after a run.</p>';
      return;
    }
    $('#log-meta').textContent = `Last run ${Fmt.time(ex.ran)}.`;
    const rs = ex.results_summary || {};
    const v = ex.verification || {};
    const known = ['storage_avoided_bytes', 'space_freed_bytes', 'unresolved'];
    const unresolved = Array.isArray(v.unresolved) ? v.unresolved : [];
    const other = Object.entries(v).filter(([k]) => !known.includes(k));
    const fmtV = (k, val) => typeof val === 'boolean' ? (val ? 'yes' : 'no') : k.includes('bytes') ? Fmt.bytes(val) : typeof val === 'number' ? Fmt.num(val) : Array.isArray(val) ? Fmt.num(val.length) : esc(String(val));
    vbox.innerHTML = `<h2 class="sub-h">Verification</h2>
      <div class="vrow">
        <div class="vstat"><span class="card-v mono">${Fmt.bytes(v.storage_avoided_bytes)}</span><span class="card-k">storage avoided (duplicates stored once)</span></div>
        <div class="vstat"><span class="card-v mono">${Fmt.bytes(v.space_freed_bytes)}</span><span class="card-k">disk space actually freed</span></div>
        <div class="vstat ${unresolved.length ? 'is-bad' : 'is-good'}"><span class="card-v mono">${Fmt.num(unresolved.length)}</span><span class="card-k">unresolved items</span></div>
      </div>
      <div class="rs-chips">${Object.entries(rs).map(([k, n]) => `<button type="button" class="rs rs-${esc(k)}" data-s="${esc(k)}">${esc(LOG_STATUS[k] || k)} <b class="mono">${Fmt.num(n)}</b></button>`).join('')}</div>
      ${v.unique_hashes !== undefined ? `<p class="dim vsum">${Fmt.num(v.unique_hashes)} unique files stored as ${Fmt.num(v.physical_files)} physical files across ${Fmt.num(v.library_paths)} library paths${v.kits_checked !== undefined ? ` · ${Fmt.num(v.kits_checked)} kits checked, ${Fmt.num(v.kits_incomplete ? v.kits_incomplete.length : 0)} incomplete` : ''}.</p>` : ''}
      ${other.length ? `<details class="raw"><summary>All verification details</summary><dl class="vlist">${other.map(([k, val]) => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd class="mono">${fmtV(k, val)}</dd>`).join('')}</dl></details>` : ''}
      ${unresolved.length ? `<details class="raw"><summary>Unresolved paths (${unresolved.length})</summary><ul class="unres">${unresolved.map((u) => `<li><code class="ellip" title="${esc(typeof u === 'string' ? u : JSON.stringify(u))}">${esc(typeof u === 'string' ? u : (u.path || JSON.stringify(u)))}</code></li>`).join('')}</ul></details>` : ''}`;
    $$('#verify .rs-chips [data-s]').forEach((b) => b.addEventListener('click', () => { const r = $(`#log-status [data-s="${b.dataset.s}"]`); if (r) r.click(); }));
    const reps = Object.entries(ex.reports || {});
    rbox.innerHTML = `<h2 class="sub-h">Reports</h2>` + (reps.length ? `<ul class="replist">${reps.map(([n, p]) => `<li><span class="rep-n">${esc(n)}</span><code class="path ellip" title="${esc(p)}">${esc(p)}</code><button type="button" class="ib" data-copy="${esc(p)}" aria-label="Copy path of ${esc(n)}" title="Copy path"><svg><use href="#i-copy"/></svg></button><button type="button" class="ib" data-rev="${esc(p)}" aria-label="Show ${esc(n)} in Explorer" title="Show in Explorer"><svg><use href="#i-folder"/></svg></button></li>`).join('')}</ul>` : '<p class="dim">No reports were written.</p>');
    $$('#reports [data-copy]').forEach((b) => b.addEventListener('click', () => copyText(b.dataset.copy)));
    $$('#reports [data-rev]').forEach((b) => b.addEventListener('click', () => reveal({ path: b.dataset.rev })));
  },
  show() {
    this.renderTop(App.state);
    if (!this.started) { this.started = true; this.table.reset(); } else this.table.refresh();
  },
};

/* ================= Global keyboard ================= */
function isTyping(el) {
  if (!el) return false;
  const t = el.tagName;
  return t === 'TEXTAREA' || t === 'SELECT' || el.isContentEditable || (t === 'INPUT' && !['checkbox', 'radio', 'range', 'button', 'submit'].includes(el.type));
}
document.addEventListener('keydown', (ev) => {
  if (document.querySelector('dialog[open]')) return; // dialogs handle their own keys (Esc closes)
  const ae = document.activeElement;
  if (isTyping(ae)) {
    if (ev.key === 'Escape') {
      if (ae.type === 'search' && ae.value) return; // native: clears the field
      ae.blur();
      if (App.view === 'library') Library.table.body.focus();
    }
    return;
  }
  if (ev.altKey && ['1', '2', '3', '4'].includes(ev.key)) { ev.preventDefault(); Shell.go(['scan', 'review', 'library', 'log'][+ev.key - 1]); return; }
  if (ev.key === '/') {
    ev.preventDefault();
    const s = { library: '#lib-q', review: '#op-q', log: '#log-q', scan: '#scan-root' }[App.view];
    const el = $(s); if (el) { el.focus(); el.select && el.select(); }
    return;
  }
  const onCtl = ae && (ae.tagName === 'BUTTON' || ae.tagName === 'SUMMARY' || (ae.tagName === 'INPUT' && ae.type !== 'range'));
  if (App.view === 'library' && Library.handleKey(ev)) return;
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === ' ' && !onCtl) {
    ev.preventDefault();
    if (App.view === 'library') {
      const r = Library.currentRow();
      if (r && (!Player.row || Player.row.id !== r.id)) Player.play(r, true); else Player.toggle();
    } else Player.toggle();
    return;
  }
  if (ev.key === 'l' || ev.key === 'L') { Player.setLoop(!Player.audio.loop); toast('Loop ' + (Player.audio.loop ? 'on' : 'off')); return; }
  if (ev.key === 'a' || ev.key === 'A') { Player.toggleAuto(); return; }
  if (ev.key === '?') { $('#keys-dlg').showModal(); return; }
  if (ev.key === 'Escape') {
    if (App.view === 'review' && Review.closeOp()) return;
    if (!Player.audio.paused) Player.stop();
  }
});

/* ================= Theme: accent preset and reduce effects ================= */
/* Both are per-browser preferences kept in localStorage (slo.accent, slo.reduceFx)
   through Store, which already swallows blocked-storage errors. */
const Theme = {
  ACCENTS: ['magenta', 'teal', 'violet', 'amber'],
  init() {
    this.setAccent(Store.get('accent', 'magenta'), false);
    this.setReduceFx(Store.get('reduceFx', false), false);
    $$('#accent-pick [role=radio]').forEach((b) => b.addEventListener('click', () => this.setAccent(b.dataset.accent, true)));
    segKeys($('#accent-pick'));
    $('#btn-effects').addEventListener('click', () => this.setReduceFx(!document.documentElement.classList.contains('reduce-fx'), true));
  },
  setAccent(name, save) {
    if (!this.ACCENTS.includes(name)) name = 'magenta';
    document.documentElement.dataset.accent = name;
    $$('#accent-pick [role=radio]').forEach((b) => b.setAttribute('aria-checked', b.dataset.accent === name ? 'true' : 'false'));
    if (save) Store.set('accent', name);
  },
  setReduceFx(on, save) {
    on = !!on;
    document.documentElement.classList.toggle('reduce-fx', on);
    $('#btn-effects').setAttribute('aria-pressed', on ? 'true' : 'false');
    if (save) Store.set('reduceFx', on);
  },
};

/* ================= Boot ================= */
(async function boot() {
  Theme.init();
  await API.init();
  if (API.fixture) {
    $('#fixture-flag').hidden = false;
    document.body.classList.add('is-fixture');
    if (API.fallbackReason) setTimeout(() => toast(API.fallbackReason, 'warn'), 300);
  }
  Player.init();
  Shell.init();
  Scan.init();
  Library.init();
  Review.init();
  MoveLog.init();
  App.on('state', (s) => Shell.renderPlanStatus(s));
  await App.refreshState();
  App.refreshFacets();
  Shell.route();
  Shell.poll();
})();
