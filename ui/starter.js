/* starter.js: Beat Starter view. Picks a drum loop, bass and melody that fit
   together; lock, reroll, play all in sync, save sets (stored in app data). */
'use strict';

const KEYS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const SLOT_LABEL = { drums: 'Drums', bass: 'Bass', melody: 'Melody' };

const Starter = {
  res: null,
  locks: {},
  init() {
    const opts = ['<option value="">Random key</option>'].concat(
      KEYS.map((k) => `<option value="${k}m">${k} minor</option>`),
      KEYS.map((k) => `<option value="${k}">${k} major</option>`));
    $('#bs-key').innerHTML = opts.join('');
    $('#bs-new').onclick = () => { this.locks = {}; this.roll(); };
    $('#bs-playall').onclick = () => this.togglePlayAll();
    $('#bs-save').onclick = () => this.save();
    $('#bs-loose').onchange = () => this.roll();
  },
  show() {
    if (!this.res) this.roll();
    this.loadSaved();
  },
  ids() {
    const out = {};
    for (const [slot, v] of Object.entries((this.res && this.res.slots) || {})) if (v) out[slot] = v.row.id;
    return out;
  },
  async roll(rerollSlot) {
    this.stopAll();
    let locked = {}, avoid = {};
    let key = $('#bs-key').value || null;
    let bpm = +$('#bs-bpm').value || null;
    if (rerollSlot) {
      locked = this.ids();
      avoid[rerollSlot] = locked[rerollSlot];
      delete locked[rerollSlot];
      key = this.res.key; bpm = this.res.bpm;
    } else {
      const cur = this.ids();
      for (const s of Object.keys(this.locks)) if (this.locks[s] && cur[s]) locked[s] = cur[s];
    }
    try {
      this.res = await API.post('/api/starter', { key, bpm, locked, avoid, loose: $('#bs-loose').checked });
    } catch (e) { toast(e.message, 'err'); return; }
    this.render();
  },
  render() {
    const r = this.res;
    $('#bs-target').textContent = r && (r.key || r.bpm) ? [r.key, r.bpm && Fmt.bpm(r.bpm) + ' BPM'].filter(Boolean).join(' · ') : '';
    $('#bs-slots').innerHTML = ['drums', 'bass', 'melody'].map((slot) => {
      const v = r && r.slots[slot];
      if (!v) return `<article class="bs-slot bs-empty" data-slot="${slot}"><h3 class="side-h">${SLOT_LABEL[slot]}</h3><p class="dim">${esc((r && r.missing[slot]) || 'Nothing picked yet')}</p></article>`;
      const row = v.row, on = !!this.locks[slot];
      const only = !v.locked && (r.options[slot] || 0) < 2;
      return `<article class="bs-slot${on ? ' locked' : ''}" data-slot="${slot}">
        <h3 class="side-h">${SLOT_LABEL[slot]}</h3>
        <p class="bs-name ellip" title="${esc(row.abs_path)}">${esc(row.name)}</p>
        <p class="dim small ellip">${esc(row.pack || '')}</p>
        <p class="mono small">${esc([row.bpm && Fmt.bpm(row.bpm) + ' BPM', row.key, row.bars && row.bars + ' bars'].filter(Boolean).join(' · ') || '—')}</p>
        <p class="mwhy">${esc(v.reasons.join(' · '))}</p>
        <div class="bs-acts">
          <button type="button" class="btn btn-small bs-play"><svg><use href="#i-${Player.isPlaying(row.id) ? 'stop' : 'play'}"/></svg>Play</button>
          <button type="button" class="btn btn-small bs-lock" aria-pressed="${on}">${on ? 'Locked' : 'Lock'}</button>
          <button type="button" class="btn btn-small btn-ghost bs-reroll" ${on || only ? 'disabled' : ''} ${only ? 'title="Only one loop fits this key and tempo"' : ''}>Reroll</button>
        </div></article>`;
    }).join('');
    $$('#bs-slots .bs-slot').forEach((el) => {
      const slot = el.dataset.slot, v = r && r.slots[slot];
      if (!v) return;
      $('.bs-play', el).onclick = () => { this.stopAll(); Player.toggle(v.row); this.render(); };
      $('.bs-lock', el).onclick = () => { this.locks[slot] = !this.locks[slot]; this.render(); };
      $('.bs-reroll', el).onclick = () => this.roll(slot);
    });
    $('#bs-save').disabled = !Object.keys(this.ids()).length;
  },

  /* ---- Play all: one shared clock (see mixclock.js) ----
     Each cycle starts every loop at the same AudioContext time, at the set
     tempo (playbackRate), with RMS-balanced gain, and restarts them together
     at the next cycle so tiny tempo differences never pile up. */
  async togglePlayAll() {
    if (this.mix) { this.stopAll(); return; }
    const picks = Object.values((this.res && this.res.slots) || {}).filter(Boolean).map((v) => v.row);
    if (!picks.length) return;
    if (API.fixture) { toast('Audio is unavailable in fixture mode. Play all works with your real library.'); return; }
    Player.stop();
    const btn = $('#bs-playall');
    btn.disabled = true;
    try {
      this.ctx = this.ctx || new (window.AudioContext || window.webkitAudioContext)();
      const bufs = await Promise.all(picks.map(async (row) => {
        const r = await fetch(API.audioUrl(row.id));
        if (!r.ok) throw new Error('could not load audio (' + r.status + ')');
        return this.ctx.decodeAudioData(await r.arrayBuffer());
      }));
      const setBpm = this.res.bpm || picks.find((r) => r.bpm)?.bpm || null;
      const plan = MixClock.plan(picks.map((row, k) => ({ bpm: row.bpm, bars: row.bars, duration: bufs[k].duration })), setBpm);
      const gains = MixClock.gains(bufs.map((b) => MixClock.rms(Array.from({ length: b.numberOfChannels }, (_, c) => b.getChannelData(c)))));
      const nodes = gains.map((g) => { const n = this.ctx.createGain(); n.gain.value = g; n.connect(this.ctx.destination); return n; });
      this.mix = { bufs, plan, nodes, live: [], timer: null };
      this.cycleAt(this.ctx.currentTime + 0.08);
      btn.textContent = 'Stop';
    } catch (e) {
      toast('Play all failed: ' + e.message + '. AIFF and other formats the browser cannot decode are skipped by preview.', 'err');
      this.stopAll();
    } finally { btn.disabled = false; }
  },
  cycleAt(t) {
    const m = this.mix;
    if (!m || !m.plan.cycle) return;
    const end = t + m.plan.cycle;
    m.live = m.live.filter((s) => s.endsAt > this.ctx.currentTime);
    m.bufs.forEach((b, k) => {
      const L = m.plan.loops[k];
      const s = this.ctx.createBufferSource();
      s.buffer = b; s.loop = true; s.loopStart = 0; s.loopEnd = L.loopEnd;
      s.playbackRate.value = L.rate;
      s.connect(m.nodes[k]);
      s.start(t); s.stop(end);
      s.endsAt = end;
      m.live.push(s);
    });
    // Queue the next cycle shortly before this one ends, on the same clock.
    const wait = Math.max(0, (end - this.ctx.currentTime - 0.4) * 1000);
    m.timer = setTimeout(() => this.cycleAt(end), wait);
  },
  stopAll() {
    const m = this.mix;
    if (m) {
      clearTimeout(m.timer);
      for (const s of m.live) { try { s.stop(); } catch (e) { /* already stopped */ } }
      for (const n of m.nodes) { try { n.disconnect(); } catch (e) { /* gone */ } }
    }
    this.mix = null;
    const btn = $('#bs-playall');
    if (btn) btn.textContent = 'Play all';
  },

  /* ---- saved sets ---- */
  async save() {
    const name = prompt('Name this set', [this.res.key, this.res.bpm && Fmt.bpm(this.res.bpm) + ' BPM'].filter(Boolean).join(' '));
    if (name === null) return;
    try {
      await API.post('/api/starters', { name, key: this.res.key, bpm: this.res.bpm, ids: Object.values(this.ids()) });
      toast('Saved "' + name + '"');
      this.loadSaved();
    } catch (e) { toast(e.message, 'err'); }
  },
  async loadSaved() {
    let items = [];
    try { items = (await API.get('/api/starters')).items; } catch (e) { toast(e.message, 'err'); }
    const box = $('#bs-saved');
    if (!items.length) { box.innerHTML = '<p class="dim small">No saved sets yet.</p>'; return; }
    box.innerHTML = items.map((s, i) => `<li class="bs-set"><b>${esc(s.name)}</b><span class="dim small">${esc(s.rows.map((r) => r.name).join(' · ') || 'Samples no longer in the library')}</span>
      <button type="button" class="btn btn-small" data-load="${i}">Load</button><button type="button" class="ib" data-del="${i}" aria-label="Delete ${esc(s.name)}"><svg><use href="#i-x"/></svg></button></li>`).join('');
    $$('[data-load]', box).forEach((b) => { b.onclick = () => this.loadSet(items[+b.dataset.load]); });
    $$('[data-del]', box).forEach((b) => {
      b.onclick = async () => {
        try { await API.del('/api/starters/' + encodeURIComponent(items[+b.dataset.del].id)); this.loadSaved(); } catch (e) { toast(e.message, 'err'); }
      };
    });
  },
  async loadSet(s) {
    const locked = {};
    for (const r of s.rows) {
      const slot = { 'Loops/Drum Loops': 'drums', 'Loops/Bass Loops': 'bass', 'Loops/Melody Loops': 'melody' }[r.category];
      if (slot) locked[slot] = r.id;
    }
    this.locks = { drums: !!locked.drums, bass: !!locked.bass, melody: !!locked.melody };
    try { this.res = await API.post('/api/starter', { key: s.key, bpm: s.bpm, locked }); } catch (e) { toast(e.message, 'err'); return; }
    this.render();
  },
};
