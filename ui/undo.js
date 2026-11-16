/* undo.js: owned by the undo feature.
   Move Log: "Undo this run" builds a reverse plan (read-only), which then shows in
   Review like any plan and runs only after its own approval. After an undo run the
   Move Log asks for a rescan. Review-side labelling lives in review.js. */
'use strict';

const Undo = {
  runs: [],
  rescanNeeded: false,
  target: null,
  goReview: false,

  init() {
    const dlg = document.createElement('dialog');
    dlg.className = 'dlg undo-dlg';
    dlg.id = 'undo-dlg';
    dlg.setAttribute('aria-labelledby', 'undo-h');
    dlg.innerHTML = `<form method="dialog" class="dlg-body">
      <h2 id="undo-h">Undo run <span class="mono" id="undo-hash"></span></h2>
      <div class="undo-pick" id="undo-pick" hidden><label class="field-label" for="undo-run">Run to undo</label><select class="input" id="undo-run"></select></div>
      <p id="undo-text"></p>
      <table class="dlg-table" id="undo-table"></table>
      <p>Building the undo plan only reads your files. It replaces the current plan in Review, where you approve and run it like any plan. Files that changed since the run, or whose original location is taken, are held and stay where they are.</p>
      <div class="dlg-err" id="undo-err" role="alert" hidden></div>
      <div class="dlg-actions">
        <button class="btn" value="cancel" formnovalidate>Cancel</button>
        <button class="btn btn-primary" id="undo-go" type="button">Build undo plan</button>
      </div></form>`;
    document.body.appendChild(dlg);
    $('#log-undo').addEventListener('click', () => this.open());
    $('#undo-go').addEventListener('click', () => this.build());
    $('#undo-run').addEventListener('change', () => { this.target = this.runs.find((r) => r.run_id === $('#undo-run').value) || this.target; this.fillDialog(); });
    $('#undo-rescan-go').addEventListener('click', () => this.rescan());
    App.on('job-done', (job) => {
      this.refresh();
      if (job === 'plan' && this.goReview) { this.goReview = false; Shell.go('review'); }
    });
    App.on('state', () => { if (App.view === 'log') this.refresh(); });
  },
  afterInit() {
    const show = MoveLog.show.bind(MoveLog);
    MoveLog.show = () => { show(); this.refresh(); };
    const top = MoveLog.renderTop.bind(MoveLog);
    MoveLog.renderTop = (s) => { top(s); this.renderUndoTop(s); };
  },
  isUndoExec(s) {
    return !!(s && s.execution && ((s.undo && s.undo.execution_kind === 'undo') || (API.fixture && this.rescanNeeded)));
  },
  /* After an undo run the forward verification does not apply: show what came back. */
  renderUndoTop(s) {
    if (!this.isUndoExec(s)) return;
    const rs = s.execution.results_summary || {};
    const back = (rs.done || 0) + (rs['already-done'] || 0), held = (rs.held || 0) + (rs.failed || 0);
    $('#log-meta').textContent = `Last run ${Fmt.time(s.execution.ran)} was an undo.`;
    $('#verify').innerHTML = `<h2 class="sub-h">Undo result</h2>
      <div class="vrow">
        <div class="vstat is-good"><span class="card-v mono">${Fmt.num(back)}</span><span class="card-k">operations reversed</span></div>
        <div class="vstat ${held ? 'is-bad' : 'is-good'}"><span class="card-v mono">${Fmt.num(held)}</span><span class="card-k">held, left where they are</span></div>
      </div>
      <p class="dim vsum">Folders the run created inside Samples are kept, even when empty. The catalog CSVs in Samples\\Catalog now describe files that moved back; Undo_Report.csv lists what was reversed.</p>`;
  },

  async refresh() {
    let r;
    try { r = await API.get('/api/runs'); } catch (e) { return; }
    this.runs = r.runs || [];
    this.rescanNeeded = !!r.rescan_needed;
    const btn = $('#log-undo');
    const cur = this.runs.find((x) => x.is_current);
    const t = cur || this.runs.find((x) => x.undoable) || this.runs[0] || null;
    this.target = t;
    $('#undo-rescan').hidden = !this.rescanNeeded;
    if (App.view === 'log' && App.state) this.renderUndoTop(App.state);
    if (!t) { btn.hidden = true; $('#undo-state').innerHTML = ''; return; }
    const anyUndoable = this.runs.some((x) => x.undoable);
    btn.hidden = !anyUndoable;
    btn.textContent = cur && cur === t ? 'Undo this run…' : `Undo run of ${Fmt.time(t.ran)}…`;
    btn.disabled = !!(App.state && App.state.progress && App.state.progress.running);
    btn.title = 'Build a plan that puts the files of this run back where they were';
    const u = t.undo;
    $('#undo-state').innerHTML = !u ? '' : u.status === 'undone' ? `<span class="us-done">Undone ${esc(Fmt.time(u.ran))}</span>`
      : `<span class="us-part">${u.status === 'interrupted' ? 'Undo paused' : 'Partly undone'} ${esc(Fmt.time(u.ran))}</span>`;
  },

  open() {
    const choices = this.runs.filter((x) => x.undoable);
    if (!choices.length) { toast('Nothing to undo', 'warn'); return; }
    if (!this.target || !this.target.undoable) this.target = choices[0];
    const pick = $('#undo-pick');
    pick.hidden = choices.length < 2;
    $('#undo-run').innerHTML = choices.map((r) => `<option value="${esc(r.run_id)}"${r === this.target ? ' selected' : ''}>${esc(Fmt.time(r.ran))} · ${Fmt.num(r.moved)} moved, ${Fmt.num(r.linked)} linked${r.undo ? ' · ' + esc(r.undo.status) : ''}</option>`).join('');
    this.fillDialog();
    $('#undo-err').hidden = true;
    $('#undo-dlg').showModal();
    $('#undo-go').focus();
  },
  fillDialog() {
    const t = this.target;
    $('#undo-hash').textContent = Fmt.hash8(t.plan_hash);
    $('#undo-text').textContent = `The run of ${Fmt.time(t.ran)}${t.interrupted ? ' (interrupted)' : ''}${t.undo ? ', already ' + t.undo.status.replace('-', ' ') : ''}. Only operations the journal confirms as done are reversed.`;
    $('#undo-table').innerHTML = '<thead><tr><th>Action</th><th class="num">Files</th><th>What the undo does</th></tr></thead><tbody>' +
      `<tr><td>${actionBadge('move')}</td><td class="num mono">${Fmt.num(t.moved)}</td><td class="dim">moved back from Samples to the original folder</td></tr>` +
      `<tr><td>${actionBadge('unlink')}</td><td class="num mono">${Fmt.num(t.linked)}</td><td class="dim">kit hard link removed; the file itself stays</td></tr></tbody>`;
  },
  async build() {
    const t = this.target; if (!t) return;
    try {
      await API.post('/api/undo/build', { run_id: t.run_id });
      $('#undo-dlg').close();
      toast('Checking files for undo. This only reads.');
      this.goReview = true;
      Shell.pollSoon();
    } catch (e) {
      const m = $('#undo-err');
      m.textContent = e.status === 409 ? `Can't build: ${e.message}` : 'Undo plan failed: ' + e.message;
      m.hidden = false;
    }
  },
  async rescan() {
    try {
      await API.post('/api/scan', {});
      toast('Rescanning. Nothing in your folders will be changed.');
      Shell.pollSoon();
    } catch (e) { toast(explain409(e, 'Can’t rescan'), 'err'); }
  },
};
App.features.push(Undo);

/* ---------- fixture mode (?fixture=1): in-memory undo endpoints ---------- */
window.FixtureExt.push((method, path, query, body, st, h) => {
  const undoPlan = () => st.plan && st.plan.summary && st.plan.summary.kind === 'undo';
  if (!st.undoRun && st.execution) {
    st.undoRun = { run_id: 'f0e1d2c3-0a1b2c3d4e5f6071', plan_hash: st.plan.plan_hash, ran: st.execution.ran, undo: null, results: st.results.slice(), ops: st.ops.slice() };
  }
  const run = st.undoRun;
  if (path === '/api/runs' && method === 'GET') {
    if (!run) return h.ok({ runs: [], rescan_needed: false });
    const done = (a) => run.results.filter((r) => r.action === a && (r.status === 'done' || r.status === 'already-done')).length;
    if (undoPlan() && st.results.length && st.ops.length && st.results[0].op_id === st.ops[0].id && !run.undo) run.undo = { status: 'undone', ran: st.execution.ran, plan_hash: st.plan.plan_hash, counts: {} };
    return h.ok({
      runs: [{ run_id: run.run_id, ran: run.ran, plan_hash: run.plan_hash, root: st.root, dest_root: st.dest_root, interrupted: false, counts: {}, moved: done('move'), linked: done('link'), undoable: !run.undo, why_not: run.undo ? 'This run was already undone.' : null, is_current: !undoPlan() || !run.undo, undo: run.undo }],
      rescan_needed: !!(run.undo && undoPlan()),
    });
  }
  if (path === '/api/undo/build' && method === 'POST') {
    if (!run) return h.err(400, 'No completed run to undo');
    if (run.undo) return h.err(409, 'This run was already undone.');
    if (st.progress.running) return h.err(409, 'A ' + st.progress.job + ' job is already running');
    Object.assign(st.progress, { job: 'plan', running: true, phase: 'checking', done: 0, total: run.results.length, message: '' });
    setTimeout(() => {
      const byId = new Map(st.ops.concat(run.ops).map((o) => [o.id, o]));
      const unl = [], mv = [], hold = [];
      run.results.forEach((r, i) => {
        if (!(r.status === 'done' || r.status === 'already-done') || !r.dest) return;
        const o = byId.get(r.op_id) || { id: r.op_id, action: r.action, source: r.source, dest: r.dest, name: r.name, size: 0, link_to: r.source };
        const base = { item_id: o.item_id, sha256: o.sha256, size: o.size, kit_id: o.kit_id, kit_name: o.kit_name, category: o.category, name: o.name, pack: o.pack, rel_path: o.rel_path, flags: ['undo'], depends_on: null, canonical: null };
        if (o.action === 'link') unl.push(Object.assign(base, { id: 'u' + o.id.slice(1), action: 'unlink', source: o.dest, dest: null, link_to: o.link_to, reason: 'Remove the kit link this run created (the file stays where it is)' }));
        else if (i % 41 === 7) hold.push(Object.assign(base, { id: 'h' + o.id.slice(1), action: 'hold', source: o.dest, dest: o.source, reason: 'changed since the run (content differs)' }));
        else mv.push(Object.assign(base, { id: 'm' + o.id.slice(1), action: 'move', source: o.dest, dest: o.source, reason: 'Move back to its original location' }));
      });
      const ops = unl.concat(mv, hold);
      const by = {}, bb = {};
      ops.forEach((o) => { by[o.action] = (by[o.action] || 0) + 1; bb[o.action] = (bb[o.action] || 0) + o.size; });
      st.ops = ops;
      st.plan = { plan_hash: 'undo' + run.plan_hash.slice(4, 60) + 'u0', created: new Date().toISOString(), stale: false, approved: false,
        summary: { kind: 'undo', undo_of: run.run_id, run_ts: run.ran, run_plan_hash: run.plan_hash, total_files: ops.length, total_bytes: ops.reduce((s, o) => s + o.size, 0), by_action: by, bytes_by_action: bb, held: by.hold || 0, duplicate_groups: 0, duplicate_bytes_avoided: 0, collisions: 0 } };
      Object.assign(st.progress, { running: false, phase: 'complete', done: ops.length, message: 'Undo plan ready' });
    }, 500);
    return h.ok({ ok: true, run_id: run.run_id });
  }
  return null;
});
