/* core.js: API client, formatting helpers, toasts, shared app state, audition player. */
'use strict';

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(v) { return v === null || v === undefined ? '' : String(v).replace(/[&<>"']/g, (c) => ESC_MAP[c]); }

function debounce(fn, ms) {
  let t;
  return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); };
}

const Fmt = {
  bytes(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = Math.abs(n);
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return (n < 0 ? '-' : '') + (i === 0 ? v : v < 10 ? v.toFixed(1) : Math.round(v)) + ' ' + u[i];
  },
  dur(s) {
    if (s === null || s === undefined || isNaN(s)) return '—';
    const m = Math.floor(s / 60), r = s - m * 60;
    return m + ':' + (r < 10 ? '0' : '') + r.toFixed(1);
  },
  num(n) { return n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US'); },
  bpm(b) { return b === null || b === undefined ? '' : (Math.round(b * 10) / 10).toString(); },
  time(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = (x) => String(x).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },
  hash8(h) { return h ? String(h).slice(0, 8) : '—'; },
  pct(c) { return c === null || c === undefined ? '' : Math.round(c * 100) + '%'; },
  catShort(c) { return c ? c.replace(/^One-Shots\//, '').replace(/^Loops\//, '') : ''; },
};

/* Category → one of the six palette slugs (drums|bass|synth|vocals|fx|keys).
   CSS classes .cat-<slug> set --cat to that color. Unsorted/unknown → fx. */
const CAT_SLUG = {
  'One-Shots/Drums': 'drums', 'Loops/Drum Loops': 'drums',
  'One-Shots/Bass & 808s': 'bass', 'Loops/Bass Loops': 'bass',
  'One-Shots/Instruments': 'synth', 'Loops/Melody Loops': 'synth',
  'One-Shots/FX & Vocals': 'vocals', 'Loops/Vocal & FX Loops': 'vocals',
  'Presets & MIDI': 'keys',
};
const CAT_SLUGS = ['drums', 'bass', 'synth', 'vocals', 'fx', 'keys'];
function catSlug(c) { return CAT_SLUG[c] || 'fx'; }

const STATUS_LABEL = { ok: 'OK', 'needs-review': 'Review', edited: 'Edited', held: 'Held', excluded: 'Excluded', done: 'Organized' };
const ACTION_LABEL = { move: 'Move', link: 'Link', keep: 'Keep', hold: 'Hold', exclude: 'Exclude', unlink: 'Unlink' };
const SOURCE_LABEL = { tag: 'Tag', filename: 'Filename', folder: 'Folder', inference: 'Inferred', analysis: 'Analysis', manual: 'Manual', pack: 'From pack' };

/* ---------------- Store (tiny localStorage wrapper) ---------------- */
const Store = {
  get(k, d) { try { const v = localStorage.getItem('slo.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('slo.' + k, JSON.stringify(v)); } catch (e) { /* storage blocked: ignore */ } },
};

/* ---------------- API client ---------------- */
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const API = {
  fixture: false,
  async init() {
    const params = new URLSearchParams(location.search);
    if (params.get('fixture') === '1') { this.fixture = true; return; }
    try {
      const r = await fetch('/api/state', { cache: 'no-store' });
      if (!r.ok) throw new Error('status ' + r.status);
      const ct = r.headers.get('content-type') || '';
      if (!ct.includes('json')) throw new Error('not json');
    } catch (e) {
      this.fixture = true;
      this.fallbackReason = 'Could not reach the app server (' + e.message + '); showing fixture data.';
    }
  },
  qs(params) {
    const u = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) if (v !== '' && v !== null && v !== undefined) u.set(k, v);
    const s = u.toString();
    return s ? '?' + s : '';
  },
  async request(method, path, body) {
    if (this.fixture) {
      const r = await window.FixtureAPI.handle(method, path, body);
      if (r.status >= 200 && r.status < 300) return r.body;
      throw new ApiError(r.status, (r.body && r.body.error) || 'Request failed');
    }
    const opt = { method, cache: 'no-store', headers: {} };
    if (method !== 'GET') {
      // CSRF guard required by server: every POST carries X-SLO and a JSON content type.
      opt.headers['X-SLO'] = '1';
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body || {});
    }
    let r;
    try { r = await fetch(path, opt); } catch (e) { throw new ApiError(0, 'The app server is not responding. Is the launcher window still open?'); }
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) throw new ApiError(r.status, (data && data.error) || `${method} ${path} failed with HTTP ${r.status}`);
    return data;
  },
  get(path, params) { return this.request('GET', path + this.qs(params)); },
  post(path, body) { return this.request('POST', path, body); },
  del(path) { return this.request('DELETE', path); },
  audioUrl(id) { return '/api/audio/' + encodeURIComponent(id); },
};

/* ---------------- Toasts ---------------- */
function toast(msg, kind) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast' + (kind ? ' toast-' + kind : '');
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, kind === 'err' ? 6000 : 3200);
}

function explain409(e, what) {
  if (e.status === 409) return (what ? what + ': ' : '') + e.message;
  return e.message;
}

/* ---------------- Clipboard ---------------- */
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); toast('Path copied'); return true; }
  } catch (e) { /* fall through */ }
  const ta = document.createElement('textarea');
  ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  ta.remove();
  toast(ok ? 'Path copied' : 'Copy failed. Select the path and press Ctrl+C.', ok ? '' : 'err');
  return ok;
}

async function reveal(arg) {
  try {
    await API.post('/api/reveal', arg);
    toast(API.fixture ? 'Fixture mode: Explorer would open here' : 'Opened in Explorer');
  } catch (e) { toast('Could not open Explorer: ' + e.message, 'err'); }
}

/* ---------------- Shared app state + events ---------------- */
const App = {
  state: null,
  facets: null,
  view: 'library',
  listeners: {},
  views: {},          // name -> {obj, title}; obj.show() is called when routed
  features: [],       // feature modules: {init(), afterInit?()}; push at script load time
  keyHandlers: [],    // (ev) => true when handled; run for keys outside text fields, after Alt+N
  registerView(name, obj, title) { this.views[name] = { obj, title }; },
  on(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
  emit(ev, data) { (this.listeners[ev] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); },
  async refreshState() {
    try {
      this.state = await API.get('/api/state');
      this.emit('state', this.state);
    } catch (e) { toast('Could not load app state: ' + e.message, 'err'); }
    return this.state;
  },
  async refreshFacets() {
    try { this.facets = await API.get('/api/facets'); this.emit('facets', this.facets); } catch (e) { /* library may be empty */ }
  },
  /* Called after any edit: server marks plan stale and clears approval. */
  async afterEdit() {
    const wasApproved = this.state && this.state.plan && this.state.plan.approved;
    await this.refreshState();
    this.emit('edited');
    if (wasApproved) toast('Plan approval cleared because you made a change. Rebuild and review the plan again.', 'warn');
  },
};

/* ---------------- Waveforms ----------------
   Peaks decodes a sample once (fetch /api/audio/<id> + decodeAudioData), keeps
   normalized peaks keyed by sample id, and paints them into every
   canvas.wave-canvas[data-wave-for="<id>"] (e.g. the inspector) via paintAll().
   The deck's own waveform is media.js's Wave (server peaks, click/drag seek).
   Files over 40 MB, formats the browser can't decode, and fixture mode get no
   peaks; their canvases are left blank and get data-wave-state="none". */
const Peaks = {
  MAX_BYTES: 40 * 1024 * 1024,
  BUCKETS: 600,
  CACHE: 48,
  peaks: new Map(),       // id -> Float32Array (0..1), most recently used last
  failed: new Set(),      // ids that can't be decoded
  inflight: new Map(),    // id -> Promise
  queue: [],              // waiting decodes, newest first
  busy: false,
  has(id) { return this.peaks.has(id); },
  get(id) {
    const p = this.peaks.get(id);
    if (p) { this.peaks.delete(id); this.peaks.set(id, p); }
    return p || null;
  },
  /* Ask for a sample's peaks. size (bytes) is optional; the fetch also checks
     Content-Length. Resolves to the peaks or null; never rejects. */
  request(id, size) {
    if (!id || API.fixture || this.failed.has(id)) return Promise.resolve(null);
    if (this.peaks.has(id)) return Promise.resolve(this.get(id));
    if (size && size > this.MAX_BYTES) { this.failed.add(id); return Promise.resolve(null); }
    if (this.inflight.has(id)) return this.inflight.get(id);
    const job = new Promise((resolve) => {
      this.queue.unshift({ id, resolve });
      // Arrowing quickly through a list must not pile up downloads: keep the newest few.
      this.queue.splice(3).forEach((q) => { this.inflight.delete(q.id); q.resolve(null); });
    });
    this.inflight.set(id, job);
    this.pump();
    return job;
  },
  pump() {
    if (this.busy || !this.queue.length) return;
    const { id, resolve } = this.queue.shift();
    this.busy = true;
    this.decode(id).catch(() => null).then((pk) => {
      if (pk) {
        this.peaks.set(id, pk);
        while (this.peaks.size > this.CACHE) this.peaks.delete(this.peaks.keys().next().value);
      } else this.failed.add(id);
      this.busy = false; this.inflight.delete(id);
      resolve(pk);
      try { this.paintAll(); } catch (e) { console.warn(e); }
      this.pump();
    });
  },
  async decode(id) {
    const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Ctx) return null;
    const r = await fetch(API.audioUrl(id));
    if (!r.ok) return null;
    if (+(r.headers.get('content-length') || 0) > this.MAX_BYTES) {
      try { if (r.body) r.body.cancel(); } catch (e) { /* ignore */ }
      return null;
    }
    const buf = await r.arrayBuffer();
    if (buf.byteLength > this.MAX_BYTES) return null;
    const ctx = new Ctx(1, 1, 44100);
    const audio = await new Promise((res, rej) => {
      const p = ctx.decodeAudioData(buf, res, rej);
      if (p && p.then) p.then(res, rej);
    });
    return this.reduce(audio);
  },
  reduce(audio) {
    const n = this.BUCKETS, out = new Float32Array(n), chans = [];
    for (let c = 0; c < Math.min(audio.numberOfChannels, 2); c++) chans.push(audio.getChannelData(c));
    const len = chans.length ? chans[0].length : 0;
    if (!len) return out;
    let max = 0;
    for (let b = 0; b < n; b++) {
      const s0 = Math.floor(b * len / n), s1 = Math.min(len, Math.max(s0 + 1, Math.floor((b + 1) * len / n)));
      const stride = Math.max(1, Math.floor((s1 - s0) / 512)); // peaks only need to look right
      let m = 0;
      for (const ch of chans) for (let i = s0; i < s1; i += stride) { const v = Math.abs(ch[i]); if (v > m) m = v; }
      out[b] = m; if (m > max) max = m;
    }
    if (max > 0) for (let b = 0; b < n; b++) out[b] /= max;
    return out;
  },
  /* Size a canvas's backing store for devicePixelRatio. */
  fit(cv) {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(cv.clientWidth * dpr)), h = Math.max(1, Math.round(cv.clientHeight * dpr));
    if (cv.width !== w) cv.width = w;
    if (cv.height !== h) cv.height = h;
    return { w, h, dpr };
  },
  /* The category color when an ancestor provides --cat, else the accent gradient. */
  fill(ctx, cv, w) {
    const cs = getComputedStyle(cv);
    if (cv.closest('[style*="--cat"], [class*="cat-"]')) {
      const c = cs.getPropertyValue('--cat').trim();
      if (c) return c;
    }
    const g = ctx.createLinearGradient(0, 0, w, 0);
    g.addColorStop(0, cs.getPropertyValue('--accent').trim() || '#ff3fd2');
    g.addColorStop(1, cs.getPropertyValue('--accent-2').trim() || '#2de2ff');
    return g;
  },
  /* Mirrored bars, 2px wide with 1.5px gaps (CSS px). style defaults to fill(). */
  draw(cv, pk, style) {
    const { w, h, dpr } = this.fit(cv);
    const ctx = cv.getContext('2d');
    if (!ctx || !cv.clientWidth) return;
    ctx.clearRect(0, 0, w, h);
    const bw = Math.max(1, Math.round(2 * dpr)), gap = Math.max(1, Math.round(1.5 * dpr)), pitch = bw + gap;
    const bars = Math.max(1, Math.floor((w + gap) / pitch));
    ctx.fillStyle = style || this.fill(ctx, cv, w);
    for (let i = 0; i < bars; i++) {
      const a = Math.floor(i / bars * pk.length), b = Math.max(a + 1, Math.floor((i + 1) / bars * pk.length));
      let m = 0;
      for (let j = a; j < b; j++) if (pk[j] > m) m = pk[j];
      const bh = Math.max(dpr, Math.sqrt(m) * (h - 2 * dpr)); // sqrt keeps quiet tails visible
      ctx.fillRect(i * pitch, Math.round((h - bh) / 2), bw, Math.round(bh));
    }
  },
  /* Paint every canvas.wave-canvas[data-wave-for] whose sample has peaks cached.
     Others are left untouched (no fetch here: only the player, or an explicit
     Peaks.request(id, size), decodes, so non-audio rows never hit /api/audio). */
  paintAll() {
    $$('canvas.wave-canvas[data-wave-for]').forEach((cv) => {
      const id = cv.dataset.waveFor, pk = this.get(id);
      if (pk) { this.draw(cv, pk); cv.dataset.waveState = 'ready'; }
      else if (API.fixture || this.failed.has(id)) cv.dataset.waveState = 'none';
    });
  },
};
window.Peaks = Peaks;

/* ---------------- Spectrum ----------------
   An AnalyserNode on the <audio> element, created lazily on first play inside
   the user gesture. Any failure keeps the static bar graphic; playback never
   depends on it. Reduced motion also keeps the static graphic. */
const Spectrum = {
  ctx: null, an: null, data: null, failed: false,
  BARS: 9,
  STATIC: [0.34, 0.62, 0.88, 0.52, 0.96, 0.7, 0.44, 0.78, 0.4],
  reduced() { return !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches); },
  ensure(audio) {
    if (this.an || this.failed) return;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) throw new Error('no AudioContext');
      this.ctx = new AC();
      const src = this.ctx.createMediaElementSource(audio);
      const an = this.ctx.createAnalyser();
      an.fftSize = 256; an.smoothingTimeConstant = 0.75;
      src.connect(an); an.connect(this.ctx.destination);
      this.an = an; this.data = new Uint8Array(an.frequencyBinCount);
    } catch (e) { this.failed = true; this.an = null; }
  },
  resume() {
    if (!this.ctx || this.ctx.state === 'running') return;
    try { const p = this.ctx.resume(); if (p && p.catch) p.catch(() => {}); } catch (e) { /* ignore */ }
  },
  levels() {
    if (!this.an || this.reduced()) return null;
    try { this.an.getByteFrequencyData(this.data); } catch (e) { return null; }
    const n = this.data.length, out = [];
    for (let i = 0; i < this.BARS; i++) { // log-spaced bands so every bar moves
      const a = Math.floor(Math.pow(n, i / this.BARS)), b = Math.max(a + 1, Math.floor(Math.pow(n, (i + 1) / this.BARS)));
      let m = 0;
      for (let j = a; j < b && j < n; j++) if (this.data[j] > m) m = this.data[j];
      out.push(m / 255);
    }
    return out;
  },
  draw(cv, live) {
    const { w, h, dpr } = Peaks.fit(cv);
    const ctx = cv.getContext('2d');
    if (!ctx || !cv.clientWidth) return;
    ctx.clearRect(0, 0, w, h);
    const lv = (live && this.levels()) || this.STATIC, isLive = lv !== this.STATIC;
    const gap = Math.round(2.5 * dpr), bw = Math.max(1, Math.floor((w - gap * (this.BARS - 1)) / this.BARS));
    const cs = getComputedStyle(cv);
    const g = ctx.createLinearGradient(0, h, 0, 0);
    g.addColorStop(0, cs.getPropertyValue('--accent').trim() || '#ff3fd2');
    g.addColorStop(1, cs.getPropertyValue('--accent-2').trim() || '#2de2ff');
    ctx.fillStyle = g;
    ctx.globalAlpha = isLive ? 1 : 0.4;
    const r = Math.min(bw / 2, 2 * dpr);
    for (let i = 0; i < this.BARS; i++) {
      const bh = Math.max(r * 2, Math.round(lv[i] * h));
      const x = i * (bw + gap);
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, h - bh, bw, bh, [r, r, 0, 0]); else ctx.rect(x, h - bh, bw, bh);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    cv.dataset.live = isLive ? 'true' : 'false';
  },
};

/* ---------------- Audition player ----------------
   Play-state hooks: #transport and <html> get .is-playing while audio plays,
   #transport carries data-playing-id, and --playhead (0..1) on #transport
   follows the position every frame while playing. */
const Player = {
  row: null,
  raf: 0,
  init() {
    this.audio = $('#audio');
    this.btn = $('#tp-play');
    this.scrub = $('#tp-scrub');
    this.vol = $('#tp-vol');
    this.loopBtn = $('#tp-loop');
    this.auto = $('#tp-auto');
    this.tp = $('#transport');
    this.spec = $('#tp-spec');
    this.oneShotBtn = $('#tp-oneshot');
    const vol = Store.get('volume', 80);
    this.vol.value = vol; this.audio.volume = vol / 100; this.renderVol();
    this.setLoop(Store.get('loop', false));
    this.auto.checked = Store.get('auto', false);
    this.setOneShot(Store.get('oneshot', false));
    if (this.oneShotBtn) this.oneShotBtn.addEventListener('click', () => this.setOneShot(!this.oneShot));
    this.vol.addEventListener('input', () => { this.audio.volume = this.vol.value / 100; Store.set('volume', +this.vol.value); this.renderVol(); });
    this.btn.addEventListener('click', () => this.toggle());
    this.loopBtn.addEventListener('click', () => this.setLoop(!this.audio.loop));
    this.auto.addEventListener('change', () => Store.set('auto', this.auto.checked));
    this.scrubbing = false;
    this.scrub.addEventListener('input', () => {
      this.scrubbing = true;
      if (this.audio.duration) { this.audio.currentTime = this.scrub.value / 1000 * this.audio.duration; this.renderTime(); }
    });
    this.scrub.addEventListener('change', () => { this.scrubbing = false; });
    this.audio.addEventListener('timeupdate', () => this.renderTime());
    this.audio.addEventListener('loadedmetadata', () => {
      this.scrub.disabled = false;
      if (this.pendingSeek !== null && isFinite(this.audio.duration)) { this.audio.currentTime = this.pendingSeek * this.audio.duration; this.pendingSeek = null; }
      this.renderTime();
    });
    this.audio.addEventListener('play', () => { Spectrum.resume(); this.renderState(); });
    this.audio.addEventListener('pause', () => this.renderState());
    this.audio.addEventListener('ended', () => this.renderState());
    this.audio.addEventListener('error', () => {
      if (!this.row || !this.audio.getAttribute('src')) return;
      const row = this.row;
      this.msg(`Could not play ${row.name}. The file may have moved or uses an encoding this browser can't decode.`, 'err', this.revealAction(row));
      this.renderState();
      if (Player.CONVERTED.includes(row.ext)) this.explainConvertError(row);
    });
    this.renderSub();
    this.setPlayhead(0);
    this.repaint();
    let rt;
    window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => this.repaint(), 120); });
    // The accent picker recolors every canvas.
    new MutationObserver(() => this.repaint()).observe(document.documentElement, { attributes: true, attributeFilter: ['data-accent'] });
  },
  repaint() {
    Peaks.paintAll();
    if (this.audio.paused) Spectrum.draw(this.spec, false);
  },
  setLoop(on) {
    this.audio.loop = !!on;
    this.loopBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    Store.set('loop', !!on);
  },
  CONVERTED: ['.aif', '.aiff', '.aifc'],  // served as WAV converted on the fly by the server
  pendingSeek: null,
  oneShot: false,
  /* Retrigger mode: every arrow-key move stops the current sound and restarts
     the newly selected row from 0 (also when the row did not change). */
  setOneShot(on) {
    this.oneShot = !!on;
    if (this.oneShotBtn) this.oneShotBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    Store.set('oneshot', !!on);
  },
  toggleOneShot() { this.setOneShot(!this.oneShot); toast('Retrigger on arrows ' + (this.oneShot ? 'on' : 'off')); },
  autoOn() { return this.auto.checked || this.oneShot; },
  revealAction(row) { return row && row.id ? { label: 'Show in Explorer', fn: () => reveal({ id: row.id }) } : null; },
  async explainConvertError(row) {
    try {
      const r = await fetch(API.audioUrl(row.id), { headers: { Range: 'bytes=0-0' }, cache: 'no-store' });
      if (r.ok || !this.row || this.row.id !== row.id) return;
      const j = await r.json().catch(() => null);
      if (j && j.error) this.msg(`Can't preview ${row.name}: ${j.error}`, 'warn', this.revealAction(row));
    } catch (e) { /* keep the generic message */ }
  },
  /* Seek to a fraction 0..1 of the loaded sample; starts playback if nothing is loaded yet. */
  seekFrac(f) {
    f = Math.max(0, Math.min(1, f));
    const a = this.audio;
    if (!this.row) return;
    if (a.getAttribute('src') && a.duration && isFinite(a.duration)) {
      a.currentTime = f * a.duration;
      this.renderTime();
      if (a.paused) this.play(null, false);
      return;
    }
    if (!this.playable(this.row)) { this.renderState(); return; }
    this.pendingSeek = f;
    this.play(null, false);
  },
  toggleAuto() { this.auto.checked = !this.auto.checked; Store.set('auto', this.auto.checked); toast('Auto-play on arrows ' + (this.auto.checked ? 'on' : 'off')); },
  msg(text, kind, action) {
    const m = $('#tp-msg');
    m.textContent = text || '';
    m.title = text || '';
    m.className = 'tp-msg' + (kind ? ' tp-msg-' + kind : '') + (action ? ' has-act' : '');
    if (text && action) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'tp-msg-act'; b.textContent = action.label;
      b.addEventListener('click', action.fn);
      m.append(' ', b);
    }
  },
  renderVol() { this.vol.style.setProperty('--v', this.vol.value + '%'); },
  renderSub() {
    const r = this.row;
    $('#tp-sub').textContent = r ? [r.pack, Fmt.catShort(r.category), r.bpm ? Fmt.bpm(r.bpm) + ' BPM' : '', r.key].filter(Boolean).join(' · ') : '';
  },
  decodable(row) {
    return !!row && (!row.kind || row.kind === 'audio') && row.preview !== 'unsupported' && row.preview !== 'none' && !API.fixture;
  },
  load(row) {
    this.row = row;
    $('#tp-name').textContent = row ? row.name : 'Nothing loaded';
    $('#tp-name').title = row ? row.abs_path || row.name : '';
    $('#tp-dur').textContent = Fmt.dur(row && row.duration_s);
    this.msg('');
    this.pendingSeek = null;
    this.audio.pause();
    this.audio.removeAttribute('src');
    this.scrub.value = 0; this.scrub.disabled = true;
    this.setPlayhead(0);
    this.renderSub();
    if (this.decodable(row)) Peaks.request(row.id, row.size);
    App.emit('player', row);
  },
  playable(row) {
    if (!row) return false;
    if (row.kind && row.kind !== 'audio') { this.msg(`${row.name} is not audio (${row.ext || row.kind}).`, 'info'); return false; }
    if (row.preview === 'unsupported') {
      const rex = ['.rex', '.rx2'].includes((row.ext || '').toLowerCase());
      this.msg(rex ? `Preview not supported in browser for ${row.ext}. REX/RX2 loops play in your DAW.`
        : `Preview not supported in browser for ${row.ext}. Open it in your DAW.`, 'warn', this.revealAction(row));
      return false;
    }
    if (row.preview === 'none') { this.msg(`No preview for ${row.ext}.`, 'info'); return false; }
    if (API.fixture) { this.msg('Audio unavailable in fixture mode. The player works with your real library.', 'info'); return false; }
    return true;
  },
  play(row, fromStart) {
    if (row && (!this.row || row.id !== this.row.id)) this.load(row);
    row = this.row;
    if (!this.playable(row)) { this.renderState(); return; }
    if (!this.audio.getAttribute('src')) this.audio.src = API.audioUrl(row.id);
    if (fromStart && this.oneShot && !this.audio.paused) this.audio.pause();  // retrigger: cut the tail now
    if (fromStart) { this.pendingSeek = null; this.audio.currentTime = 0; }
    // Build the analyser inside the user gesture so its AudioContext can start.
    Spectrum.ensure(this.audio); Spectrum.resume();
    const p = this.audio.play();
    if (p && p.catch) p.catch((e) => { if (e.name !== 'AbortError') this.msg('Playback failed: ' + e.message, 'err'); });
  },
  stop() { this.audio.pause(); try { this.audio.currentTime = 0; } catch (e) { /* no media */ } this.renderTime(); this.renderState(); },
  toggle(row) {
    if (row && (!this.row || row.id !== this.row.id)) { this.play(row, true); return; }
    if (!this.audio.paused) this.stop(); else this.play(null, false);
  },
  isPlaying(id) { return this.row && this.row.id === id && !this.audio.paused; },
  renderState() {
    const playing = !this.audio.paused;
    this.btn.innerHTML = `<svg><use href="#i-${playing ? 'stop' : 'play'}"/></svg>`;
    this.btn.setAttribute('aria-label', playing ? 'Stop' : 'Play');
    this.tp.classList.toggle('is-playing', playing);
    document.documentElement.classList.toggle('is-playing', playing);
    if (playing && this.row) this.tp.dataset.playingId = this.row.id; else delete this.tp.dataset.playingId;
    if (playing) this.startLoop(); else { this.stopLoop(); Spectrum.draw(this.spec, false); }
    App.emit('playstate', playing);
  },
  startLoop() {
    if (this.raf) return;
    const tick = () => {
      if (this.audio.paused) { this.raf = 0; return; }
      this.renderTime();
      Spectrum.draw(this.spec, true);
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  },
  stopLoop() { if (this.raf) cancelAnimationFrame(this.raf); this.raf = 0; },
  setPlayhead(f) {
    f = Math.max(0, Math.min(1, f || 0));
    this.tp.style.setProperty('--playhead', f.toFixed(4));
    document.documentElement.style.setProperty('--playhead', f.toFixed(4));
    const pid = this.tp.dataset.playingId;
    document.querySelectorAll('.ins-wave').forEach((w) => { const c = w.querySelector('canvas[data-wave-for]'); w.classList.toggle('is-live', !!pid && !!c && c.dataset.waveFor === pid); });
    this.scrub.style.setProperty('--p', (f * 100) + '%');
  },
  renderTime() {
    const a = this.audio;
    $('#tp-time').textContent = Fmt.dur(a.currentTime || 0);
    if (a.duration && isFinite(a.duration)) {
      $('#tp-dur').textContent = Fmt.dur(a.duration);
      if (!this.scrubbing) this.scrub.value = Math.round(a.currentTime / a.duration * 1000);
      this.setPlayhead(a.currentTime / a.duration);
    } else this.setPlayhead(0);
  },
};
