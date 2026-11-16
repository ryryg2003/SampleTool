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
function estMark(r, k, text) {
  const est = text !== '' && r.prov && r.prov[k] && r.prov[k].source === 'analysis';
  return est ? `<span class="est" title="Measured from the audio (estimate)">${esc(text)}</span>` : esc(text);
}
function statusBadge(s) { return s ? `<span class="st st-${esc(s)}">${esc(STATUS_LABEL[s] || s)}</span>` : ''; }
function extBadge(row) { return `<span class="ext${row.preview === 'unsupported' ? ' ext-np' : ''}" title="${row.preview === 'unsupported' ? 'Browser cannot preview this format' : esc(row.ext)}">${esc((row.ext || '').replace('.', ''))}</span>`; }

/* Category tag (8px radius, filled with the category color). */
function catTag(category, text) {
  return `<span class="cat-tag cat-${catSlug(category)}" title="${esc(category || 'Unsorted')}">${esc(text || Fmt.catShort(category) || 'Unsorted')}</span>`;
}
/* Row play button: a square tinted with the row's category color. Shows EQ bars while playing. */
function rowPlayBtn(r) {
  const on = Player.isPlaying(r.id);
  const loaded = !on && Player.row && Player.row.id === r.id;
  return `<button type="button" class="row-play${on ? ' is-playing' : loaded ? ' is-loaded' : ''}" data-act="play" tabindex="-1" aria-label="${on ? 'Stop' : 'Play'} ${esc(r.name)}" title="${on ? 'Stop' : 'Play'} (Space)">${on ? '<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>' : '<svg aria-hidden="true"><use href="#i-play"/></svg>'}</button>`;
}
/* Pack avatar: initials of the first two words on a category-color gradient picked by a stable hash. */
function packInitials(title) {
  const w = String(title || '').replace(/[^\p{L}\p{N}\s]+/gu, ' ').trim().split(/\s+/).filter(Boolean);
  return ((w[0] || '?')[0] + (w[1] ? w[1][0] : '')).toUpperCase();
}
function packSlug(name) {
  let h = 0;
  for (const ch of String(name || '')) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return CAT_SLUGS[h % CAT_SLUGS.length];
}

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
      label: 'Samples', multiselect: true, rowHeight: 50, pageSize: 200,
      sort: Store.get('lib.sort', 'name'), dir: Store.get('lib.dir', 'asc'),
      columns: [
        { key: 'play', label: '', sr: 'Playing', width: '34px', cls: 'c-play', render: (r) => rowPlayBtn(r) },
        { key: 'name', label: 'Name', sort: 'name', width: 'minmax(110px, 1.7fr)', cls: 'c-name', title: (r) => r.abs_path, render: (r) => `<span class="nm-line"><span class="nm ellip">${esc(r.name)}</span></span><span class="nm-sub">${extBadge(r)}${r.kit_name ? `<span class="kitmark" title="Part of kit ${esc(r.kit_name)}">kit</span>` : ''}${r.dup_count > 1 ? `<span class="dupmark mono" title="${r.dup_count} identical copies">×${r.dup_count}</span>` : ''}</span>` },
        { key: 'pack', label: 'Pack', sort: 'pack', width: 'minmax(56px, .8fr)', cls: 'c-pack', title: (r) => r.pack || '', render: (r) => `<span class="ellip">${esc(r.pack ? shortPack(r.pack, r.manufacturer) : (r.origin === 'library' ? 'In library' : '—'))}</span>` },
        { key: 'inst', label: 'Instrument', width: 'minmax(84px, .95fr)', cls: 'c-inst', title: (r) => r.category || '', render: (r) => catTag(r.category, r.instrument || Fmt.catShort(r.category) || '—') },
        { key: 'bpm', label: 'BPM', sort: 'bpm', width: '44px', cls: 'num', render: (r) => estMark(r, 'bpm', Fmt.bpm(r.bpm)) },
        { key: 'key', label: 'Key', sort: 'key', width: '42px', cls: 'c-key', render: (r) => estMark(r, 'key', r.key || '') },
        { key: 'bars', label: 'Bars', width: '38px', cls: 'num', render: (r) => r.bars ? `${esc(r.bars)}${r.on_grid === false ? '<span class="offgrid" title="Starts late: not on the downbeat">◂</span>' : ''}` : '' },
        { key: 'dur', label: 'Length', sort: 'duration', width: '50px', cls: 'num', render: (r) => r.duration_s ? Fmt.dur(r.duration_s) : '' },
        { key: 'size', label: 'Size', sort: 'size', width: '54px', cls: 'num', render: (r) => Fmt.bytes(r.size) },
        { key: 'st', label: 'Status', sort: 'status', width: '70px', cls: 'c-st', render: (r) => statusBadge(r.review_status) },
        { key: 'star', label: '', sr: 'Starred', width: '26px', cls: 'c-star', render: (r) => starBtn(r) },
        { key: 'act', label: '', sr: 'Actions', width: '66px', cls: 'c-act', render: () => `<button type="button" class="ib" data-act="copy" aria-label="Copy path" title="Copy path" tabindex="-1"><svg><use href="#i-copy"/></svg></button><button type="button" class="ib" data-act="reveal" aria-label="Show in Explorer" title="Show in Explorer" tabindex="-1"><svg><use href="#i-folder"/></svg></button><button type="button" class="ib" data-act="edit" aria-label="Edit" title="Edit (E)" tabindex="-1"><svg><use href="#i-edit"/></svg></button>` },
      ],
      fetch: (offset, limit) => API.get('/api/samples', Object.assign(this.params(), { sort: this.table.sort, dir: this.table.dir, offset, limit })),
      onSort: (s, d) => { Store.set('lib.sort', s); Store.set('lib.dir', d); },
      onTotal: (t) => { $('#lib-count').textContent = Fmt.num(t) + (t === 1 ? ' sample' : ' samples'); },
      isSelected: (r) => this.sel.has(r.id),
      rowClass: (r) => `cat-${catSlug(r.category)}${r.excluded ? ' is-excl' : ''}${Player.isPlaying(r.id) ? ' is-playing' : ''}`,
      onRowClick: (i, r, ev) => this.click(i, r, ev),
      onRowDblClick: (i, r) => Player.play(r, true),
      onAction: (act, r, i, ev) => {
        if (act === 'play') this.playRow(i, r, ev);
        else if (act === 'star') { if ((typeof Collections !== 'undefined')) Collections.star([r]); }
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
    App.on('player', () => { this.table.render(); this.syncInsPlay(); });
    App.on('playstate', () => { this.table.render(); this.syncInsPlay(); });
    App.on('edited', () => { this.loadPacks(); this.loadStats(true); });
    $('#lib-stats').addEventListener('click', (ev) => {
      const b = ev.target.closest('.stat'); if (!b) return;
      const sel = $('#lib-filters [data-f="category"]');
      const want = this.filters.category === b.dataset.cat ? '' : b.dataset.cat;
      if (want && !Array.from(sel.options).some((o) => o.value === want)) sel.insertAdjacentHTML('beforeend', `<option value="${esc(want)}">${esc(want.replace('/', ' › '))}</option>`);
      sel.value = want;
      sel.dispatchEvent(new Event('change'));
    });
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
    this.loadStats();
  },

  /* ---------- category stat tiles ----------
     Counts use the list's scope only (pack + "show all files"), so they are fetched once per
     scope change, in parallel, and not on every search or filter change. */
  scopeParams() {
    const p = {};
    if (!this.allFiles) p.kind = DEFAULT_KINDS;
    if (this.pack) { p.pack = this.pack; p.kit = 'none'; }
    return p;
  },
  async loadStats(force) {
    const scope = JSON.stringify(this.scopeParams());
    if (!force && scope === this.statsScope) { this.renderStats(); return; }
    this.statsScope = scope;
    const base = this.scopeParams();
    const one = (extra) => API.get('/api/samples', Object.assign({}, base, extra, { limit: 1 })).then((r) => r.total || 0, () => null);
    const res = await Promise.all([one({})].concat(CATEGORY_LIST.map((c) => one({ category: c }))));
    if (this.statsScope !== scope) return; // scope changed while loading: drop stale counts
    this.stats = { total: res[0], counts: CATEGORY_LIST.map((c, k) => [c, res[k + 1]]) };
    this.renderStats();
    this.renderPackHead();
  },
  renderStats() {
    const box = $('#lib-stats');
    const st = this.stats;
    if (!st) { box.innerHTML = ''; return; }
    const active = this.filters.category || '';
    const tiles = st.counts.filter(([, n]) => n > 0);
    box.classList.toggle('has-active', !!active && tiles.some(([c]) => c === active));
    box.hidden = !tiles.length;
    box.innerHTML = tiles.map(([c, n]) => {
      const on = c === active;
      const pct = st.total ? Math.max(2, Math.round(n / st.total * 100)) : 0;
      return `<button type="button" class="stat cat-${catSlug(c)}${on ? ' on' : ''}" data-cat="${esc(c)}" aria-pressed="${on ? 'true' : 'false'}" title="${esc(c.replace('/', ' › '))}: ${Fmt.num(n)} of ${Fmt.num(st.total)}"><span class="stat-l ellip">${esc(Fmt.catShort(c))}</span><span class="stat-n mono">${Fmt.num(n)}</span><span class="stat-bar" aria-hidden="true"><i style="width:${pct}%"></i></span></button>`;
    }).join('');
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
    this.renderStats();
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
    if (!this.packsLoaded) { ul.innerHTML = '<li class="side-msg">Loading packs…</li>'; if (this.statsScope) this.loadStats(true); }
    try {
      const packs = await API.get('/api/packs', { q });
      this.packsLoaded = true;
      this.packs = packs;
      $('#pack-n').textContent = packs.length;
      const all = `<li><button type="button" class="pk pk-all" data-pack="" role="option"><span class="pk-av" aria-hidden="true"><svg><use href="#i-library"/></svg></span><span class="pk-txt"><span class="pk-name ellip">Full library</span><span class="pk-meta ellip">all packs</span></span><span class="pk-n mono"></span></button></li>`;
      ul.innerHTML = (q ? '' : all) + (packs.length ? packs.map((p) => { const t = this.packTitle(p); return `<li><button type="button" class="pk" role="option" data-pack="${esc(p.name)}" title="${esc(p.name)}"><span class="pk-av cat-${packSlug(p.name)}" aria-hidden="true">${esc(packInitials(t))}</span><span class="pk-txt"><span class="pk-name ellip">${esc(t)}</span><span class="pk-meta ellip">${esc(p.manufacturer || 'Unknown maker')}${p.kits ? ` · ${p.kits} kit${p.kits > 1 ? 's' : ''}` : ''}</span></span><span class="pk-n mono">${Fmt.num(p.files)}</span></button></li>`; }).join('') : `<li class="side-msg">No pack or maker matches “${esc(q)}”.</li>`);
      this.markPacks();
      if (!this.pack) this.renderPackHead();
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
    const st = this.stats;
    $('#lib-sub').textContent = p ? `${p.manufacturer || 'Unknown maker'} · ${Fmt.num(p.files)} files · ${Fmt.bytes(p.bytes)}`
      : !this.pack && st && st.total !== null && this.packs && !$('#pack-q').value.trim() ? `${Fmt.num(st.total)} sample${st.total === 1 ? '' : 's'} across ${Fmt.num(this.packs.length)} pack${this.packs.length === 1 ? '' : 's'}` : '';
    $('#lib-tabs').hidden = !this.pack;
    $('#ltab-sounds').innerHTML = this.pack ? 'Loose sounds' : 'Sounds';
    this.setTab(this.tab, true);
  },
  /* Play/stop the cursor row: the same logic Space uses (app.js keydown). */
  playCursor() {
    const r = this.currentRow();
    if (r && (!Player.row || Player.row.id !== r.id)) Player.play(r, true); else Player.toggle();
  },
  /* Row play button: move the cursor to the row, select it, then play it like Space does.
     Ctrl/Shift+click on the button falls back to normal multi-select. */
  playRow(i, r, ev) {
    const t = this.table;
    t.setCursor(i, false);
    t.body.focus({ preventScroll: true });
    if (ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey)) { this.click(i, r, ev); return; }
    if (this.sel.size === 1 && this.sel.has(r.id)) this.anchor = i; else this.selectOnly(i, r);
    this.playCursor();
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
    this.matches = null;
    this.matchErr = null;
    this.renderInspector();
    this.loadMatches(row.id);
    try {
      const d = await API.get('/api/samples/' + encodeURIComponent(row.id));
      if (this.current && this.current.id === row.id) { this.detail = d; this.renderInspector(); }
    } catch (e) {
      if (this.current && this.current.id === row.id) { this.detailErr = e.message; this.renderInspector(); }
    }
  },
  async loadMatches(id) {
    if (!this.current || this.current.kind !== 'audio') return;
    try {
      const r = await API.get('/api/samples/' + encodeURIComponent(id) + '/matches', { role: this.matchRole || '', limit: 25, loose: this.matchLoose ? 1 : '' });
      if (this.current && this.current.id === id) { this.matches = r.items; this.renderInspector(); }
    } catch (e) {
      if (this.current && this.current.id === id) { this.matches = []; this.matchErr = e.message; this.renderInspector(); }
    }
  },
  matchesHtml(d) {
    if (d.kind !== 'audio') return '';
    const role = this.matchRole || '';
    const roles = [['', 'All'], ['drums', 'Drums'], ['bass', 'Bass'], ['melody', 'Melody'], ['vocal-fx', 'Vocal & FX']];
    const chips = roles.map(([v, l]) => `<button type="button" class="mchip${role === v ? ' on' : ''}" data-role="${v}" aria-pressed="${role === v}">${l}</button>`).join('')
      + `<button type="button" class="mchip mloose${this.matchLoose ? ' on' : ''}" aria-pressed="${!!this.matchLoose}" title="Loose also allows neighbor keys and tempos a few percent apart">Loose</button>`;
    let body;
    if (this.matches === null) body = '<p class="dim small">Finding matches…</p>';
    else if (!this.matches.length) body = `<p class="dim small">${esc(this.matchErr || (this.matchLoose ? 'No good matches yet. A sample needs a category, plus a tempo or key, to be matched.' : 'No exact matches: nothing shares this tempo (within 0.5%) and key. Try Loose for near matches.'))}</p>`;
    else body = `<ol class="mlist">${this.matches.map((m, i) => `<li class="mrow">
        <button type="button" class="ib mplay" data-i="${i}" aria-label="Play ${esc(m.row.name)}"><svg><use href="#i-${Player.isPlaying(m.row.id) ? 'stop' : 'play'}"/></svg></button>
        <button type="button" class="mname" data-i="${i}" title="Open ${esc(m.row.name)}"><span class="ellip">${esc(m.row.name)}</span><span class="dim mono small ellip">${esc([Fmt.catShort(m.row.category), m.row.bpm && Fmt.bpm(m.row.bpm) + ' BPM', m.row.key].filter(Boolean).join(' · '))}</span></button>
        <span class="mscore" title="Match ${Math.round(m.score * 100)}%"><i style="width:${Math.round(m.score * 100)}%"></i></span>
        <span class="mwhy">${esc(m.reasons.join(' · '))}</span></li>`).join('')}</ol>`;
    return `<section class="matches" aria-label="Goes well with"><h3 class="side-h">Goes well with</h3><div class="mchips" role="group" aria-label="Filter matches by role">${chips}</div>${body}</section>`;
  },
  bindMatches() {
    $$('#inspector .mchip:not(.mloose)').forEach((b) => { b.onclick = () => { this.matchRole = b.dataset.role; this.matches = null; this.renderInspector(); this.loadMatches(this.current.id); }; });
    $$('#inspector .mloose').forEach((b) => { b.onclick = () => { this.matchLoose = !this.matchLoose; this.matches = null; this.renderInspector(); this.loadMatches(this.current.id); }; });
    $$('#inspector .mplay').forEach((b) => { b.onclick = () => { Player.toggle(this.matches[+b.dataset.i].row); this.renderInspector(); }; });
    $$('#inspector .mname').forEach((b) => { b.onclick = () => this.inspect(this.matches[+b.dataset.i].row); });
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
      box.innerHTML = `<div class="ins-empty"><span class="ins-empty-ico" aria-hidden="true"><svg><use href="#i-play"/></svg></span><h2 class="side-h">Nothing selected</h2><p>Click a sample to hear it and see where its metadata came from. Use <kbd>↑</kbd> <kbd>↓</kbd> to step through and <kbd>Space</kbd> to play or stop.</p></div>`;
      return;
    }
    const d = this.detail || r;
    const meta = (this.detail && this.detail.meta) || null;
    const val = (k) => (meta && meta[k] ? meta[k].value : d[k]);
    const isUnk = (v) => v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length);
    const box2 = (k, label, fmt, cls) => {
      const v = val(k);
      const p = meta && meta[k] ? meta[k] : (d.prov || {})[k];
      const shown = isUnk(v) ? '<span class="unk">unknown</span>' : esc(fmt ? fmt(v) : v);
      return `<div class="mbox${cls ? ' ' + cls : ''}"><span class="mbox-h"><span class="mbox-l">${label}</span>${provBadge(p)}</span><span class="mbox-v">${shown}</span></div>`;
    };
    const plain = (label, html, cls) => `<div class="mbox${cls ? ' ' + cls : ''}"><span class="mbox-h"><span class="mbox-l">${label}</span></span><span class="mbox-v">${html}</span></div>`;
    const a = (this.detail && this.detail.audio) || null;
    const audioLine = a ? [a.format && a.format.toUpperCase(), a.sample_rate && (a.sample_rate / 1000) + ' kHz', a.bits && a.bits + '-bit', a.channels && (a.channels === 1 ? 'mono' : a.channels === 2 ? 'stereo' : a.channels + ' ch'), a.duration_s && Fmt.dur(a.duration_s)].filter(Boolean).join(' · ') : '';
    const fmtName = (a && a.format ? a.format : (d.ext || '').replace('.', '')).toUpperCase() || '—';
    const fmtSub = a ? [a.bits && a.bits + '-bit', a.sample_rate && (a.sample_rate / 1000) + ' kHz', a.channels && (a.channels === 1 ? 'mono' : a.channels === 2 ? 'stereo' : a.channels + ' ch')].filter(Boolean).join(' · ') : '';
    const durS = (a && a.duration_s) || d.duration_s;
    const tags = val('tags');
    const tagsP = meta && meta.tags ? meta.tags : (d.prov || {}).tags;
    const lt = val('loop_type');
    const playing = Player.isPlaying(d.id);
    box.innerHTML = `
      <div class="ins-top">
        <div class="ins-kicker">${catTag(val('category'))}<span class="ins-type">${esc(lt === 'loop' ? 'Loop' : lt === 'one-shot' ? 'One-shot' : lt || '')}</span>${d.kit_name ? `<span class="kitmark">kit: ${esc(d.kit_name)}</span>` : ''}</div>
        <h2 class="ins-name" title="${esc(d.name)}">${esc(d.name)}</h2>
        <p class="ins-pack">${d.pack ? `<span>${esc(shortPack(d.pack, d.manufacturer))}</span>` : `<span>${d.origin === 'library' ? 'In library' : 'No pack'}</span>`}${val('manufacturer') ? ` · <span class="dim">${esc(val('manufacturer'))}</span>` : ''}</p>
      </div>
      <div class="ins-scroll">
        <div class="ins-wave cat-${catSlug(val('category'))}"><canvas class="wave-canvas" data-wave-for="${esc(d.id)}"></canvas></div>
        <div class="ins-actions">
          <button class="btn btn-primary ins-play${playing ? ' is-playing' : ''}" type="button" id="ins-play"><svg><use href="#i-${playing ? 'stop' : 'play'}"/></svg>${playing ? 'Stop' : 'Play'}</button>
          <button class="btn" type="button" id="ins-edit" title="Edit"><svg><use href="#i-edit"/></svg>Edit</button>
          ${starBtn(d, ' ins-star').replace('tabindex="-1"', 'id="ins-star"')}
          <button class="ib ins-collb" type="button" id="ins-coll" aria-label="Add to collection" ${d.sha256 ? 'title="Add to collection…"' : `disabled title="${esc(NO_HASH_TIP)}"`}><svg viewBox="0 0 24 24"><path d="M4 7h11M4 12h11M4 17h7M18 14v6M15 17h6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
          <button class="ib" type="button" id="ins-copy" aria-label="Copy original path" title="Copy original path"><svg><use href="#i-copy"/></svg></button>
          <button class="ib" type="button" id="ins-reveal" aria-label="Show in Explorer" title="Show in Explorer"><svg><use href="#i-folder"/></svg></button>
        </div>
        ${d.preview === 'unsupported' ? `<p class="note note-warn">Preview not supported in browser for ${esc(d.ext)}.</p>` : ''}
        <div class="ins-grid">
          ${box2('bpm', 'BPM', Fmt.bpm, 'mono-v')}
          ${box2('key', 'Key', null, 'mono-v')}
          ${plain('Length', esc(durS ? Fmt.dur(durS) : '—'), 'mono-v')}
          ${d.bars ? `<div class="mbox mono-v mbox-bars"><span class="mbox-h"><span class="mbox-l">Bars</span><span class="prov prov-analysis">Analysis</span></span><span class="mbox-v">${esc(d.bars)}${d.on_grid === false ? `<small class="mbox-s">starts ${esc(Math.round(d.downbeat_ms || 0))} ms late</small>` : d.on_grid ? '<small class="mbox-s">on the downbeat</small>' : ''}</span></div>` : ''}
          ${plain('Format', `${esc(fmtName)}${fmtSub ? `<small class="mbox-s mono">${esc(fmtSub)}</small>` : ''}`)}
          ${plain('Size', esc(Fmt.bytes(d.size)))}
          ${plain('Status', statusBadge(d.review_status) || '<span class="unk">—</span>')}
          ${box2('category', 'Category', (v) => v.replace('/', ' › '), 'wide')}
          ${box2('instrument', 'Instrument')}
          ${box2('loop_type', 'Type')}
          ${box2('genre', 'Genre')}
          ${box2('manufacturer', 'Maker')}
        </div>
        <div class="ins-tags">
          <div class="ins-tags-h"><span class="mbox-l">Tags</span>${provBadge(tagsP)}</div>
          ${isUnk(tags) ? '<span class="unk">unknown</span>' : `<ul class="tag-pills">${(Array.isArray(tags) ? tags : String(tags).split(',')).map((t) => `<li class="tag-pill">#${esc(String(t).trim())}</li>`).join('')}</ul>`}
        </div>
        ${!this.detail && !this.detailErr ? '<p class="dim small">Loading details…</p>' : ''}
        ${audioLine ? `<p class="ins-audio mono">${esc(audioLine)}</p>` : ''}
        <div class="paths">
          <div class="path-blk"><span class="path-l">Original</span><code class="path" title="${esc(d.abs_path)}">${esc(d.abs_path)}</code></div>
          <div class="path-blk"><span class="path-l">${d.final_path ? 'Now at' : 'Will go to'}</span>${d.final_path || d.proposed_dest ? `<code class="path path-dest" title="${esc(d.final_path || d.proposed_dest)}">${esc(d.final_path || d.proposed_dest)}</code>` : `<span class="unk">${d.excluded ? 'Excluded: stays where it is' : 'No destination yet: held until category is known'}</span>`}</div>
        </div>
        ${d.dup_count > 1 ? `<p class="note">${d.dup_count} identical copies of this file exist. The plan keeps one physical copy.</p>` : ''}
        ${(d.warnings || []).length ? `<ul class="warns">${d.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}
        ${this.detail && this.detail.tags_raw && Object.keys(this.detail.tags_raw).length ? `<details class="raw"><summary>Embedded tags</summary><pre class="mono">${esc(JSON.stringify(this.detail.tags_raw, null, 1))}</pre></details>` : ''}
        ${this.matchesHtml(d)}
        ${(typeof Collections !== 'undefined') ? Collections.inspectorHtml(d) : ''}
        <p class="dim small mono">sha256 ${esc(Fmt.hash8(d.sha256))}…</p>
      </div>`;
    this.bindMatches();
    $('#ins-play').onclick = () => { Player.toggle(d); this.syncInsPlay(); };
    $('#ins-edit').onclick = () => this.openBulk();
    $('#ins-copy').onclick = () => copyText(d.abs_path);
    $('#ins-reveal').onclick = () => reveal({ id: d.id });
    $('#ins-star').onclick = () => Collections.star([d]);
    $('#ins-coll').onclick = () => Collections.openAdd([d]);
    $$('#inspector [data-coll-rm]').forEach((b) => { b.onclick = () => Collections.removeFrom(b.dataset.collRm, [d]); });
    if (window.Peaks) Peaks.paintAll();
    this.detailErr = null;
  },
  /* Keep the inspector's Play/Stop button in step with the player without re-rendering
     the inspector (which would recreate the waveform canvas). */
  syncInsPlay() {
    const b = $('#ins-play'); const d = this.detail || this.current;
    if (!b || !d) return;
    const on = Player.isPlaying(d.id);
    b.classList.toggle('is-playing', on);
    b.innerHTML = `<svg><use href="#i-${on ? 'stop' : 'play'}"/></svg>${on ? 'Stop' : 'Play'}`;
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
