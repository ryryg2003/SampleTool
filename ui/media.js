/* media.js: owned by the media feature.
   - Waveform of the loaded sample in the deck (GET /api/peaks/<id>), click/drag to seek,
     moving playhead, dimmed played portion. The range input stays the accessible control.
   - Retrigger toggle key (O).
   - Drag a Library row out to a DAW / Explorer (Chromium DownloadURL, original bytes).
   - Inspector extras: Copy file to clipboard (Windows), REX/RX2 explanation + Reveal. */
'use strict';

const MEDIA_MIME = {
  '.wav': 'audio/wav', '.aif': 'audio/aiff', '.aiff': 'audio/aiff', '.aifc': 'audio/aiff', '.mp3': 'audio/mpeg',
  '.ogg': 'audio/ogg', '.flac': 'audio/flac', '.m4a': 'audio/mp4', '.opus': 'audio/ogg', '.mid': 'audio/midi', '.midi': 'audio/midi',
};

const Wave = {
  N: 800,          // fixed so the server-side cache (keyed by sha256 + n) is reused
  id: null,
  peaks: null,
  init() {
    this.el = $('#tp-wave');
    this.cv = $('#tp-wave-c');
    this.ph = $('#tp-wave-ph');
    this.audio = $('#audio');
    if (!this.el || !this.cv) return;
    this.ctx = this.cv.getContext('2d');
    this.colors();
    // The accent picker recolors the waveform.
    new MutationObserver(() => { this.colors(); this.build(); this.draw(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-accent'] });
    new ResizeObserver(() => this.resize()).observe(this.el);
    this.fetchSoon = debounce((row) => this.fetch(row), 70);
    App.on('player', (row) => this.load(row));
    App.on('playstate', (p) => { if (p) this.loop(); else this.draw(); });
    ['timeupdate', 'seeked', 'loadedmetadata', 'emptied'].forEach((e) => this.audio.addEventListener(e, () => this.draw()));
    // pointer seeking on the whole wave area (the range input underneath keeps keyboard access)
    this.el.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0 || !Player.row) return;
      ev.preventDefault(); // keep focus (and arrow keys) where they are
      this.el.setPointerCapture(ev.pointerId);
      this.seeking = true;
      this.seekAt(ev);
    });
    this.el.addEventListener('pointermove', (ev) => { if (this.seeking) this.seekAt(ev); });
    const end = (ev) => { if (!this.seeking) return; this.seeking = false; try { this.el.releasePointerCapture(ev.pointerId); } catch (e) { /* gone */ } };
    this.el.addEventListener('pointerup', end);
    this.el.addEventListener('pointercancel', end);
    this.setPh('Nothing loaded');
  },
  colors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (k, d) => (cs.getPropertyValue(k) || '').trim() || d;
    // Prism Aurora: unplayed peaks dim, played peaks lit in the accent gradient, white playhead with an accent glow.
    this.c = { wave: 'rgba(246, 239, 255, .26)', played: [v('--accent', '#ff3fd2'), v('--accent-2', '#2de2ff')], line: v('--line', 'rgba(255, 255, 255, .1)'), head: '#ffffff', glow: v('--accent', '#ff3fd2') };
  },
  seekAt(ev) {
    const r = this.el.getBoundingClientRect();
    if (r.width > 0) Player.seekFrac((ev.clientX - r.left) / r.width);
  },
  setPh(text, kind) {
    this.ph.textContent = text || '';
    this.el.classList.toggle('is-empty', !!text);
    this.el.dataset.state = kind || (text ? 'empty' : 'ready');
  },
  load(row) {
    this.id = row ? row.id : null;
    this.peaks = null;
    this.layers = null;
    if (!row) { this.setPh('Nothing loaded'); this.draw(); return; }
    if (row.kind && row.kind !== 'audio') { this.setPh('No waveform: not an audio file'); this.draw(); return; }
    if (row.preview === 'unsupported' && ['.rex', '.rx2'].includes((row.ext || '').toLowerCase())) {
      this.setPh('No waveform for REX/RX2 loops', 'unsupported'); this.draw(); return;
    }
    this.setPh('Loading waveform…', 'loading');
    this.draw();
    this.fetchSoon(row);
  },
  async fetch(row) {
    if (!row || row.id !== this.id) return;
    try {
      const d = await API.get('/api/peaks/' + encodeURIComponent(row.id), { n: this.N });
      if (row.id !== this.id) return;
      if (d.unsupported || !d.peaks || !d.peaks.length) { this.setPh(d.reason || 'No waveform for this file', 'unsupported'); this.draw(); return; }
      this.peaks = d.peaks;
      this.setPh('');
      this.build();
      this.draw();
    } catch (e) {
      if (row.id !== this.id) return;
      this.setPh(e.status === 404 ? 'Waveform unavailable: file not found' : 'Waveform unavailable', 'error');
      this.draw();
    }
  },
  resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(this.el.clientWidth * dpr)), h = Math.max(1, Math.round(this.el.clientHeight * dpr));
    if (this.cv.width === w && this.cv.height === h) return;
    this.cv.width = w; this.cv.height = h;
    this.build();
    this.draw();
  },
  /* Pre-render the waveform twice (normal + dimmed "played") at device resolution. */
  build() {
    this.layers = null;
    const P = this.peaks; if (!P) return;
    const W = this.cv.width, H = this.cv.height;
    if (W < 2 || H < 2) return;
    const mk = (color, alpha) => {
      const c = document.createElement('canvas'); c.width = W; c.height = H;
      const g = c.getContext('2d');
      if (Array.isArray(color)) { const gr = g.createLinearGradient(0, 0, W, 0); gr.addColorStop(0, color[0]); gr.addColorStop(1, color[1]); g.fillStyle = gr; } else g.fillStyle = color;
      g.globalAlpha = alpha;
      const mid = H / 2, amp = H / 2 - 1;
      const per = P.length / W;
      for (let x = 0; x < W; x++) {
        const a = Math.floor(x * per), b = Math.max(a + 1, Math.floor((x + 1) * per));
        let lo = 1, hi = -1;
        for (let k = a; k < b && k < P.length; k++) { if (P[k][0] < lo) lo = P[k][0]; if (P[k][1] > hi) hi = P[k][1]; }
        if (hi < lo) continue;
        const y0 = Math.floor(mid - hi * amp), y1 = Math.ceil(mid - lo * amp);
        g.fillRect(x, y0, 1, Math.max(1, y1 - y0));
      }
      return c;
    };
    this.layers = { base: mk(this.c.wave, 1), played: mk(this.c.played, 1) };
  },
  frac() {
    const a = this.audio;
    if (!Player.row || this.id !== Player.row.id || !a.getAttribute('src') || !a.duration || !isFinite(a.duration)) return null;
    return Math.max(0, Math.min(1, a.currentTime / a.duration));
  },
  draw() {
    const g = this.ctx; if (!g) return;
    const W = this.cv.width, H = this.cv.height, dpr = window.devicePixelRatio || 1;
    g.clearRect(0, 0, W, H);
    const f = this.frac();
    const px = f === null ? 0 : Math.round(f * W);
    if (this.layers) {
      g.drawImage(this.layers.base, 0, 0);
      if (px > 0) { g.clearRect(0, 0, px, H); g.drawImage(this.layers.played, 0, 0, px, H, 0, 0, px, H); }
    } else {
      g.fillStyle = this.c.line;
      g.fillRect(0, Math.floor(H / 2), W, Math.max(1, Math.round(dpr)));
      if (px > 0) { g.fillStyle = this.c.played[0]; g.fillRect(0, Math.floor(H / 2), px, Math.max(1, Math.round(dpr))); }
    }
    if (f !== null) {
      const lw = Math.max(1, Math.round(1.5 * dpr));
      g.save();
      g.shadowColor = this.c.glow; g.shadowBlur = 8 * dpr;
      g.fillStyle = this.c.head;
      g.fillRect(Math.min(W - lw, Math.max(0, px - Math.floor(lw / 2))), 0, lw, H);
      g.restore();
    }
  },
  loop() {
    if (this.raf) return;
    const tick = () => { this.raf = 0; this.draw(); if (!this.audio.paused) this.raf = requestAnimationFrame(tick); };
    this.raf = requestAnimationFrame(tick);
  },
  /* For tests: number of non-transparent pixels in the canvas. */
  inkCount() {
    const W = this.cv.width, H = this.cv.height;
    const d = this.ctx.getImageData(0, 0, W, H).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
    return n;
  },
};

const Media = {
  /* Chromium drag-out: DownloadURL makes Explorer / DAWs receive the original file. */
  onDragStart(i, row, ev) {
    const dt = ev.dataTransfer;
    if (!dt || !row) return false;
    const url = new URL(API.audioUrl(row.id) + '?download=1', location.href).href;
    const mime = MEDIA_MIME[(row.ext || '').toLowerCase()] || 'application/octet-stream';
    const name = (row.name || 'sample').replace(/[:\r\n]/g, '_');
    try { dt.setData('DownloadURL', `${mime}:${name}:${url}`); } catch (e) { /* not Chromium */ }
    const p = row.final_path || row.abs_path || '';
    if (p) {
      dt.setData('text/uri-list', Media.fileUri(p));
      dt.setData('text/plain', p);
    }
    dt.effectAllowed = 'copy';
    return true;
  },
  fileUri(p) {
    let s = p.replace(/\\/g, '/');
    if (/^\/\/[^/]/.test(s)) return 'file:' + encodeURI(s);           // UNC \\server\share
    if (!s.startsWith('/')) s = '/' + s;                                  // C:/...
    return 'file://' + encodeURI(s).replace(/#/g, '%23').replace(/\?/g, '%3F');
  },
  clipboardAvailable() {
    const m = App.state && App.state.media;
    return !(m && m.clipboard_file === false);
  },
  async copyFile(row) {
    try {
      await API.post('/api/clipboard/file', { id: row.id });
      toast(API.fixture ? 'Fixture mode: the file would be on the clipboard now' : `Copied ${row.name}. Paste it into Explorer or your DAW.`);
    } catch (e) { toast('Could not copy the file: ' + e.message, 'err'); }
  },
  /* Add media actions to the inspector each time library.js re-renders it. */
  decorateInspector() {
    const box = $('#inspector');
    if (!box || typeof Library === 'undefined' || Library.sel.size > 1) return;
    const d = Library.detail || Library.current;
    const acts = $('.ins-actions', box);
    if (!d || !acts) return;
    if (this.clipboardAvailable() && !$('#ins-clipfile', box)) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'ib'; b.id = 'ins-clipfile';
      b.setAttribute('aria-label', 'Copy file to clipboard'); b.title = 'Copy file to clipboard (paste into Explorer or a DAW)';
      b.innerHTML = '<svg><use href="#i-clip"/></svg>';
      b.addEventListener('click', () => this.copyFile(d));
      acts.appendChild(b);
    }
    const rex = ['.rex', '.rx2'].includes((d.ext || '').toLowerCase());
    if (rex && !$('#ins-rex', box)) {
      const n = document.createElement('div');
      n.className = 'note note-warn media-note'; n.id = 'ins-rex';
      n.innerHTML = '<p>REX/RX2 loops are sliced audio that browsers cannot play. Audition it in Reason, ReCycle or your DAW.</p>';
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'btn btn-small'; b.innerHTML = '<svg><use href="#i-folder"/></svg>Show in Explorer';
      b.addEventListener('click', () => reveal({ id: d.id }));
      n.appendChild(b);
      const old = $('.note-warn', box);
      if (old) old.replaceWith(n); else acts.after(n);
    }
  },
};

App.features.push({
  init() {
    Wave.init();
    App.keyHandlers.push((ev) => {
      if (ev.ctrlKey || ev.metaKey || ev.altKey || (ev.key !== 'o' && ev.key !== 'O')) return false;
      ev.preventDefault();
      Player.toggleOneShot();
      return true;
    });
  },
  afterInit() {
    if (typeof Library !== 'undefined' && Library.table) {
      Library.table.o.draggable = true;
      Library.table.o.onDragStart = (i, row, ev) => Media.onDragStart(i, row, ev);
      Library.table.render();
    }
    const box = $('#inspector');
    if (box) new MutationObserver(() => Media.decorateInspector()).observe(box, { childList: true });
    App.on('state', () => Media.decorateInspector());
  },
});

/* Fixture mode: no real audio files, so waveforms show their placeholder. */
window.FixtureExt.push((method, path, query, body, st, h) => {
  if (method === 'GET' && /^\/api\/peaks\//.test(path)) {
    return h.ok({ id: decodeURIComponent(path.split('/').pop()), unsupported: true, reason: 'No waveform in fixture mode (no real audio files)' });
  }
  if (method === 'POST' && path === '/api/clipboard/file') return body && body.id ? h.ok({ ok: true, fixture: true }) : h.err(400, 'id required');
  return null;
});
