/* collections.js: favorites (stars), named collections, smart collections (saved
   searches), the "New since last run" count and "Plan only new files".
   Membership is keyed by file content (SHA-256) on the server, so it survives rescans
   and follows a file after a run moves it. Nothing here changes the plan. */
'use strict';

const COLL_ICON = {
  manual: '<svg class="ck-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  smart: '<svg class="ck-ic" aria-hidden="true"><use href="#i-search"/></svg>',
  fav: '<svg class="ck-ic ck-star" aria-hidden="true"><use href="#i-star"/></svg>',
};

const Collections = {
  data: { favorites: { count: 0, hashes: 0 }, new: { count: 0 }, collections: [] },
  loaded: false,

  init() {
    this.buildDialog();
    $('#coll-new').addEventListener('click', () => this.openNew([]));
    $('#coll-list').addEventListener('click', (ev) => this.sideClick(ev));
    $('#coll-list').addEventListener('keydown', (ev) => {
      const b = ev.target.closest('.ck'); if (!b) return;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        const all = $$('#coll-list .ck'); const k = all.indexOf(b) + (ev.key === 'ArrowDown' ? 1 : -1);
        if (all[k]) all[k].focus();
      } else if (ev.key === 'F2' || ev.key === 'Delete') {
        const c = this.byId(b.dataset.c); if (!c) return;
        ev.preventDefault(); if (ev.key === 'F2') this.openRename(c); else this.openDelete(c);
      }
    });
    App.keyHandlers.push((ev) => {
      if (App.view !== 'library' || ev.ctrlKey || ev.metaKey || ev.altKey || (ev.key !== 's' && ev.key !== 'S')) return false;
      const rows = Library.sel.size ? [...Library.sel.values()] : [Library.currentRow()].filter(Boolean);
      if (!rows.length) { toast('Select a sample to star'); return true; }
      ev.preventDefault();
      this.star(rows);
      return true;
    });
    App.on('lib-filters', () => this.markActive());
    App.on('job-done', () => this.load());
    App.on('state', (s) => {
      const rev = s && s.collections ? s.collections.rev : null;
      if (rev !== this.rev) { this.rev = rev; if (this.loaded) this.load(); }
      this.renderReviewButton(s);
    });
  },
  afterInit() {
    const keys = $('#keys-dlg dl.keys');
    if (keys && !$('[data-coll-key]', keys)) keys.insertAdjacentHTML('beforeend', '<dt data-coll-key><kbd>S</kbd></dt><dd>Star / unstar selection</dd>');
    const pb = $('#plan-build');
    if (pb && !$('#plan-build-new')) {
      pb.insertAdjacentHTML('afterend', '<button class="btn" id="plan-build-new" type="button" hidden title="Only files that arrived since the last run are moved; everything else is kept where it is for a later plan">Plan only new files</button>');
      $('#plan-build-new').addEventListener('click', () => this.planOnlyNew());
    }
    this.load();
  },

  /* ---------- data ---------- */
  async load() {
    try {
      this.data = await API.get('/api/collections');
      this.loaded = true;
      this.renderSide();
    } catch (e) {
      $('#coll-list').innerHTML = `<li class="side-msg err">Couldn't load collections: ${esc(e.message)}</li>`;
    }
  },
  byId(id) { return (this.data.collections || []).find((c) => c.id === id) || null; },
  name(id) { if (id === 'fav') return 'Favorites'; const c = this.byId(id); return c ? c.name : null; },
  manual() { return (this.data.collections || []).filter((c) => c.kind === 'manual'); },

  /* ---------- sidebar ---------- */
  renderSide() {
    const d = this.data;
    const row = (id, kind, name, n, tools) => `<li class="ck-row"><button type="button" class="ck" data-c="${esc(id)}" data-kind="${kind}" title="${esc(name)}${kind === 'smart' ? ' (saved search)' : ''}">${COLL_ICON[kind]}<span class="ck-name ellip">${esc(name)}</span><span class="ck-n mono">${Fmt.num(n)}</span></button>${tools ? `<span class="ck-tools"><button type="button" class="ib" data-ren="${esc(id)}" aria-label="Rename ${esc(name)}" title="Rename (F2)"><svg><use href="#i-edit"/></svg></button><button type="button" class="ib" data-del="${esc(id)}" aria-label="Delete ${esc(name)}" title="Delete (Del)"><svg><use href="#i-x"/></svg></button></span>` : ''}</li>`;
    const cs = d.collections || [];
    const man = cs.filter((c) => c.kind === 'manual'), smart = cs.filter((c) => c.kind === 'smart');
    $('#coll-list').innerHTML = row('fav', 'fav', 'Favorites', d.favorites.count, false) +
      man.map((c) => row(c.id, 'manual', c.name, c.count, true)).join('') +
      (smart.length ? '<li class="ck-sep" aria-hidden="true">Smart</li>' + smart.map((c) => row(c.id, 'smart', c.name, c.count, true)).join('') : '') +
      (cs.length ? '' : '<li class="side-msg ck-hint">Select samples and choose <em>Add to collection</em>, or save a search.</li>');
    $('#lib-new-n').textContent = Fmt.num((d.new || {}).count || 0);
    this.markActive();
  },
  isSmartActive(c) {
    const norm = (p) => JSON.stringify(Object.keys(p).filter((k) => p[k] !== '' && p[k] !== null && p[k] !== undefined).sort().map((k) => [k, String(p[k])]));
    return norm(c.params || {}) === norm(Library.params());
  },
  markActive() {
    $$('#coll-list .ck').forEach((b) => {
      const id = b.dataset.c, c = this.byId(id);
      const on = b.dataset.kind === 'smart' ? !!c && this.isSmartActive(c) : !!Library.coll && Library.coll.id === id;
      b.classList.toggle('on', on);
      if (on) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
  },
  sideClick(ev) {
    const ren = ev.target.closest('[data-ren]'), del = ev.target.closest('[data-del]');
    if (ren) { const c = this.byId(ren.dataset.ren); if (c) this.openRename(c); return; }
    if (del) { const c = this.byId(del.dataset.del); if (c) this.openDelete(c); return; }
    const b = ev.target.closest('.ck'); if (!b) return;
    if (App.view !== 'library') Shell.go('library');
    const id = b.dataset.c;
    if (b.dataset.kind === 'smart') {
      const c = this.byId(id); if (!c) return;
      Library.restoreParams(c.params);
      toast(`Opened smart collection “${c.name}”`);
      return;
    }
    if (Library.coll && Library.coll.id === id) Library.setColl(null);
    else Library.setColl({ id, name: this.name(id) || 'Collection' });
  },

  /* ---------- row patching ---------- */
  patchRows(ids, fn) {
    const set = new Set(ids);
    for (const [, items] of Library.table.pages) if (Array.isArray(items)) items.forEach((r) => { if (set.has(r.id)) fn(r); });
    for (const r of Library.sel.values()) if (set.has(r.id)) fn(r);
    if (Library.kitMembers) Object.values(Library.kitMembers).forEach((r) => { if (set.has(r.id)) fn(r); });
    if (Library.current && set.has(Library.current.id)) fn(Library.current);
    if (Library.detail && set.has(Library.detail.id)) fn(Library.detail);
    Library.table.render();
    Library.renderInspector();
  },
  afterChange(affected, fn) {
    this.patchRows(affected || [], fn);
    if (Library.coll) Library.table.refresh();
    this.load();
  },

  /* ---------- actions ---------- */
  async star(rows) {
    rows = (rows || []).filter(Boolean);
    if (!rows.length) return;
    const ok = rows.filter((r) => r.sha256);
    if (!ok.length) { toast('Can’t star: this file could not be fingerprinted (SHA-256 failed), so it has no stable identity. Rescan after fixing the file.', 'warn'); return; }
    try {
      const res = await API.post('/api/collections/star', { ids: rows.map((r) => r.id) });
      this.afterChange(res.affected_ids, (r) => { r.starred = res.starred; });
      const twins = (res.affected_ids || []).length - ok.length;
      toast(`${res.starred ? 'Starred' : 'Unstarred'} ${rows.length === 1 ? rows[0].name : Fmt.num(rows.length) + ' samples'}${twins > 0 ? ` (and ${twins} identical cop${twins === 1 ? 'y' : 'ies'})` : ''}`);
      if (res.skipped && res.skipped.length) toast(`${res.skipped.length} file${res.skipped.length === 1 ? '' : 's'} could not be starred: no fingerprint (hash error)`, 'warn');
    } catch (e) { toast('Not starred: ' + e.message, 'err'); }
  },
  async addTo(id, rows) {
    const res = await API.post('/api/collections/add', { id, ids: rows.map((r) => r.id) });
    this.afterChange(res.affected_ids, (r) => { r.collections = [...new Set([...(r.collections || []), id])]; });
    toast(`Added ${rows.length === 1 ? rows[0].name : Fmt.num(rows.length) + ' samples'} to “${res.collection.name}”`);
    return res;
  },
  async removeFrom(id, rows) {
    try {
      const res = await API.post('/api/collections/remove', { id, ids: rows.map((r) => r.id) });
      this.afterChange(res.affected_ids, (r) => { r.collections = (r.collections || []).filter((x) => x !== id); });
      toast(`Removed from “${res.collection.name}”`);
    } catch (e) { toast('Not removed: ' + e.message, 'err'); }
  },
  async planOnlyNew() {
    try {
      await API.post('/api/plan/build', { only_new: true });
      toast('Building a plan for new files only. Everything else stays where it is.');
      Shell.pollSoon();
      if (App.view !== 'review') Shell.go('review');
    } catch (e) { toast(e.status === 409 ? 'Can’t build now: ' + e.message : 'Plan not built: ' + e.message, 'err'); }
  },
  renderReviewButton(s) {
    const b = $('#plan-build-new'); if (!b) return;
    const n = (s && s.scan_summary && s.scan_summary.new_files) || 0;
    b.hidden = !n;
    b.textContent = `Plan only new files (${Fmt.num(n)})`;
    b.disabled = !!(s && s.progress && s.progress.running);
  },

  /* ---------- inspector ---------- */
  inspectorHtml(d) {
    if (!d.sha256) return `<p class="note">${esc(NO_HASH_TIP)}.</p>`;
    const ids = (d.collections || []).filter((id) => this.byId(id));
    const chips = (d.starred ? [`<span class="cchip cchip-fav">${COLL_ICON.fav}Favorites</span>`] : [])
      .concat(ids.map((id) => `<span class="cchip">${esc(this.name(id))}<button type="button" class="chip-x" data-coll-rm="${esc(id)}" aria-label="Remove from ${esc(this.name(id))}" title="Remove from collection"><svg><use href="#i-x"/></svg></button></span>`));
    return `<div class="ins-colls"><span class="path-l">Collections</span>${chips.length ? `<div class="cchips">${chips.join('')}</div>` : '<span class="dim small">Not in any collection</span>'}${d.dup_count > 1 && (d.starred || ids.length) ? '<span class="dim small">Shared by all identical copies: collections follow the content.</span>' : ''}</div>`;
  },

  /* ---------- dialog ---------- */
  buildDialog() {
    document.body.insertAdjacentHTML('beforeend', `
<dialog class="dlg" id="coll-dlg" aria-labelledby="coll-dlg-h">
  <form method="dialog" class="dlg-body" id="coll-form">
    <h2 id="coll-dlg-h"></h2>
    <p id="coll-dlg-p" class="dim"></p>
    <div id="coll-pick-wrap" hidden>
      <label class="field-label" for="coll-pick">Collection</label>
      <select class="input" id="coll-pick"></select>
    </div>
    <div id="coll-name-wrap">
      <label class="field-label" for="coll-name">Name</label>
      <input class="input" id="coll-name" type="text" maxlength="80" autocomplete="off" placeholder="e.g. Trap drums">
    </div>
    <div class="dlg-err" id="coll-err" role="alert" hidden></div>
    <div class="dlg-actions">
      <button class="btn" value="cancel" formnovalidate>Cancel</button>
      <button class="btn btn-primary" id="coll-go" type="button">Save</button>
    </div>
  </form>
</dialog>`);
    $('#coll-go').addEventListener('click', () => this.submit());
    $('#coll-name').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); this.submit(); } });
    $('#coll-pick').addEventListener('change', () => this.syncPick());
  },
  dlg(mode, opt) {
    this.mode = mode; this.opt = opt || {};
    $('#coll-dlg-h').textContent = opt.title;
    $('#coll-dlg-p').innerHTML = opt.text || '';
    $('#coll-dlg-p').hidden = !opt.text;
    $('#coll-pick-wrap').hidden = mode !== 'add';
    $('#coll-name-wrap').hidden = mode === 'delete';
    $('#coll-name').value = opt.name || '';
    $('#coll-go').textContent = opt.go || 'Save';
    $('#coll-go').className = 'btn ' + (mode === 'delete' ? 'btn-danger' : 'btn-primary');
    $('#coll-err').hidden = true;
    if (mode === 'add') {
      const man = this.manual();
      $('#coll-pick').innerHTML = man.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${Fmt.num(c.count)})</option>`).join('') + '<option value="__new">New collection…</option>';
      const last = Store.get('coll.last', null);
      $('#coll-pick').value = man.some((c) => c.id === last) ? last : (man[0] ? man[0].id : '__new');
      this.syncPick();
    }
    $('#coll-dlg').showModal();
    (mode === 'add' ? $('#coll-pick') : mode === 'delete' ? $('#coll-go') : $('#coll-name')).focus();
    if (mode !== 'add' && mode !== 'delete') $('#coll-name').select();
  },
  syncPick() { $('#coll-name-wrap').hidden = $('#coll-pick').value !== '__new'; },
  err(msg) { const e = $('#coll-err'); e.textContent = msg; e.hidden = false; },
  openNew(rows) { this.dlg('new', { title: 'New collection', text: rows.length ? `${Fmt.num(rows.length)} selected sample${rows.length === 1 ? '' : 's'} will be added.` : 'Collections live in the app data folder. Files are never moved or copied.', go: 'Create', rows }); },
  openAdd(rows) {
    rows = (rows || []).filter(Boolean);
    if (!rows.length) { toast('Select one or more samples first'); return; }
    if (!rows.some((r) => r.sha256)) { toast(NO_HASH_TIP, 'warn'); return; }
    this.dlg('add', { title: `Add ${rows.length === 1 ? rows[0].name : Fmt.num(rows.length) + ' samples'} to a collection`, go: 'Add', rows,
      text: rows.some((r) => r.dup_count > 1) ? 'Identical copies of a file share its collections.' : '' });
  },
  openRename(c) { this.dlg('rename', { title: `Rename ${c.kind === 'smart' ? 'smart collection' : 'collection'}`, name: c.name, go: 'Rename', c }); },
  openDelete(c) { this.dlg('delete', { title: `Delete “${c.name}”?`, text: c.kind === 'smart' ? 'The saved search is removed. No files are affected.' : 'The collection is removed. Its samples stay in your library and keep their stars.', go: 'Delete', c }); },
  openSaveSmart(params) {
    const p = Object.assign({}, params);
    const bits = [p.q && `“${p.q}”`, p.pack, p.category && Fmt.catShort(p.category), p.instrument, p.key, p.genre].filter(Boolean);
    this.dlg('smart', { title: 'Save search as smart collection', text: 'Opening it later restores this search, these filters, the pack and the Show all files setting.', name: bits.slice(0, 3).join(' · ').slice(0, 80), go: 'Save', params: p });
  },
  async submit() {
    const name = $('#coll-name').value.trim();
    const o = this.opt;
    const btn = $('#coll-go'); btn.disabled = true;
    try {
      if (this.mode === 'new') {
        if (!name) { this.err('Give the collection a name.'); return; }
        const res = await API.post('/api/collections/create', { name, ids: (o.rows || []).map((r) => r.id) });
        toast(`Collection “${res.collection.name}” created`);
      } else if (this.mode === 'add') {
        let id = $('#coll-pick').value;
        if (id === '__new') {
          if (!name) { this.err('Give the new collection a name.'); return; }
          id = (await API.post('/api/collections/create', { name })).collection.id;
        }
        Store.set('coll.last', id);
        await this.addTo(id, o.rows);
      } else if (this.mode === 'rename') {
        if (!name) { this.err('Name required.'); return; }
        await API.post('/api/collections/rename', { id: o.c.id, name });
        if (Library.coll && Library.coll.id === o.c.id) { Library.coll.name = name; Library.renderChips(); }
      } else if (this.mode === 'delete') {
        await API.post('/api/collections/delete', { id: o.c.id });
        if (Library.coll && Library.coll.id === o.c.id) Library.setColl(null);
        toast(`Deleted “${o.c.name}”`);
      } else if (this.mode === 'smart') {
        if (!name) { this.err('Give the smart collection a name.'); return; }
        const res = await API.post('/api/collections/smart', { name, params: o.params });
        toast(`Saved smart collection “${res.collection.name}”`);
      }
      $('#coll-dlg').close();
      await this.load();
    } catch (e) { this.err(e.message); } finally { btn.disabled = false; }
  },
};
App.features.push(Collections);

/* ---------- Fixture mode (?fixture=1): in-memory collections ---------- */
(function () {
  const FX_NEW_PACK = 'Velvet Room - Late Set Synths';
  const fx = { favs: new Set(), colls: [], items: null, n: 0 };
  function setup(items) {
    if (fx.items) return;
    fx.items = items;
    items.forEach((it) => { it.is_new = it.pack === FX_NEW_PACK; it.starred = false; it.collections = []; });
  }
  function sync() {
    for (const it of fx.items) {
      it.starred = fx.favs.has(it.sha256);
      it.collections = fx.colls.filter((c) => c.kind === 'manual' && c.members.has(it.sha256)).map((c) => c.id);
    }
  }
  const pub = (c, count) => Object.assign({ id: c.id, name: c.name, kind: c.kind, count }, c.kind === 'smart' ? { params: c.params } : { members: c.members.size });
  function hashesOf(ids) { const byId = new Map(fx.items.map((i) => [i.id, i])); return [...new Set(ids.map((id) => (byId.get(id) || {}).sha256).filter(Boolean))]; }
  function affected(hs) { const s = new Set(hs); return fx.items.filter((i) => s.has(i.sha256)).map((i) => i.id); }
  function cleanName(name, except) {
    const n = String(name || '').trim();
    if (!n) throw new Error('Give the collection a name');
    if (n.toLowerCase() === 'favorites') throw new Error('“Favorites” is reserved for starred samples');
    if (fx.colls.some((c) => c.id !== except && c.name.toLowerCase() === n.toLowerCase())) throw new Error('A collection with that name already exists');
    return n;
  }
  window.FixtureExt.push((method, p, q, body, st, h) => {
    setup(h.items);
    if (!p.startsWith('/api/collections')) return null;
    try {
      if (method === 'GET' && p === '/api/collections') {
        const cnt = (hs) => fx.items.filter((i) => hs.has(i.sha256)).length;
        return h.ok({ favorites: { count: cnt(fx.favs), hashes: fx.favs.size }, new: { count: fx.items.filter((i) => i.is_new).length },
          collections: fx.colls.map((c) => pub(c, c.kind === 'smart' ? c.count || 0 : cnt(c.members))) });
      }
      if (method !== 'POST') return null;
      const get = (id) => { const c = fx.colls.find((x) => x.id === id); if (!c) throw new Error('Unknown collection ' + id); return c; };
      let res;
      if (p === '/api/collections/star') {
        const hs = hashesOf(body.ids || []);
        const on = body.on === undefined || body.on === null ? !hs.every((x) => fx.favs.has(x)) : !!body.on;
        hs.forEach((x) => (on ? fx.favs.add(x) : fx.favs.delete(x)));
        res = { ok: true, starred: on, hashes: hs, affected_ids: affected(hs), skipped: [] };
      } else if (p === '/api/collections/create') {
        const c = { id: 'c' + (++fx.n), name: cleanName(body.name), kind: 'manual', members: new Set(hashesOf(body.ids || [])) };
        fx.colls.push(c); res = { ok: true, collection: pub(c) };
      } else if (p === '/api/collections/smart') {
        const params = Object.assign({}, body.params || {});
        if (!Object.keys(params).length) throw new Error('Set a search or at least one filter first');
        const c = { id: 's' + (++fx.n), name: cleanName(body.name), kind: 'smart', params, members: new Set(), count: 0 };
        fx.colls.push(c); res = { ok: true, collection: pub(c) };
      } else if (p === '/api/collections/rename') {
        const c = get(body.id); c.name = cleanName(body.name, c.id); res = { ok: true, collection: pub(c) };
      } else if (p === '/api/collections/delete') {
        fx.colls.splice(fx.colls.indexOf(get(body.id)), 1); res = { ok: true };
      } else if (p === '/api/collections/add' || p === '/api/collections/remove') {
        const c = get(body.id); const hs = hashesOf(body.ids || []);
        hs.forEach((x) => (p.endsWith('add') ? c.members.add(x) : c.members.delete(x)));
        res = { ok: true, changed: hs.length, hashes: hs, affected_ids: affected(hs), skipped: [], collection: pub(c) };
      } else return null;
      sync();
      return h.ok(res);
    } catch (e) { return h.err(400, e.message); }
  });
  // Filters the core fixture does not know (fav, collection, new) and extra state fields.
  if (!window.FixtureAPI) return;
  const orig = window.FixtureAPI.handle.bind(window.FixtureAPI);
  window.FixtureAPI.handle = async function (method, url, body) {
    if (!fx.items) await orig('GET', '/api/collections');
    const u = new URL(url, 'http://fixture.local');
    if (method === 'GET' && u.pathname === '/api/collections') {
      const r = await orig(method, url, body);
      for (const c of (r.body && r.body.collections) || []) {
        if (c.kind !== 'smart') continue;
        const s = await window.FixtureAPI.handle('GET', '/api/samples?' + new URLSearchParams(Object.assign({}, c.params, { limit: '1' })).toString());
        c.count = s.status === 200 ? s.body.total : 0;
      }
      return r;
    }
    if (method === 'GET' && u.pathname === '/api/state') {
      const r = await orig(method, url, body);
      if (r.status === 200) {
        const n = fx.items.filter((i) => i.is_new && i.origin === 'incoming');
        r.body.scan_summary = Object.assign({}, r.body.scan_summary || {}, { new_files: n.length, new_packs: new Set(n.map((i) => i.pack)).size, new_bytes: n.reduce((s, i) => s + i.size, 0) });
        r.body.collections = { favorites: fx.favs.size, manual: fx.colls.filter((c) => c.kind === 'manual').length, smart: fx.colls.filter((c) => c.kind === 'smart').length, rev: fx.n + fx.favs.size };
      }
      return r;
    }
    if (method === 'GET' && u.pathname === '/api/samples') {
      const q = u.searchParams;
      let coll = q.get('collection');
      const smart = coll && fx.colls.find((c) => c.id === coll && c.kind === 'smart');
      if (smart) { for (const [k, v] of Object.entries(smart.params)) if (!q.get(k)) q.set(k, v); q.delete('collection'); coll = q.get('collection'); }
      const fav = q.get('fav') === '1', onlyNew = q.get('new') === '1';
      if (!fav && !coll && !onlyNew) return orig(method, '/api/samples?' + q.toString(), body);
      const off = +(q.get('offset') || 0), lim = +(q.get('limit') || 100);
      ['fav', 'collection', 'new'].forEach((k) => q.delete(k));
      q.set('offset', '0'); q.set('limit', '1000000');
      const r = await orig(method, '/api/samples?' + q.toString(), body);
      if (r.status !== 200) return r;
      const c = coll && fx.colls.find((x) => x.id === coll);
      if (coll && !c) return { status: 404, body: { error: 'Unknown collection ' + coll } };
      const rows = r.body.items.filter((i) => (!fav || fx.favs.has(i.sha256)) && (!c || c.members.has(i.sha256)) && (!onlyNew || i.is_new));
      return { status: 200, body: { total: rows.length, offset: off, items: rows.slice(off, off + lim) } };
    }
    return orig(method, url, body);
  };
})();
