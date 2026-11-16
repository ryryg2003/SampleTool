/* library.js: Library view — packs sidebar, search + filters, virtual sample table,
   multi-select, inspector, bulk edit, kits per pack. */
'use strict';

const CATEGORY_LIST = ['One-Shots/Drums', 'One-Shots/Bass & 808s', 'One-Shots/Instruments', 'One-Shots/FX & Vocals',
  'Loops/Drum Loops', 'Loops/Melody Loops', 'Loops/Bass Loops', 'Loops/Vocal & FX Loops', 'Presets & MIDI', 'Unsorted'];
const FILTER_LABEL = { category: 'Category', instrument: 'Instrument', loop_type: 'Type', key: 'Key', genre: 'Genre', manufacturer: 'Maker', bpm_min: 'BPM ≥', bpm_max: 'BPM ≤', status: 'Status', pack: 'Pack', q: 'Search', coll: 'Collection', new: 'New' };
const FILTER_KEYS = ['category', 'instrument', 'loop_type', 'key', 'genre', 'manufacturer', 'bpm_min', 'bpm_max', 'status'];
const NO_HASH_TIP = 'Can’t star or collect: this file could not be fingerprinted (SHA-256 failed)';
function starTip(r) {
  if (!r.sha256) return NO_HASH_TIP;
  const base = r.starred ? 'Unstar (S)' : 'Star (S)';
  return r.dup_count > 1 ? `${base}. ${r.dup_count} identical copies share this star: favorites follow the content, not the path` : base;
}
function starBtn(r, extra) {
  return `<button type="button" class="ib star${r.starred ? ' on' : ''}${extra || ''}" data-act="star" aria-pressed="${r.starred ? 'true' : 'false'}" aria-label="${r.starred ? 'Unstar' : 'Star'} ${esc(r.name)}" title="${esc(starTip(r))}" tabindex="-1"${r.sha256 ? '' : ' disabled'}><svg><use href="#i-star"/></svg></button>`;
}

function provBadge(p) {
  if (!p || !p.source) return '<span class="prov prov-none" title="Unknown: no evidence found">unknown</span>';
  const c = p.confidence;
  const lvl = c === null || c === undefined ? 'sure' : c >= 0.8 ? 'hi' : c >= 0.55 ? 'mid' : 'lo';
  const title = `${SOURCE_LABEL[p.source] || p.source}${c !== null && c !== undefined ? ', ' + Fmt.pct(c) + ' confidence' : ''}`;
  return `<span class="prov prov-${esc(p.source)} conf-${lvl}" title="${esc(title)}">${esc(SOURCE_LABEL[p.source] || p.source)}${c !== null && c !== undefined ? `<b class="mono">${Fmt.pct(c)}</b>` : ''}</span>`;
}
function statusBadge(s) { return s ? `<span class="st st-${esc(s)}">${esc(STATUS_LABEL[s] || s)}</span>` : ''; }
function extBadge(row) { return `<span class="ext${row.preview === 'unsupported' ? ' ext-np' : ''}" title="${row.preview === 'unsupported' ? 'Browser cannot preview this format' : esc(row.ext)}">${esc((row.ext || '').replace('.', ''))}</span>`; }

function shortPack(pack, maker) {
  return maker && pack.toLowerCase().startsWith(maker.toLowerCase() + ' - ') ? pack.slice(maker.length + 3) : pack;
}
const DEFAULT_KINDS = 'audio,midi,preset';

const Library = {
  allFiles: Store.get('lib.allFiles', false),
  filters: {},
  q: '',
  pack: null,
  coll: null,          // {id: 'fav'|<collection id>, name} membership filter
  newOnly: false,      // "New since last run"
  tab: 'sounds',
  sel: new Map(),      // id -> row
  anchor: -1,
  current: null,       // row shown in inspector
  detail: null,

  init() {
    this.table = new VTable($('#lib-table'), {
      label: 'Samples', multiselect: true, rowHeight: 28, pageSize: 200,
      sort: Store.get('lib.sort', 'name'), dir: Store.get('lib.dir', 'asc'),
      columns: [
        { key: 'play', label: '', sr: 'Playing', width: '28px', cls: 'c-play', render: (r) => `<i class="pl${Player.isPlaying(r.id) ? ' on' : Player.row && Player.row.id === r.id ? ' loaded' : ''}" aria-hidden="true"></i>` },
        { key: 'name', label: 'Name', sort: 'name', width: 'minmax(150px, 2.2fr)', cls: 'c-name', title: (r) => r.abs_path, render: (r) => `<span class="nm ellip">${esc(r.name)}</span>${extBadge(r)}${r.kit_name ? `<span class="kitmark" title="Part of kit ${esc(r.kit_name)}">kit</span>` : ''}${r.dup_count > 1 ? `<span class="dupmark mono" title="${r.dup_count} identical copies">×${r.dup_count}</span>` : ''}` },
        { key: 'pack', label: 'Pack', sort: 'pack', width: 'minmax(90px, 1.1fr)', cls: 'c-pack', title: (r) => r.pack || '', render: (r) => `<span class="ellip">${esc(r.pack ? shortPack(r.pack, r.manufacturer) : (r.origin === 'library' ? 'In library' : '—'))}</span>` },
        { key: 'inst', label: 'Instrument', width: 'minmax(64px, .55fr)', cls: 'c-inst', title: (r) => r.category || '', render: (r) => `<span class="ellip">${esc(r.instrument || Fmt.catShort(r.category) || '—')}</span>` },
        { key: 'bpm', label: 'BPM', sort: 'bpm', width: '46px', cls: 'num', render: (r) => esc(Fmt.bpm(r.bpm)) },
        { key: 'key', label: 'Key', sort: 'key', width: '42px', cls: 'c-key', render: (r) => esc(r.key || '') },
        { key: 'dur', label: 'Length', sort: 'duration', width: '54px', cls: 'num', render: (r) => r.duration_s ? Fmt.dur(r.duration_s) : '' },
        { key: 'size', label: 'Size', sort: 'size', width: '60px', cls: 'num', render: (r) => Fmt.bytes(r.size) },
        { key: 'st', label: 'Status', sort: 'status', width: '76px', cls: 'c-st', render: (r) => statusBadge(r.review_status) },
        { key: 'star', label: '', sr: 'Starred', width: '24px', cls: 'c-star', render: (r) => starBtn(r) },
        { key: 'act', label: '', sr: 'Actions', width: '72px', cls: 'c-act', render: () => `<button type="button" class="ib" data-act="copy" aria-label="Copy path" title="Copy path" tabindex="-1"><svg><use href="#i-copy"/></svg></button><button type="button" class="ib" data-act="reveal" aria-label="Show in Explorer" title="Show in Explorer" tabindex="-1"><svg><use href="#i-folder"/></svg></button><button type="button" class="ib" data-act="edit" aria-label="Edit" title="Edit (E)" tabindex="-1"><svg><use href="#i-edit"/></svg></button>` },
      ],
      fetch: (offset, limit) => API.get('/api/samples', Object.assign(this.params(), { sort: this.table.sort, dir: this.table.dir, offset, limit })),
      onSort: (s, d) => { Store.set('lib.sort', s); Store.set('lib.dir', d); },
      onTotal: (t) => { $('#lib-count').textContent = Fmt.num(t) + (t === 1 ? ' sample' : ' samples'); },
      isSelected: (r) => this.sel.has(r.id),
      rowClass: (r) => (r.excluded ? 'is-excl' : ''),
      onRowClick: (i, r, ev) => this.click(i, r, ev),
      onRowDblClick: (i, r) => Player.play(r, true),
      onAction: (act, r, i) => {
        if (act === 'star') { if ((typeof Collections !== 'undefined')) Collections.star([r]); }
        else if (act === 'copy') copyText(r.abs_path);
        else if (act === 'reveal') reveal({ id: r.id });
        else if (act === 'edit') { if (!this.sel.has(r.id)) this.selectOnly(i, r); this.openBulk(); }
      },
      onKeyMove: (i, r, ev) => {
        if (!r) return;
        if (ev.shiftKey && this.anchor >= 0) this.selectRange(this.anchor, i);
        else { this.selectOnly(i, r); }
        if (Player.autoOn()) Player.play(r, true);
        else if (!Player.row || Player.audio.paused) Player.load(r);
      },
      emptyHtml: () => this.emptyHtml(),
      onEmptyAction: (a) => { if (a === 'clear') this.clearAll(); else if (a === 'scan') Shell.go('scan'); },
    });

    $('#lib-q').addEventListener('input', debounce(() => { this.q = $('#lib-q').value.trim(); this.apply(); }, 150));
    $('#lib-q').addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowDown') { ev.preventDefault(); this.table.body.focus(); if (this.table.cursor < 0) this.table.setCursor(0); }
      else if (ev.key === 'Enter') { ev.preventDefault(); this.table.body.focus(); this.table.setCursor(0); }
    });
    $$('#lib-filters [data-f]').forEach((el) => {
      const h = () => { const v = el.value.trim(); if (v) this.filters[el.dataset.f] = v; else delete this.filters[el.dataset.f]; this.apply(); };
      el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', el.tagName === 'SELECT' ? h : debounce(h, 300));
    });
    $('#lib-clear').addEventListener('click', () => this.clearAll());
    $('#lib-allfiles').checked = this.allFiles;
    $('#lib-allfiles').addEventListener('change', () => { this.allFiles = $('#lib-allfiles').checked; Store.set('lib.allFiles', this.allFiles); this.apply(); });
    $('#pack-q').addEventListener('input', debounce(() => this.loadPacks(), 150));
    $('#pack-q').addEventListener('keydown', (ev) => { if (ev.key === 'ArrowDown') { ev.preventDefault(); const f = $('#pack-list .pk'); if (f) f.focus(); } });
    $('#pack-list').addEventListener('click', (ev) => { const b = ev.target.closest('.pk'); if (b) this.setPack(b.dataset.pack || null); });
    $('#pack-list').addEventListener('keydown', (ev) => {
      const b = ev.target.closest('.pk'); if (!b) return;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        const all = $$('#pack-list .pk'); const k = all.indexOf(b) + (ev.key === 'ArrowDown' ? 1 : -1);
        if (all[k]) all[k].focus(); else if (k < 0) $('#pack-q').focus();
      }
    });
    $('#ltab-sounds').addEventListener('click', () => this.setTab('sounds'));
    $('#ltab-kits').addEventListener('click', () => this.setTab('kits'));
    $('#sel-edit').addEventListener('click', () => this.openBulk());
    $('#sel-kit').addEventListener('click', () => this.openKitNew());
    $('#sel-clear').addEventListener('click', () => this.clearSelection());
    $('#sel-star').addEventListener('click', () => Collections.star([...this.sel.values()]));
    $('#sel-coll').addEventListener('click', () => Collections.openAdd([...this.sel.values()]));
    $('#lib-new').addEventListener('click', () => this.setNewOnly(!this.newOnly));
    $('#lib-save-smart').addEventListener('click', () => Collections.openSaveSmart(this.params()));
    $('#bulk-go').addEventListener('click', () => this.applyBulk());
    $('#kitnew-go').addEventListener('click', () => this.createKit());
    $('#kitnew-name').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); this.createKit(); } });
    $('#lib-kits').addEventListener('click', (ev) => this.kitsClick(ev));
    App.on('facets', (f) => this.fillFacets(f));
    App.on('player', () => this.table.render());
    App.on('playstate', () => this.table.render());
    App.on('edited', () => { this.loadPacks(); });
    this.renderInspector();
  },

  params() {
    const p = Object.assign({}, this.filters);
    if (this.q) p.q = this.q;
    if (!this.allFiles) p.kind = DEFAULT_KINDS;
    if (this.pack) { p.pack = this.pack; p.kit = 'none'; }
    if (this.coll) { if (this.coll.id === 'fav') p.fav = '1'; else p.collection = this.coll.id; }
    if (this.newOnly) p.new = '1';
    return p;
  },
  activeCount() { return Object.keys(this.filters).length + (this.q ? 1 : 0) + (this.pack ? 1 : 0) + (this.coll ? 1 : 0) + (this.newOnly ? 1 : 0); },
  setColl(c) { this.coll = c || null; this.apply(); },
  /* Open a collection from the sidebar: show the whole collection (other filters reset). */
  openColl(c) {
    this.filters = {}; this.q = ''; $('#lib-q').value = '';
    $$('#lib-filters [data-f]').forEach((el) => { el.value = ''; });
    this.newOnly = false; this.coll = c || null;
    if (this.pack) { this.setPack(null); return; }
    this.apply();
  },
  setNewOnly(on) { this.newOnly = !!on; this.apply(); },
  /* Restore a saved search (smart collection). Replaces every current filter. */
  restoreParams(p) {
    p = p || {};
    this.filters = {};
    for (const k of FILTER_KEYS) if (p[k]) this.filters[k] = String(p[k]);
    $$('#lib-filters [data-f]').forEach((el) => { el.value = this.filters[el.dataset.f] || ''; });
    this.q = p.q || ''; $('#lib-q').value = this.q;
    this.allFiles = !p.kind; $('#lib-allfiles').checked = this.allFiles; Store.set('lib.allFiles', this.allFiles);
    this.newOnly = p.new === '1' || p.new === 'true';
    this.coll = p.fav ? { id: 'fav', name: 'Favorites' } : p.collection ? { id: p.collection, name: ((typeof Collections !== 'undefined') && Collections.name(p.collection)) || 'Collection' } : null;
    if ((p.pack || null) !== this.pack) { this.setPack(p.pack || null); return; }
    this.apply();
  },

  apply() {
    this.renderChips();
    this.table.reset();
  },

  renderChips() {
    const chips = [];
    if (this.pack) chips.push(['pack', this.pack]);
    if (this.coll) chips.push(['coll', this.coll.name]);
    if (this.newOnly) chips.push(['new', 'since last run']);
    if (this.q) chips.push(['q', '“' + this.q + '”']);
    for (const [k, v] of Object.entries(this.filters)) chips.push([k, k === 'category' ? Fmt.catShort(v) : k === 'status' ? STATUS_LABEL[v] || v : v]);
    $('#lib-chips').innerHTML = chips.map(([k, v]) => `<span class="chip"><span class="chip-k">${esc(FILTER_LABEL[k] || k)}</span> <span class="chip-v ellip">${esc(v)}</span><button type="button" class="chip-x" data-k="${k}" aria-label="Remove filter ${esc(FILTER_LABEL[k] || k)} ${esc(v)}"><svg><use href="#i-x"/></svg></button></span>`).join('');
    $$('#lib-chips .chip-x').forEach((b) => b.addEventListener('click', () => {
      const k = b.dataset.k;
      if (k === 'pack') this.setPack(null);
      else if (k === 'coll') this.setColl(null);
      else if (k === 'new') this.setNewOnly(false);
      else if (k === 'q') { this.q = ''; $('#lib-q').value = ''; this.apply(); }
      else { delete this.filters[k]; const el = $(`#lib-filters [data-f="${k}"]`); if (el) el.value = ''; this.apply(); }
    }));
    $('#lib-clear').hidden = chips.length === 0;
    $('#lib-save-smart').hidden = chips.length === 0;
    const nb = $('#lib-new'); nb.setAttribute('aria-pressed', this.newOnly ? 'true' : 'false'); nb.classList.toggle('is-set', this.newOnly);
    App.emit('lib-filters', this.params());
    $$('#lib-filters [data-f]').forEach((el) => el.classList.toggle('is-set', !!this.filters[el.dataset.f]));
  },

  clearAll() {
    this.filters = {}; this.q = ''; $('#lib-q').value = '';
    $$('#lib-filters [data-f]').forEach((el) => { el.value = ''; });
    this.pack = null; this.tab = 'sounds'; this.coll = null; this.newOnly = false;
    this.renderPackHead(); this.markPacks();
    this.apply();
  },

  emptyHtml() {
    if (App.state && !App.state.has_inventory) return '<div><strong>Your library is empty.</strong><p>Run a read-only scan of your packs folder to see every sound here.</p><button class="btn btn-small btn-primary" type="button" data-empty-act="scan">Go to Scan</button></div>';
    if (this.activeCount()) return `<div><strong>No samples match.</strong><p>${this.pack ? 'This pack has no loose sounds matching these filters' + (this.kitCount ? '; check its Kits tab' : '') + '.' : 'Try a shorter search or remove a filter.'}</p><button class="btn btn-small" type="button" data-empty-act="clear">Show full library</button></div>`;
    return '<div><strong>No samples yet.</strong></div>';
  },

  fillFacets(f) {
    const map = { category: f.categories, instrument: f.instruments, loop_type: f.loop_types, key: f.keys, genre: f.genres, manufacturer: f.manufacturers };
    for (const [k, vals] of Object.entries(map)) {
      const el = $(`#lib-filters [data-f="${k}"]`); if (!el) continue;
      const cur = el.value; const first = el.options[0].outerHTML;
      el.innerHTML = first + (vals || []).map((v) => `<option value="${esc(v)}">${esc(k === 'category' ? v.replace('/', ' › ') : v)}</option>`).join('');
      el.value = cur;
    }
  },

  /* ---------- packs ---------- */
  async loadPacks() {
    const ul = $('#pack-list');
    const q = $('#pack-q').value.trim();
    if (!this.packsLoaded) ul.innerHTML = '<li class="side-msg">Loading packs…</li>';
    try {
      const packs = await API.get('/api/packs', { q });
      this.packsLoaded = true;
      this.packs = packs;
      $('#pack-n').textContent = packs.length;
      const all = `<li><button type="button" class="pk pk-all" data-pack="" role="option"><span class="pk-name">Full library</span><span class="pk-meta">all packs</span></button></li>`;
      ul.innerHTML = (q ? '' : all) + (packs.length ? packs.map((p) => `<li><button type="button" class="pk" role="option" data-pack="${esc(p.name)}" title="${esc(p.name)}"><span class="pk-name ellip">${esc(this.packTitle(p))}</span><span class="pk-meta"><span class="ellip">${esc(p.manufacturer || 'Unknown maker')}</span><span class="mono">${Fmt.num(p.files)}${p.kits ? ` · ${p.kits} kit${p.kits > 1 ? 's' : ''}` : ''}</span></span></button></li>`).join('') : `<li class="side-msg">No pack or maker matches “${esc(q)}”.</li>`);
      this.markPacks();
    } catch (e) {
      ul.innerHTML = `<li class="side-msg err">Couldn't load packs: ${esc(e.message)}</li>`;
    }
  },
  packTitle(p) {
    const m = p.manufacturer;
    return shortPack(p.name, m);
  },
  markPacks() {
    $$('#pack-list .pk').forEach((b) => { const on = (b.dataset.pack || null) === this.pack; b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
  },
  async setPack(name) {
    this.pack = name || null;
    this.tab = 'sounds';
    this.markPacks();
    this.renderPackHead();
    this.apply();
    if (this.pack) {
      try {
        this.kits = await API.get('/api/kits', { pack: this.pack });
        this.kitCount = this.kits.length;
        $('#ltab-kits-n').textContent = this.kits.length;
        if (this.tab === 'kits') this.renderKits();
      } catch (e) { this.kits = []; $('#ltab-kits-n').textContent = '!'; }
    }
  },
  renderPackHead() {
    const p = this.pack && (this.packs || []).find((x) => x.name === this.pack);
    $('#lib-h').textContent = p ? this.packTitle(p) : this.pack || 'Full library';
    $('#lib-sub').textContent = p ? `${p.manufacturer || 'Unknown maker'} · ${Fmt.num(p.files)} files · ${Fmt.bytes(p.bytes)}` : '';
    $('#lib-tabs').hidden = !this.pack;
    $('#ltab-sounds').innerHTML = this.pack ? 'Loose sounds' : 'Sounds';
    this.setTab(this.tab, true);
  },
  setTab(t, quiet) {
    this.tab = t;
    $('#ltab-sounds').setAttribute('aria-selected', t === 'sounds' ? 'true' : 'false');
    $('#ltab-kits').setAttribute('aria-selected', t === 'kits' ? 'true' : 'false');
    $('#lib-sounds').hidden = t !== 'sounds';
    $('#lib-kits').hidden = t !== 'kits';
    if (t === 'kits' && !quiet) this.renderKits();
    if (t === 'sounds') this.table.schedule();
  },

  /* ---------- kits inside a pack ---------- */
  renderKits() {
    const box = $('#lib-kits');
    const ks = this.kits || [];
    if (!ks.length) { box.innerHTML = '<div class="empty"><strong>No complete kits detected in this pack.</strong><p>Kits are folders whose stems, MIDI and project files belong together. Select samples on the Loose sounds tab and choose Create kit to group them yourself.</p></div>'; return; }
    box.innerHTML = ks.map((k) => `
      <details class="kitgrp" data-kit="${esc(k.id)}">
        <summary>
          <svg class="chev" aria-hidden="true"><use href="#i-chev"/></svg>
          <span class="kg-name">${esc(k.name)}</span>
          <span class="kg-meta mono">${k.bpm ? Fmt.bpm(k.bpm) + ' BPM' : ''}${k.key ? ' · ' + esc(k.key) : ''}</span>
          <span class="kg-meta mono">${k.member_count} files · ${Fmt.bytes(k.bytes)}</span>
          <span class="conf" title="Detection confidence"><i style="--c:${Math.round((k.confidence || 0) * 100)}%"></i><b class="mono">${Fmt.pct(k.confidence)}</b></span>
          ${k.held ? '<span class="st st-held">Held</span>' : ''}${k.excluded ? '<span class="st st-excluded">Excluded</span>' : ''}
        </summary>
        <div class="kg-body" data-body>Loading…</div>
      </details>`).join('');
    $$('#lib-kits details').forEach((d) => d.addEventListener('toggle', () => { if (d.open && !d.dataset.loaded) this.loadKitMembers(d); }));
  },
  async loadKitMembers(d) {
    const body = $('[data-body]', d);
    try {
      const k = await API.get('/api/kits/' + encodeURIComponent(d.dataset.kit));
      d.dataset.loaded = '1';
      this.kitMembers = this.kitMembers || {};
      k.members.forEach((m) => { this.kitMembers[m.id] = m; });
      body.innerHTML = `<p class="kg-dest mono ellip" title="${esc(k.proposed_root)}">→ ${esc(k.proposed_root || '')}</p>` +
        `<ul class="kg-members">${k.members.map((m) => `<li><button type="button" class="kgm" data-id="${esc(m.id)}" title="${esc(m.abs_path)}"><i class="pl" aria-hidden="true"></i><span class="ellip">${esc(m.name)}</span>${extBadge(m)}<span class="mono dim">${m.duration_s ? Fmt.dur(m.duration_s) : ''}</span><span class="mono dim">${Fmt.bytes(m.size)}</span></button></li>`).join('')}</ul>`;
    } catch (e) { body.innerHTML = `<p class="err">Couldn't load kit: ${esc(e.message)}</p>`; }
  },
  kitsClick(ev) {
    const b = ev.target.closest('.kgm'); if (!b) return;
    const m = this.kitMembers[b.dataset.id]; if (!m) return;
    $$('#lib-kits .kgm').forEach((x) => x.classList.toggle('on', x === b));
    this.sel.clear(); this.sel.set(m.id, m); this.updateSelbar();
    this.inspect(m);
    Player.play(m, true);
  },

  /* ---------- selection ---------- */
  click(i, r, ev) {
    if (ev.ctrlKey || ev.metaKey) {
      if (this.sel.has(r.id)) this.sel.delete(r.id); else this.sel.set(r.id, r);
      this.anchor = i;
      this.afterSel(r);
    } else if (ev.shiftKey && this.anchor >= 0) {
      this.selectRange(this.anchor, i);
    } else {
      this.selectOnly(i, r);
      Player.toggle(r);
    }
  },
  selectOnly(i, r) { this.sel.clear(); this.sel.set(r.id, r); this.anchor = i; this.afterSel(r); },
  selectRange(a, b) {
    this.sel.clear();
    const [lo, hi] = a < b ? [a, b] : [b, a];
    for (let k = lo; k <= hi; k++) { const row = this.table.getRow(k); if (row) this.sel.set(row.id, row); }
    this.afterSel(this.table.getRow(b));
  },
  async selectAll() {
    const total = this.table.total || 0;
    if (!total) return;
    if (total > 50000) toast('Selecting the first 50,000 results', 'warn');
    const rows = await this.table.loadAll(50000);
    this.sel.clear(); rows.forEach((r) => this.sel.set(r.id, r));
    this.afterSel(null);
  },
  clearSelection() { this.sel.clear(); this.anchor = -1; this.afterSel(null); },
  afterSel(focusRow) {
    this.table.render();
    this.updateSelbar();
    if (this.sel.size === 1) this.inspect([...this.sel.values()][0]);
    else if (this.sel.size > 1) { this.current = null; this.renderInspector(); }
    else { this.current = null; this.renderInspector(); }
  },
  updateSelbar() {
    const n = this.sel.size;
    $('#selbar').hidden = n < 2;
    $('#sel-n').textContent = Fmt.num(n) + ' selected';
  },
  selectedIds() { return [...this.sel.keys()]; },

  /* ---------- inspector ---------- */
  async inspect(row) {
    this.current = row;
    this.detail = null;
    this.renderInspector();
    try {
      const d = await API.get('/api/samples/' + encodeURIComponent(row.id));
      if (this.current && this.current.id === row.id) { this.detail = d; this.renderInspector(); }
    } catch (e) {
      if (this.current && this.current.id === row.id) { this.detailErr = e.message; this.renderInspector(); }
    }
  },
  renderInspector() {
    const box = $('#inspector');
    if (this.sel.size > 1) {
      const rows = [...this.sel.values()];
      const bytes = rows.reduce((s, r) => s + (r.size || 0), 0);
      const packs = new Set(rows.map((r) => r.pack)).size;
      box.innerHTML = `<div class="ins-multi">
        <h2 class="ins-name">${Fmt.num(rows.length)} samples selected</h2>
        <p class="dim">${Fmt.bytes(bytes)} from ${packs} pack${packs > 1 ? 's' : ''}</p>
        <div class="ins-actions col">
          <button class="btn btn-primary" type="button" id="ins-bulk">Bulk edit ${Fmt.num(rows.length)} samples…</button>
          <button class="btn" type="button" id="ins-kit">Create kit from selection…</button>
          <button class="btn" type="button" id="ins-mstar"><svg><use href="#i-star"/></svg>Star / unstar all</button>
          <button class="btn" type="button" id="ins-mcoll">Add to collection…</button>
          ${this.coll && this.coll.id !== 'fav' ? `<button class="btn" type="button" id="ins-mremove">Remove from “${esc(this.coll.name)}”</button>` : ''}
          <button class="btn btn-ghost" type="button" id="ins-clear">Clear selection</button>
        </div>
        <p class="hint">Ctrl+click adds or removes one sample. Shift+click or Shift+↓ extends the range.</p></div>`;
      $('#ins-bulk').onclick = () => this.openBulk();
      $('#ins-kit').onclick = () => this.openKitNew();
      $('#ins-mstar').onclick = () => Collections.star(rows);
      $('#ins-mcoll').onclick = () => Collections.openAdd(rows);
      if ($('#ins-mremove')) $('#ins-mremove').onclick = () => Collections.removeFrom(this.coll.id, rows);
      $('#ins-clear').onclick = () => this.clearSelection();
      return;
    }
    const r = this.current;
    if (!r) {
      box.innerHTML = `<div class="ins-empty"><h2 class="side-h">Nothing selected</h2><p>Click a sample to hear it and see where its metadata came from. Use <kbd>↑</kbd> <kbd>↓</kbd> to step through and <kbd>Space</kbd> to play or stop.</p></div>`;
      return;
    }
    const d = this.detail || r;
    const meta = (this.detail && this.detail.meta) || null;
    const field = (k, label, fmt) => {
      const v = meta && meta[k] ? meta[k].value : d[k];
      const p = meta && meta[k] ? meta[k] : (d.prov || {})[k];
      const shown = v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length) ? '<span class="unk">unknown</span>' : esc(fmt ? fmt(v) : v);
      return `<tr><th scope="row">${label}</th><td>${shown}</td><td>${provBadge(p)}</td></tr>`;
    };
    const a = (this.detail && this.detail.audio) || null;
    const audioLine = a ? [a.format && a.format.toUpperCase(), a.sample_rate && (a.sample_rate / 1000) + ' kHz', a.bits && a.bits + '-bit', a.channels && (a.channels === 1 ? 'mono' : a.channels === 2 ? 'stereo' : a.channels + ' ch'), a.duration_s && Fmt.dur(a.duration_s)].filter(Boolean).join(' · ') : '';
    box.innerHTML = `
      <div class="ins-top">
        <h2 class="ins-name" title="${esc(d.name)}">${esc(d.name)}</h2>
        <div class="ins-sub">${statusBadge(d.review_status)}${d.kit_name ? `<span class="kitmark">kit: ${esc(d.kit_name)}</span>` : ''}<span class="dim mono">${esc(audioLine || Fmt.bytes(d.size))}</span></div>
        <div class="ins-actions">
          <button class="btn btn-small" type="button" id="ins-play"><svg><use href="#i-${Player.isPlaying(d.id) ? 'stop' : 'play'}"/></svg>${Player.isPlaying(d.id) ? 'Stop' : 'Play'}</button>
          <button class="btn btn-small" type="button" id="ins-edit"><svg><use href="#i-edit"/></svg>Edit</button>
          ${starBtn(d, ' ins-star').replace('tabindex="-1"', 'id="ins-star"')}
          <button class="btn btn-small" type="button" id="ins-coll" ${d.sha256 ? '' : `disabled title="${esc(NO_HASH_TIP)}"`}>Collect…</button>
          <button class="ib" type="button" id="ins-copy" aria-label="Copy original path" title="Copy original path"><svg><use href="#i-copy"/></svg></button>
          <button class="ib" type="button" id="ins-reveal" aria-label="Show in Explorer" title="Show in Explorer"><svg><use href="#i-folder"/></svg></button>
        </div>
        ${d.preview === 'unsupported' ? `<p class="note note-warn">Preview not supported in browser for ${esc(d.ext)}.</p>` : ''}
      </div>
      <div class="ins-scroll">
        <table class="meta-t"><tbody>
          ${field('category', 'Category', (v) => v.replace('/', ' › '))}
          ${field('instrument', 'Instrument')}
          ${field('loop_type', 'Type')}
          ${field('bpm', 'BPM', Fmt.bpm)}
          ${field('key', 'Key')}
          ${field('genre', 'Genre')}
          ${field('tags', 'Tags', (v) => Array.isArray(v) ? v.join(', ') : v)}
          ${field('manufacturer', 'Maker')}
        </tbody></table>
        ${!this.detail && !this.detailErr ? '<p class="dim small">Loading details…</p>' : ''}
        <div class="paths">
          <div class="path-blk"><span class="path-l">Original</span><code class="path" title="${esc(d.abs_path)}">${esc(d.abs_path)}</code></div>
          <div class="path-blk"><span class="path-l">${d.final_path ? 'Now at' : 'Will go to'}</span>${d.final_path || d.proposed_dest ? `<code class="path path-dest" title="${esc(d.final_path || d.proposed_dest)}">${esc(d.final_path || d.proposed_dest)}</code>` : `<span class="unk">${d.excluded ? 'Excluded: stays where it is' : 'No destination yet: held until category is known'}</span>`}</div>
        </div>
        ${d.dup_count > 1 ? `<p class="note">${d.dup_count} identical copies of this file exist. The plan keeps one physical copy.</p>` : ''}
        ${(d.warnings || []).length ? `<ul class="warns">${d.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}
        ${this.detail && this.detail.tags_raw && Object.keys(this.detail.tags_raw).length ? `<details class="raw"><summary>Embedded tags</summary><pre class="mono">${esc(JSON.stringify(this.detail.tags_raw, null, 1))}</pre></details>` : ''}
        ${(typeof Collections !== 'undefined') ? Collections.inspectorHtml(d) : ''}
        <p class="dim small mono">sha256 ${esc(Fmt.hash8(d.sha256))}…</p>
      </div>`;
    $('#ins-play').onclick = () => { Player.toggle(d); this.renderInspector(); };
    $('#ins-edit').onclick = () => this.openBulk();
    $('#ins-copy').onclick = () => copyText(d.abs_path);
    $('#ins-reveal').onclick = () => reveal({ id: d.id });
    $('#ins-star').onclick = () => Collections.star([d]);
    $('#ins-coll').onclick = () => Collections.openAdd([d]);
    $$('#inspector [data-coll-rm]').forEach((b) => { b.onclick = () => Collections.removeFrom(b.dataset.collRm, [d]); });
    this.detailErr = null;
  },

  /* ---------- bulk / single edit ---------- */
  openBulk() {
    const ids = this.selectedIds();
    if (!ids.length) { toast('Select one or more samples first'); return; }
    const single = ids.length === 1 ? this.sel.get(ids[0]) : null;
    const src = single ? Object.assign({}, single, this.detail && this.detail.id === single.id ? this.detail : {}) : null;
    const f = App.facets || {};
    const dl = (id, vals) => `<datalist id="${id}">${(vals || []).map((v) => `<option value="${esc(v)}">`).join('')}</datalist>`;
    const cats = [...new Set(CATEGORY_LIST.concat(f.categories || []))];
    const rows = [
      ['category', 'Category', `<select class="input" data-v="category">${cats.map((c) => `<option value="${esc(c)}">${esc(c.replace('/', ' › '))}</option>`).join('')}</select>`],
      ['instrument', 'Instrument', `<input class="input" data-v="instrument" list="dl-inst" autocomplete="off">${dl('dl-inst', f.instruments)}`],
      ['loop_type', 'Loop type', '<select class="input" data-v="loop_type"><option value="one-shot">One-shot</option><option value="loop">Loop</option><option value="">Unknown</option></select>'],
      ['bpm', 'BPM', '<input class="input mono" data-v="bpm" type="number" min="20" max="400" step="0.01">'],
      ['key', 'Key', `<input class="input" data-v="key" list="dl-key" autocomplete="off">${dl('dl-key', f.keys)}`],
      ['genre', 'Genre', `<input class="input" data-v="genre" list="dl-genre" autocomplete="off">${dl('dl-genre', f.genres)}`],
      ['tags', 'Tags', '<input class="input" data-v="tags" placeholder="comma, separated" autocomplete="off">'],
      ['excluded', 'Organize?', '<select class="input" data-v="excluded"><option value="false">Include in plan</option><option value="true">Exclude: leave file where it is</option></select>'],
    ];
    $('#bulk-n').textContent = single ? single.name : Fmt.num(ids.length) + ' samples';
    $('#bulk-grid').innerHTML = rows.map(([k, label, ctl]) => `<label class="bulk-set"><input type="checkbox" data-set="${k}"> ${label}</label><div class="bulk-ctl" data-ctl="${k}">${ctl}</div>`).join('');
    $$('#bulk-grid [data-v]').forEach((el) => {
      const k = el.dataset.v;
      if (src) {
        let v = src[k];
        if (k === 'tags') v = (v || []).join(', ');
        if (k === 'excluded') v = String(!!v);
        if (v !== null && v !== undefined) el.value = v;
      }
      el.disabled = false;
      const mark = () => { $(`#bulk-grid [data-set="${k}"]`).checked = true; };
      el.addEventListener('input', mark); el.addEventListener('change', mark);
    });
    $('#bulk-err').hidden = true;
    $('#bulk-dlg').showModal();
    const first = $('#bulk-grid [data-v]'); if (first) first.focus();
  },
  async applyBulk() {
    const ids = this.selectedIds();
    const changes = {};
    $$('#bulk-grid [data-set]').forEach((cb) => {
      if (!cb.checked) return;
      const k = cb.dataset.set; const el = $(`#bulk-grid [data-v="${k}"]`); let v = el.value.trim();
      if (k === 'bpm') v = v === '' ? null : parseFloat(v);
      else if (k === 'tags') v = v ? v.split(',').map((s) => s.trim()).filter(Boolean) : [];
      else if (k === 'excluded') v = v === 'true';
      else if (v === '') v = null;
      changes[k] = v;
    });
    if (!Object.keys(changes).length) { this.showErr('#bulk-err', 'Tick at least one field to change.'); return; }
    if ('bpm' in changes && changes.bpm !== null && (isNaN(changes.bpm) || changes.bpm < 20 || changes.bpm > 400)) { this.showErr('#bulk-err', 'BPM must be between 20 and 400, or empty for unknown.'); return; }
    const btn = $('#bulk-go'); btn.disabled = true;
    try {
      const res = await API.post('/api/items/edit', { ids, changes });
      $('#bulk-dlg').close();
      this.mergeRows(res.items || []);
      toast(`Updated ${Fmt.num(res.updated)} sample${res.updated === 1 ? '' : 's'}`);
      await App.afterEdit();
      App.refreshFacets();
      this.table.refresh();
      if (this.current) this.inspect(this.sel.get(this.current.id) || this.current);
    } catch (e) {
      this.showErr('#bulk-err', explain409(e, 'Not saved'));
    } finally { btn.disabled = false; }
  },
  mergeRows(updated) {
    const by = new Map(updated.map((r) => [r.id, r]));
    for (const [, items] of this.table.pages) if (Array.isArray(items)) items.forEach((r, i) => { if (by.has(r.id)) items[i] = by.get(r.id); });
    for (const id of this.sel.keys()) if (by.has(id)) this.sel.set(id, by.get(id));
    if (this.current && by.has(this.current.id)) this.current = by.get(this.current.id);
    this.table.render();
  },
  showErr(sel, msg) { const e = $(sel); e.textContent = msg; e.hidden = false; },

  openKitNew() {
    const ids = this.selectedIds();
    if (!ids.length) { toast('Select the samples that belong together first'); return; }
    $('#kitnew-n').textContent = Fmt.num(ids.length) + ' sample' + (ids.length > 1 ? 's' : '');
    $('#kitnew-name').value = '';
    $('#kitnew-err').hidden = true;
    $('#kitnew-dlg').showModal();
    $('#kitnew-name').focus();
  },
  async createKit() {
    const name = $('#kitnew-name').value.trim();
    if (!name) { this.showErr('#kitnew-err', 'Give the kit a name. It becomes the folder name under Complete Kits.'); return; }
    try {
      const res = await API.post('/api/kits/create', { name, ids: this.selectedIds() });
      $('#kitnew-dlg').close();
      toast(`Kit “${res.kit.name}” created with ${res.kit.member_ids ? res.kit.member_ids.length : ''} files`);
      await App.afterEdit();
      this.table.refresh();
      if (this.pack) this.setPack(this.pack);
    } catch (e) { this.showErr('#kitnew-err', explain409(e, 'Kit not created')); }
  },

  /* ---------- keyboard (when focus is not in a text field) ---------- */
  handleKey(ev) {
    const t = this.table;
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'a') { ev.preventDefault(); this.selectAll(); return true; }
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return false;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      if (document.activeElement !== t.body) { ev.preventDefault(); t.body.focus(); t.onKey(ev); }
      return true;
    }
    if (ev.key === 'Enter') {
      if (document.activeElement !== t.body) return false;
      const r = t.getRow(t.cursor); if (r) { ev.preventDefault(); Player.play(r, true); }
      return true;
    }
    if (ev.key === 'e' || ev.key === 'E') { if (this.sel.size) { ev.preventDefault(); this.openBulk(); return true; } }
    if (ev.key === 'Escape') {
      if (this.sel.size) { this.clearSelection(); return true; }
      return false;
    }
    return false;
  },
  currentRow() { return this.table.getRow(this.table.cursor) || this.current; },

  show() {
    if (!this.started) { this.started = true; this.loadPacks(); this.apply(); }
    else this.table.schedule();
  },
};
