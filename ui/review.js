/* review.js: Review view — plan build, summary, grouped op table, op detail,
   kit panel, approval and execution. */
'use strict';

function actionBadge(a) { return `<span class="act act-${esc(a)}">${esc(ACTION_LABEL[a] || a)}</span>`; }
const FLAG_LABEL = { duplicate: 'duplicate', 'name-collision': 'name collision', 'low-confidence': 'low confidence', 'unknown-category': 'unknown category', 'kit-held': 'kit held', warning: 'warning' };
function flagList(fl) { return (fl || []).map((f) => `<span class="flag flag-${esc(f)}">${esc(FLAG_LABEL[f] || f)}</span>`).join(''); }
/* Undo plans (samplelib/undo.py) carry summary.kind === 'undo'. Their ops keep the
   original file name and pack in orig_name / orig_pack, since a rescan may have
   given the moved files new item ids. */
function isUndoPlan(p) { return !!(p && p.summary && p.summary.kind === 'undo'); }
function opName(o) { return o.name || o.orig_name || String(o.source || '').split(/[\\/]/).pop(); }
function isUndoOp(o) { return (o.flags || []).includes('undo'); }
function plural(n, one, many) { return `${Fmt.num(n || 0)} ${(n || 0) === 1 ? one : many}`; }
function relRoot(p) {
  const r = App.state && App.state.root;
  if (p && r && p.toLowerCase().startsWith(r.toLowerCase())) return p.slice(r.length).replace(/^[\\/]/, '');
  return p || '';
}
function relDest(p) {
  const s = App.state && App.state.dest_root;
  if (p && s && p.toLowerCase().startsWith(s.toLowerCase())) return 'Samples' + p.slice(s.length);
  if (p && App.state && App.state.root && p.toLowerCase().startsWith(App.state.root.toLowerCase())) return relRoot(p); // undo: back outside Samples
  return p || '';
}

const Review = {
  group: Store.get('rev.group', 'pack'),
  action: '', flag: '', q: '',
  kitId: null,
  kitSel: new Set(),

  init() {
    const groupKey = (o) => {
      switch (this.group) {
        case 'pack': return o.pack || o.orig_pack || '(no pack)';
        case 'kit': return o.kit_name || 'Not in a kit';
        case 'category': return o.category ? o.category.replace('/', ' › ') : 'Unsorted';
        case 'action': return ACTION_LABEL[o.action] || o.action;
        case 'warning': return (o.flags && o.flags.length) ? (FLAG_LABEL[o.flags[0]] || o.flags[0]) : 'No flags';
        default: return '';
      }
    };
    this.groupKey = groupKey;
    this.table = new VTable($('#op-table'), {
      label: 'Planned operations', rowHeight: 50, pageSize: 200,
      columns: [
        { key: 'g', label: 'Group', width: 'minmax(80px, .6fr)', cls: 'c-grp', title: (o) => groupKey(o), render: (o, i, prev) => { const g = groupKey(o); return !prev || groupKey(prev) !== g ? `<span class="ellip">${esc(g)}</span>` : '<span class="ditto" aria-hidden="true"></span>'; } },
        { key: 'a', label: 'Action', width: '66px', render: (o) => actionBadge(o.action) },
        { key: 'src', label: 'Original', width: 'minmax(110px, 1.1fr)', cls: 'c-path', title: (o) => o.source, render: (o) => `<span class="ellip">${esc(isUndoOp(o) ? relDest(o.source) : (o.rel_path || o.source))}</span>` },
        { key: 'arrow', label: '', width: '14px', cls: 'c-arrow', render: (o) => (o.dest ? '→' : '') },
        { key: 'dst', label: 'Proposed', width: 'minmax(110px, 1.3fr)', cls: 'c-path c-dest', title: (o) => o.dest || o.canonical || o.reason, render: (o) => isUndoOp(o) ? `<span class="ellip${o.action === 'move' ? '' : ' dim'}">${esc(o.action === 'unlink' ? 'link removed; file stays at ' + relDest(o.link_to) : o.action === 'move' ? relRoot(o.dest) : 'stays where it is')}</span>` : o.dest ? `<span class="ellip">${esc(relDest(o.dest))}</span>` : `<span class="ellip dim">${o.action === 'keep' ? 'stays; copy kept at ' + esc(relDest(o.canonical)) : 'stays where it is'}</span>` },
        { key: 'why', label: 'Reason', width: 'minmax(150px, 1.6fr)', cls: 'c-why', title: (o) => o.reason, render: (o) => `${flagList(o.flags)}<span class="ellip dim">${esc(o.reason || '')}</span>` },
      ],
      rowClass: (o, i, prev) => `row-act-${esc(o.action || 'keep')}${prev && groupKey(prev) !== groupKey(o) ? ' grp-start' : ''}`,
      fetch: (offset, limit) => API.get('/api/plan', { group: this.group, action: this.action, flag: this.flag, q: this.q, offset, limit }).then((r) => Object.assign(r, { items: r.items || r.ops || [] })),
      onTotal: (t, res) => { $('#op-count').textContent = Fmt.num(t) + ' operations'; this.planRes = res; this.syncPlan(res); },
      onRowClick: (i, o) => this.showOp(o),
      onKeyMove: (i, o) => { if (o) this.showOp(o); },
      emptyHtml: () => this.hasPlan() ? '<div><strong>No operations match.</strong><p>Change the action or flag filter.</p></div>' : '<div><strong>No plan yet.</strong><p>Build a plan to see exactly where every file would go. Building only reads.</p><button class="btn btn-small btn-primary" type="button" data-empty-act="build">Build plan</button></div>',
      onEmptyAction: (a) => { if (a === 'build') this.build(); },
    });
    $$('#op-group [role=radio]').forEach((b) => {
      b.setAttribute('aria-checked', b.dataset.g === this.group ? 'true' : 'false');
      b.addEventListener('click', () => {
        this.group = b.dataset.g; Store.set('rev.group', this.group);
        $$('#op-group [role=radio]').forEach((x) => x.setAttribute('aria-checked', x === b ? 'true' : 'false'));
        this.table.reset();
      });
    });
    segKeys($('#op-group'));
    $('#op-action').addEventListener('change', () => { this.action = $('#op-action').value; this.table.reset(); });
    $('#op-flag').addEventListener('change', () => { this.flag = $('#op-flag').value; this.table.reset(); });
    $('#op-q').addEventListener('input', debounce(() => { this.q = $('#op-q').value.trim(); this.table.reset(); }, 150));
    $('#stale-banner').insertAdjacentHTML('afterend', '<div class="banner banner-undo" id="undo-banner" role="status" hidden></div>');
    $('#op-action').insertAdjacentHTML('beforeend', '<option value="unlink">Unlink</option>');
    $('#plan-build').addEventListener('click', () => this.build());
    $('#stale-rebuild').addEventListener('click', () => this.build());
    $('#plan-approve-open').addEventListener('click', () => this.openApprove());
    $('#approve-check').addEventListener('change', () => { $('#approve-go').disabled = !$('#approve-check').checked; });
    $('#approve-go').addEventListener('click', () => this.approve());
    $('#plan-run').addEventListener('click', () => this.openRun());
    $('#run-go').addEventListener('click', () => this.run());
    $('#rtab-ops').addEventListener('click', () => this.setTab('ops'));
    $('#rtab-kits').addEventListener('click', () => this.setTab('kits'));
    $('#kit-q').addEventListener('input', debounce(() => this.loadKits(), 150));
    $('#kit-list').addEventListener('click', (ev) => { const b = ev.target.closest('.kitrow'); if (b) this.openKit(b.dataset.id); });
    $('#kit-list').addEventListener('keydown', (ev) => {
      const b = ev.target.closest('.kitrow'); if (!b || (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp')) return;
      ev.preventDefault(); const all = $$('#kit-list .kitrow'); const n = all[all.indexOf(b) + (ev.key === 'ArrowDown' ? 1 : -1)];
      if (n) { n.focus(); this.openKit(n.dataset.id); }
    });
    App.on('state', (s) => this.renderPlanHead(s));
    App.on('edited', () => { if (this.shown) this.table.refresh(); });
    App.on('job-done', (job) => {
      const undo = isUndoPlan(this.plan());
      if (job === 'plan' && undo) { this.action = this.flag = ''; $('#op-action').value = ''; $('#op-flag').value = ''; this.renderCards(this.plan()); } // a new undo plan opens unfiltered
      if (job === 'plan') { toast(undo ? 'Undo plan built. Review it, then approve.' : 'Plan built. Review it, then approve.'); this.table.reset(); this.loadKits(); }
      if (job === 'execute' || job === 'export') { toast(undo ? 'Undo finished. See Move Log, then rescan your packs folder.' : 'Approved plan finished running. See Move Log for every result.'); Shell.go('log'); }
    });
  },
  hasPlan() { return !!(App.state && App.state.plan); },
  plan() { return App.state && App.state.plan; },

  setTab(t) {
    this.tab = t;
    $('#rtab-ops').setAttribute('aria-selected', t === 'ops' ? 'true' : 'false');
    $('#rtab-kits').setAttribute('aria-selected', t === 'kits' ? 'true' : 'false');
    $('#rpane-ops').hidden = t !== 'ops';
    $('#rpane-kits').hidden = t !== 'kits';
    if (t === 'kits') this.loadKits(); else this.table.schedule();
  },

  syncPlan(res) {
    const p = this.plan();
    if (p && res && (p.plan_hash !== res.plan_hash || p.stale !== res.stale || p.approved !== res.approved)) App.refreshState();
  },

  renderPlanHead(s) {
    const p = s && s.plan;
    const running = s && s.progress && s.progress.running;
    const exec = s && s.execution;
    const undo = isUndoPlan(p);
    $('#plan-build').textContent = undo ? 'Rebuild undo plan' : p ? 'Rebuild plan' : 'Build plan';
    this.renderUndoBanner(s);
    $('#plan-build').disabled = !!running || !(s && s.has_inventory);
    $('#plan-build').title = s && !s.has_inventory ? 'Scan your packs first' : '';
    const stale = !!(p && p.stale), approved = !!(p && p.approved && !p.stale);
    $('#stale-banner').hidden = !stale;
    $('#rail-review-dot').hidden = !stale;
    $('#plan-approve-open').disabled = !p || stale || approved || !!running;
    $('#plan-approve-open').textContent = p ? `Approve ${undo ? 'undo plan' : 'plan'} ${Fmt.hash8(p.plan_hash)}…` : 'Approve plan…';
    $('#plan-run').disabled = !approved || !!running;
    $('#plan-run').title = approved ? '' : 'Approve the plan first';
    let st = '<span class="ps ps-none">No plan</span>';
    if (p) st = stale ? '<span class="ps ps-stale">Stale, approval cleared</span>' : approved ? `<span class="ps ps-ok">Approved ${esc(Fmt.hash8(p.plan_hash))}</span>` : '<span class="ps ps-wait">Waiting for your approval</span>';
    if (running && s.progress.job !== 'scan') st = `<span class="ps ps-run">${esc(s.progress.job)} running</span>`;
    $('#plan-state').innerHTML = st;
    $('#plan-meta').innerHTML = p ? `${undo ? 'Undo plan' : 'Plan'} <code class="mono" title="${esc(p.plan_hash)}">${esc(Fmt.hash8(p.plan_hash))}</code> built ${esc(Fmt.time(p.created))}${exec && exec.ran ? `. Last run ${esc(Fmt.time(exec.ran))}` : ''}` : (s && s.has_inventory ? 'Build a plan to see where every file would go. Building only reads.' : 'Scan your packs folder first, then build a plan.');
    this.renderCards(p);
    const hs = $$('#op-table .vt-head [role=columnheader]');
    if (hs[2]) hs[2].textContent = undo ? 'Now at' : 'Original';
    if (hs[4]) hs[4].textContent = undo ? 'Goes back to' : 'Proposed';
  },

  renderUndoBanner(s) {
    const p = s && s.plan, b = $('#undo-banner');
    if (!isUndoPlan(p)) { b.hidden = true; return; }
    const sm = p.summary, ba = sm.by_action || {};
    const rescan = s.undo && s.undo.rescan_needed;
    b.hidden = false;
    b.innerHTML = `<strong>Undo plan</strong><span class="ub-text">Reverses the run of ${esc(Fmt.time(typeof sm.run_ts === 'number' ? new Date(sm.run_ts * 1000).toISOString() : sm.run_ts))} (plan <code class="mono">${esc(Fmt.hash8(sm.run_plan_hash))}</code>): ${plural(ba.move, 'file goes', 'files go')} back to their original folders and ${plural(ba.unlink, 'kit link is', 'kit links are')} removed.${ba.hold ? ` ${plural(ba.hold, 'held file stays where it is', 'held files stay where they are')}.` : ''}${rescan ? ' <span class="ub-warn">This undo has run: rescan before building a new plan.</span>' : ''}</span>`;
  },

  renderCards(p) {
    const box = $('#plan-cards');
    if (!p || !p.summary) { box.innerHTML = ''; return; }
    const s = p.summary, ba = s.by_action || {}, bb = s.bytes_by_action || {};
    // Stat tiles: action tiles are filters on the action color; the bar is that action's share of all files.
    const tot = s.total_files || 0;
    const share = (n) => (tot ? Math.min(100, Math.round((n || 0) / tot * 100)) : 0);
    const card = (k, v, sub, cls, act, pct) => `<button type="button" class="card ${cls || ''}${act && this.action === act ? ' on' : ''}" ${act ? `data-act="${act}" aria-pressed="${this.action === act}"` : 'disabled'}><span class="card-k">${k}</span><span class="card-v mono">${v}</span>${sub ? `<span class="card-s mono">${sub}</span>` : ''}<span class="card-bar" aria-hidden="true"><i style="--p:${pct || 0}%"></i></span></button>`;
    box.classList.toggle('cards-undo', isUndoPlan(p));
    box.innerHTML = isUndoPlan(p) ? [
      card('files in undo plan', Fmt.num(s.total_files), Fmt.bytes(s.total_bytes), 'card-total', '', tot ? 100 : 0),
      card('move back', Fmt.num(ba.move || 0), bb.move !== undefined ? Fmt.bytes(bb.move) : '', 'c-move', 'move', share(ba.move)),
      card('remove kit link', Fmt.num(ba.unlink || 0), 'file stays', 'c-unlink', 'unlink', share(ba.unlink)),
      card('already back', Fmt.num(ba.keep || 0), 'nothing to do', 'c-keep', 'keep', share(ba.keep)),
      card('held for review', Fmt.num(ba.hold || 0), 'not touched', 'c-hold', 'hold', share(ba.hold)),
    ].join('') : [
      card('files in plan', Fmt.num(s.total_files), Fmt.bytes(s.total_bytes), 'card-total', '', tot ? 100 : 0),
      card('move', Fmt.num(ba.move || 0), bb.move !== undefined ? Fmt.bytes(bb.move) : '', 'c-move', 'move', share(ba.move)),
      card('link (kit copies)', Fmt.num(ba.link || 0), bb.link !== undefined ? Fmt.bytes(bb.link) + ' not copied' : '', 'c-link', 'link', share(ba.link)),
      card('keep in place', Fmt.num(ba.keep || 0), 'duplicates', 'c-keep', 'keep', share(ba.keep)),
      card('held for review', Fmt.num(ba.hold || 0), 'not touched', 'c-hold', 'hold', share(ba.hold)),
      card('excluded', Fmt.num(ba.exclude || 0), '', 'c-exclude', 'exclude', share(ba.exclude)),
      card('duplicate groups', Fmt.num(s.duplicate_groups), Fmt.bytes(s.duplicate_bytes_avoided) + ' avoided', 'card-info', '', s.total_bytes ? Math.min(100, Math.round((s.duplicate_bytes_avoided || 0) / s.total_bytes * 100)) : 0),
      card('name collisions', Fmt.num(s.collisions), 'renamed with pack', 'card-info', '', share(s.collisions)),
    ].join('');
    box.classList.toggle('has-on', !!this.action);
    $$('#plan-cards .card[data-act]').forEach((c) => c.addEventListener('click', () => {
      this.action = this.action === c.dataset.act ? '' : c.dataset.act;
      $('#op-action').value = this.action;
      this.renderCards(this.plan());
      this.setTab('ops');
      this.table.reset();
    }));
  },

  /* ---------- op detail ---------- */
  showOp(o) {
    const box = $('#op-detail');
    box.hidden = false;
    const row = (l, v, cls) => v ? `<div class="path-blk"><span class="path-l">${l}</span><code class="path ${cls || ''}" title="${esc(v)}">${esc(v)}</code></div>` : '';
    if ((o.flags || []).includes('undo')) return this.showUndoOp(o, box, row);
    box.innerHTML = `
      <div class="od-head">${actionBadge(o.action)}<h3 class="ellip" title="${esc(o.name)}">${esc(o.name)}</h3>
        <button type="button" class="ib" id="od-close" aria-label="Close details"><svg><use href="#i-x"/></svg></button></div>
      <p class="od-why">${esc(o.reason || '')}</p>
      <div>${flagList(o.flags)}</div>
      ${row('Original', o.source)}
      ${row('Proposed', o.dest, 'path-dest')}
      ${o.link_to ? row('Hard link to', o.link_to) : ''}
      ${o.canonical && !o.link_to ? row('Copy kept at', o.canonical) : ''}
      <p class="dim small mono">${Fmt.bytes(o.size)} · sha256 ${esc(Fmt.hash8(o.sha256))} · op ${esc(o.id)}</p>
      <div class="ins-actions">
        <button class="btn btn-small" type="button" id="od-lib">Open in Library</button>
        ${o.action === 'exclude' ? '<button class="btn btn-small" type="button" id="od-incl">Include file</button>' : '<button class="btn btn-small btn-danger-ghost" type="button" id="od-excl">Exclude file</button>'}
        <button class="ib" type="button" id="od-copy" aria-label="Copy original path" title="Copy original path"><svg><use href="#i-copy"/></svg></button>
        <button class="ib" type="button" id="od-reveal" aria-label="Show in Explorer" title="Show in Explorer"><svg><use href="#i-folder"/></svg></button>
      </div>`;
    $('#od-close').onclick = () => { box.hidden = true; this.table.body.focus(); };
    $('#od-copy').onclick = () => copyText(o.source);
    $('#od-reveal').onclick = () => reveal({ id: o.item_id });
    $('#od-lib').onclick = () => { Shell.go('library'); Library.clearAll(); $('#lib-q').value = o.name; Library.q = o.name; Library.apply(); };
    const ex = $('#od-excl') || $('#od-incl');
    ex.onclick = async () => {
      try {
        await API.post('/api/items/edit', { ids: [o.item_id], changes: { excluded: o.action !== 'exclude' } });
        toast(o.action === 'exclude' ? 'File included. Rebuild the plan to place it.' : 'File excluded. Rebuild the plan to apply.');
        box.hidden = true;
        await App.afterEdit();
      } catch (e) { toast(explain409(e, 'Not changed'), 'err'); }
    };
  },
  showUndoOp(o, box, row) {
    const name = opName(o);
    box.innerHTML = `
      <div class="od-head">${actionBadge(o.action)}<h3 class="ellip" title="${esc(name)}">${esc(name)}</h3>
        <button type="button" class="ib" id="od-close" aria-label="Close details"><svg><use href="#i-x"/></svg></button></div>
      <p class="od-why">${esc(o.reason || '')}</p>
      ${row(o.action === 'unlink' ? 'Link to remove' : 'Now at', o.source)}
      ${o.action === 'unlink' ? row('File stays at', o.link_to, 'path-dest') : row('Goes back to', o.dest, 'path-dest')}
      <p class="dim small mono">${Fmt.bytes(o.size)} · sha256 ${esc(Fmt.hash8(o.sha256))} · op ${esc(o.id)}</p>
      <div class="ins-actions">
        <button class="ib" type="button" id="od-copy" aria-label="Copy current path" title="Copy current path"><svg><use href="#i-copy"/></svg></button>
        <button class="ib" type="button" id="od-reveal" aria-label="Show in Explorer" title="Show in Explorer"><svg><use href="#i-folder"/></svg></button>
      </div>`;
    $('#od-close').onclick = () => { box.hidden = true; this.table.body.focus(); };
    $('#od-copy').onclick = () => copyText(o.source);
    $('#od-reveal').onclick = () => reveal({ path: o.source });
  },
  closeOp() { const b = $('#op-detail'); if (!b.hidden) { b.hidden = true; return true; } return false; },

  /* ---------- kits ---------- */
  async loadKits() {
    const ul = $('#kit-list');
    try {
      const ks = await API.get('/api/kits', { q: $('#kit-q').value.trim() });
      this.kits = ks;
      $('#rtab-kits-n').textContent = ks.length;
      ul.innerHTML = ks.length ? ks.map((k) => `<li><button type="button" class="kitrow${k.id === this.kitId ? ' on' : ''}" role="option" aria-selected="${k.id === this.kitId}" data-id="${esc(k.id)}">
        <span class="kr-name ellip">${esc(k.name)}</span>
        <span class="kr-meta"><span class="ellip">${esc(k.pack || '')}</span><span class="mono">${k.member_count}</span></span>
        <span class="kr-flags"><span class="conf"><i style="--c:${Math.round((k.confidence || 0) * 100)}%"></i><b class="mono">${Fmt.pct(k.confidence)}</b></span>${k.held ? '<span class="st st-held">Held</span>' : ''}${k.excluded ? '<span class="st st-excluded">Excluded</span>' : ''}${k.manual ? '<span class="st st-edited">Edited</span>' : ''}</span>
      </button></li>`).join('') : '<li class="side-msg">No kits found.</li>';
      if (!this.kitId && ks.length) this.openKit(ks[0].id);
      else if (this.kitId) this.openKit(this.kitId, true);
    } catch (e) { ul.innerHTML = `<li class="side-msg err">Couldn't load kits: ${esc(e.message)}</li>`; }
  },
  async openKit(id, keepSel) {
    if (this.kitId !== id || !keepSel) this.kitSel.clear();
    this.kitId = id;
    $$('#kit-list .kitrow').forEach((b) => { b.classList.toggle('on', b.dataset.id === id); b.setAttribute('aria-selected', b.dataset.id === id); });
    const box = $('#kit-detail');
    try {
      const k = await API.get('/api/kits/' + encodeURIComponent(id));
      if (this.kitId !== id) return;
      this.kit = k;
      this.renderKit();
    } catch (e) {
      box.innerHTML = e.status === 404 ? '<div class="empty"><strong>This kit no longer exists.</strong><p>It may have been dissolved.</p></div>' : `<p class="err">Couldn't load kit: ${esc(e.message)}</p>`;
      if (e.status === 404) this.kitId = null;
    }
  },
  renderKit() {
    const k = this.kit, box = $('#kit-detail');
    const checks = (k.ref_checks || []).map((c) => `<tr><td>${esc(c.check)}</td><td>${c.performed ? '<span class="chk chk-yes">Checked</span>' : '<span class="chk chk-no">Not checked</span>'}</td><td><span class="res res-${esc(c.result)}">${esc(c.result)}</span></td><td class="dim">${esc(c.detail || '')}</td></tr>`).join('');
    box.innerHTML = `
      <div class="kd-head">
        <div class="kd-title">
          <label class="sr" for="kd-name">Kit name</label>
          <input class="input kd-name" id="kd-name" value="${esc(k.name)}" spellcheck="false">
          <button class="btn btn-small" type="button" id="kd-rename">Rename</button>
        </div>
        <div class="kd-sub">${esc(k.manufacturer || 'Unknown maker')} · ${esc(k.pack ? shortPack(k.pack, k.manufacturer) : '')} · <span class="mono">${k.bpm ? Fmt.bpm(k.bpm) + ' BPM' : 'BPM unknown'}${k.key ? ' · ' + esc(k.key) : ''}</span></div>
        <div class="kd-status">
          <span class="conf conf-lg" title="Detection confidence"><i style="--c:${Math.round((k.confidence || 0) * 100)}%"></i><b class="mono">${Fmt.pct(k.confidence)}</b> confidence</span>
          ${k.held ? `<span class="st st-held">Held${k.hold_reason ? ': ' + esc(k.hold_reason) : ''}</span>` : '<span class="st st-ok">Will be organized</span>'}
          ${k.excluded ? '<span class="st st-excluded">Excluded</span>' : ''}
          ${k.manual ? '<span class="st st-edited">Edited by you</span>' : ''}
        </div>
        <div class="ins-actions">
          ${k.held ? '<button class="btn btn-small" type="button" data-ka="release">Release hold</button>' : '<button class="btn btn-small" type="button" data-ka="hold">Hold kit</button>'}
          ${k.excluded ? '<button class="btn btn-small" type="button" data-ka="include">Include kit</button>' : '<button class="btn btn-small" type="button" data-ka="exclude">Exclude kit</button>'}
          <button class="btn btn-small" type="button" data-ka="remove_members" id="kd-remove" disabled>Remove selected members</button>
          <button class="btn btn-small btn-danger-ghost" type="button" data-ka="dissolve">Dissolve kit</button>
        </div>
      </div>
      <div class="kd-scroll">
        <p class="kg-dest mono ellip" title="${esc(k.proposed_root || '')}">→ ${esc(relDest(k.proposed_root || ''))}</p>
        <h3 class="sub-h">Why this is a kit</h3>
        <ul class="evidence">${(k.evidence || []).map((e) => `<li>${esc(e)}</li>`).join('') || '<li class="dim">No evidence recorded.</li>'}</ul>
        <h3 class="sub-h">Reference checks</h3>
        <table class="mini-t"><thead><tr><th>Check</th><th>Performed</th><th>Result</th><th>Detail</th></tr></thead><tbody>${checks || '<tr><td colspan="4" class="dim">No checks recorded.</td></tr>'}</tbody></table>
        <h3 class="sub-h">Members <span class="mono dim">${(k.members || []).length} · ${Fmt.bytes(k.bytes)}</span></h3>
        <table class="mini-t members"><thead><tr><th><input type="checkbox" id="km-all" aria-label="Select all members"></th><th>File</th><th>Type</th><th class="num">Size</th><th>Status</th></tr></thead>
        <tbody>${(k.members || []).map((m) => `<tr><td><input type="checkbox" data-mid="${esc(m.id)}" aria-label="Select ${esc(m.name)}" ${this.kitSel.has(m.id) ? 'checked' : ''}></td><td class="ellip-cell"><button type="button" class="linkish ellip" data-play="${esc(m.id)}" title="${esc(m.abs_path)}">${esc(m.name)}</button></td><td>${extBadge(m)}</td><td class="num mono">${Fmt.bytes(m.size)}</td><td>${statusBadge(m.review_status)}</td></tr>`).join('')}</tbody></table>
      </div>`;
    const upd = () => { $('#kd-remove').disabled = this.kitSel.size === 0; $('#kd-remove').textContent = this.kitSel.size ? `Remove ${this.kitSel.size} member${this.kitSel.size > 1 ? 's' : ''}` : 'Remove selected members'; };
    $$('#kit-detail [data-mid]').forEach((cb) => cb.addEventListener('change', () => { if (cb.checked) this.kitSel.add(cb.dataset.mid); else this.kitSel.delete(cb.dataset.mid); upd(); }));
    $('#km-all').addEventListener('change', (ev) => { $$('#kit-detail [data-mid]').forEach((cb) => { cb.checked = ev.target.checked; if (cb.checked) this.kitSel.add(cb.dataset.mid); else this.kitSel.delete(cb.dataset.mid); }); upd(); });
    $$('#kit-detail [data-play]').forEach((b) => b.addEventListener('click', () => { const m = k.members.find((x) => x.id === b.dataset.play); if (m) Player.toggle(m); }));
    $$('#kit-detail [data-ka]').forEach((b) => b.addEventListener('click', () => this.kitAction(b.dataset.ka)));
    $('#kd-rename').addEventListener('click', () => this.kitAction('rename'));
    $('#kd-name').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); this.kitAction('rename'); } });
    upd();
  },
  async kitAction(action) {
    const k = this.kit; if (!k) return;
    const body = { kit_id: k.id, action };
    if (action === 'rename') { body.name = $('#kd-name').value.trim(); if (!body.name || body.name === k.name) { toast('Type a new name first'); return; } }
    if (action === 'remove_members') body.ids = [...this.kitSel];
    if (action === 'dissolve' && !confirm(`Dissolve kit “${k.name}”? Its ${k.members.length} files become loose sounds and are filed by category.`)) return;
    try {
      await API.post('/api/kits/edit', body);
      const msg = { hold: 'Kit held: its files will not be moved', release: 'Hold released', exclude: 'Kit excluded', include: 'Kit included', rename: 'Kit renamed', remove_members: 'Members removed from kit', dissolve: 'Kit dissolved' }[action];
      toast(msg + '. Plan needs rebuilding.');
      this.kitSel.clear();
      if (action === 'dissolve') this.kitId = null;
      await App.afterEdit();
      this.loadKits();
    } catch (e) { toast(explain409(e, 'Kit not changed'), 'err'); }
  },

  /* ---------- build / approve / run ---------- */
  async build() {
    const p = this.plan();
    if (isUndoPlan(p)) {
      try {
        await API.post('/api/undo/build', { run_id: p.summary.undo_of });
        toast('Rebuilding the undo plan. This only reads your files.');
        Shell.pollSoon();
      } catch (e) { toast(explain409(e, 'Can’t rebuild the undo plan'), 'err'); }
      return;
    }
    try {
      await API.post('/api/plan/build');
      toast('Building plan. This only reads your files.');
      Shell.pollSoon();
    } catch (e) { toast(e.status === 409 ? 'Can’t build now: ' + e.message : 'Plan build failed: ' + e.message, 'err'); }
  },
  openApprove() {
    const p = this.plan(); if (!p) return;
    if (p.stale) { toast('This plan is stale. Rebuild it before approving.', 'warn'); return; }
    const s = p.summary || {}, ba = s.by_action || {}, bb = s.bytes_by_action || {};
    $('#approve-hash').textContent = Fmt.hash8(p.plan_hash);
    $('#approve-go').textContent = 'Approve plan ' + Fmt.hash8(p.plan_hash);
    const undo = isUndoPlan(p);
    $('#approve-h').firstChild.textContent = undo ? 'Approve undo plan ' : 'Approve plan ';
    const desc = undo ? { move: 'moved back to the original folder (hard link, verify, then remove the Samples copy)', unlink: 'kit hard link removed; the file stays at its other name', keep: 'already back: nothing to do', hold: 'held: not touched' }
      : { move: 'moved into Samples (same drive: hard link, verify, then remove original)', link: 'hard-linked inside kits (no extra space)', keep: 'left in place (duplicate of a kept copy)', hold: 'held: not touched', exclude: 'excluded: not touched' };
    $('#approve-table').innerHTML = '<thead><tr><th>Action</th><th class="num">Files</th><th class="num">Size</th><th>What happens</th></tr></thead><tbody>' +
      (undo ? ['unlink', 'move', 'keep', 'hold'] : ['move', 'link', 'keep', 'hold', 'exclude']).map((a) => `<tr><td>${actionBadge(a)}</td><td class="num mono">${Fmt.num(ba[a] || 0)}</td><td class="num mono">${bb[a] !== undefined ? Fmt.bytes(bb[a]) : '—'}</td><td class="dim">${desc[a]}</td></tr>`).join('') +
      `</tbody><tfoot><tr><td>Total</td><td class="num mono">${Fmt.num(s.total_files)}</td><td class="num mono">${Fmt.bytes(s.total_bytes)}</td><td class="dim">${undo ? 'reverses an earlier run' : Fmt.bytes(s.duplicate_bytes_avoided) + ' of duplicates not stored twice'}</td></tr></tfoot>`;
    $('#approve-check').checked = false;
    $('#approve-go').disabled = true;
    $('#approve-err').hidden = true;
    $('#approve-dlg').showModal();
    $('#approve-check').focus();
  },
  async approve() {
    const p = this.plan();
    try {
      await API.post('/api/plan/approve', { plan_hash: p.plan_hash });
      $('#approve-dlg').close();
      toast(`Plan ${Fmt.hash8(p.plan_hash)} approved. Run it when you're ready.`);
      await App.refreshState();
    } catch (e) {
      const m = $('#approve-err');
      m.textContent = e.status === 409 ? `Not approved: ${e.message} Close this dialog, rebuild the plan and review it again.` : 'Not approved: ' + e.message;
      m.hidden = false;
      App.refreshState();
    }
  },
  openRun() {
    const p = this.plan(); if (!p) return;
    const ba = (p.summary && p.summary.by_action) || {};
    $('#run-hash').textContent = Fmt.hash8(p.plan_hash);
    $('#run-text').textContent = isUndoPlan(p) ? `This undo moves ${Fmt.num(ba.move || 0)} files back to their original folders and removes ${Fmt.num(ba.unlink || 0)} kit links. Each file is re-checked first; anything that changed since the run, or whose original location is taken, is held and never overwritten. Folders created in Samples stay (they may be empty). Rescan afterwards.` : `This moves ${Fmt.num(ba.move || 0)} files and creates ${Fmt.num(ba.link || 0)} hard links in ${App.state.dest_root}. Every file is re-checked against the plan before it is touched; anything that changed is held, never overwritten. Held and excluded files are not touched.`;
    $('#run-err').hidden = true;
    $('#run-dlg').showModal();
    $('#run-go').focus();
  },
  async run() {
    const p = this.plan();
    try {
      await API.post('/api/execute', { plan_hash: p.plan_hash });
      $('#run-dlg').close();
      toast('Running approved plan');
      Shell.pollSoon();
    } catch (e) {
      const m = $('#run-err');
      m.textContent = e.status === 409 ? `Can't run: ${e.message}` : 'Run failed to start: ' + e.message;
      m.hidden = false;
      App.refreshState();
    }
  },

  show() {
    this.shown = true;
    if (!this.started) { this.started = true; this.setTab('ops'); this.table.reset(); this.loadKits(); }
    else this.table.schedule();
  },
};
