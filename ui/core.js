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

const STATUS_LABEL = { ok: 'OK', 'needs-review': 'Review', edited: 'Edited', held: 'Held', excluded: 'Excluded', done: 'Organized' };
const ACTION_LABEL = { move: 'Move', link: 'Link', keep: 'Keep', hold: 'Hold', exclude: 'Exclude' };
const SOURCE_LABEL = { tag: 'Tag', filename: 'Filename', folder: 'Folder', inference: 'Inferred', analysis: 'Analysis', manual: 'Manual' };

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

/* ---------------- Audition player ---------------- */
const Player = {
  row: null,
  init() {
    this.audio = $('#audio');
    this.btn = $('#tp-play');
    this.scrub = $('#tp-scrub');
    this.vol = $('#tp-vol');
    this.loopBtn = $('#tp-loop');
    this.auto = $('#tp-auto');
    const vol = Store.get('volume', 80);
    this.vol.value = vol; this.audio.volume = vol / 100;
    this.setLoop(Store.get('loop', false));
    this.auto.checked = Store.get('auto', false);
    this.vol.addEventListener('input', () => { this.audio.volume = this.vol.value / 100; Store.set('volume', +this.vol.value); });
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
    this.audio.addEventListener('loadedmetadata', () => { this.scrub.disabled = false; this.renderTime(); });
    this.audio.addEventListener('play', () => this.renderState());
    this.audio.addEventListener('pause', () => this.renderState());
    this.audio.addEventListener('ended', () => this.renderState());
    this.audio.addEventListener('error', () => {
      if (!this.row || !this.audio.getAttribute('src')) return;
      this.msg(`Could not play ${this.row.name}. The file may have moved or uses an encoding this browser can't decode.`, 'err');
      this.renderState();
    });
  },
  setLoop(on) {
    this.audio.loop = !!on;
    this.loopBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    Store.set('loop', !!on);
  },
  autoOn() { return this.auto.checked; },
  toggleAuto() { this.auto.checked = !this.auto.checked; Store.set('auto', this.auto.checked); toast('Auto-play on arrows ' + (this.auto.checked ? 'on' : 'off')); },
  msg(text, kind) {
    const m = $('#tp-msg');
    m.textContent = text || '';
    m.className = 'tp-msg' + (kind ? ' tp-msg-' + kind : '');
  },
  load(row) {
    this.row = row;
    $('#tp-name').textContent = row ? row.name : 'Nothing loaded';
    $('#tp-name').title = row ? row.abs_path || row.name : '';
    $('#tp-dur').textContent = '/ ' + Fmt.dur(row && row.duration_s);
    this.msg('');
    this.audio.pause();
    this.audio.removeAttribute('src');
    this.scrub.value = 0; this.scrub.disabled = true;
    App.emit('player', row);
  },
  playable(row) {
    if (!row) return false;
    if (row.kind && row.kind !== 'audio') { this.msg(`${row.name} is not audio (${row.ext || row.kind}).`, 'info'); return false; }
    if (row.preview === 'unsupported') { this.msg(`Preview not supported in browser for ${row.ext}. Open it in your DAW or reveal it in Explorer.`, 'warn'); return false; }
    if (row.preview === 'none') { this.msg(`No preview for ${row.ext}.`, 'info'); return false; }
    if (API.fixture) { this.msg('Audio unavailable in fixture mode. The player works with your real library.', 'info'); return false; }
    return true;
  },
  play(row, fromStart) {
    if (row && (!this.row || row.id !== this.row.id)) this.load(row);
    row = this.row;
    if (!this.playable(row)) { this.renderState(); return; }
    if (!this.audio.getAttribute('src')) this.audio.src = API.audioUrl(row.id);
    if (fromStart) this.audio.currentTime = 0;
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
    $('#transport').classList.toggle('is-playing', playing);
    App.emit('playstate', playing);
  },
  renderTime() {
    const a = this.audio;
    $('#tp-time').textContent = Fmt.dur(a.currentTime || 0);
    if (a.duration && isFinite(a.duration)) {
      $('#tp-dur').textContent = '/ ' + Fmt.dur(a.duration);
      if (!this.scrubbing) this.scrub.value = Math.round(a.currentTime / a.duration * 1000);
      this.scrub.style.setProperty('--p', (a.currentTime / a.duration * 100) + '%');
    } else this.scrub.style.setProperty('--p', '0%');
  },
};
