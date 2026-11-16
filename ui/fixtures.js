/* Fixture mode: an in-memory fake of the HTTP API in docs/CONTRACT.md.
   Enabled with ?fixture=1 or when /api/state cannot be loaded.
   Deterministic synthetic data (~2,000 rows, 15 packs, kits, duplicates,
   held items, a plan and a move log). Edits mutate memory only. */
// Feature files push (method, path, query, body, state, helpers) => response or null.
window.FixtureExt = window.FixtureExt || [];
(function () {
  'use strict';

  function rng(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const R = rng(20261115);
  const pick = (a) => a[Math.floor(R() * a.length)];
  const between = (a, b) => a + R() * (b - a);
  const int = (a, b) => Math.floor(between(a, b + 1));
  function hex(str, len) {
    let h1 = 0x811c9dc5, h2 = 0x01000193 ^ str.length;
    for (let i = 0; i < str.length; i++) {
      h1 = Math.imul(h1 ^ str.charCodeAt(i), 16777619);
      h2 = Math.imul(h2 ^ str.charCodeAt(i), 2246822519);
    }
    let out = '';
    let a = h1 >>> 0, b = h2 >>> 0;
    while (out.length < len) {
      out += a.toString(16).padStart(8, '0') + b.toString(16).padStart(8, '0');
      a = Math.imul(a ^ (a >>> 13), 3266489917) >>> 0;
      b = Math.imul(b ^ (b >>> 11), 668265263) >>> 0;
    }
    return out.slice(0, len);
  }
  const randHex = (n) => { let s = ''; while (s.length < n) s += Math.floor(Math.random() * 16).toString(16); return s; };

  const ROOT = 'E:\\Sample Packs';
  const DEST = ROOT + '\\Samples';
  const win = (rel) => ROOT + '\\' + rel.replace(/\//g, '\\');

  const PACKS = [
    ['Lowtide Sounds', 'LTS', 'Basement Tapes Vol 2', ['Hip Hop', 'Lo-Fi']],
    ['Lowtide Sounds', 'LTS', 'Night Bus', ['Lo-Fi', 'R&B']],
    ['Halcyon Drums', 'HD', 'Breaks Cabinet', ['Drum & Bass', 'Hip Hop']],
    ['Halcyon Drums', 'HD', 'Rimshot Republic', ['House', 'Afrobeats']],
    ['Kiln Records', 'KILN', 'Fired Keys', ['R&B', 'Lo-Fi']],
    ['Mothership Audio', 'MSA', 'Orbit Trap Essentials', ['Trap']],
    ['Mothership Audio', 'MSA', '808 Gravity', ['Trap', 'Hip Hop']],
    ['Static Garden', 'SG', 'Tape Hiss Botany', ['Ambient', 'Lo-Fi']],
    ['Copper Wire', 'CW', 'Warehouse Tools', ['Techno', 'House']],
    ['Paper Tiger Samples', 'PTS', 'Soul Chops', ['Hip Hop', 'R&B']],
    ['Dustline', 'DL', 'Desert Guitars', ['Ambient', 'Hip Hop']],
    ['Ninefold', 'NF', 'Jungle Pressure', ['Drum & Bass']],
    ['Greyhound Audio', 'GHA', 'Afro Percussion Suite', ['Afrobeats', 'House']],
    ['Sundial Sounds', 'SUN', 'Golden Hour Vox', ['R&B', 'House']],
    ['Velvet Room', 'VR', 'Late Set Synths', ['Techno', 'Ambient']],
  ];
  const KEYS = ['C', 'Cm', 'C#m', 'D', 'Dm', 'Ebm', 'E', 'Em', 'F', 'Fm', 'F#m', 'G', 'Gm', 'Abm', 'A', 'Am', 'Bbm', 'B', 'Bm'];
  const CATS = {
    'One-Shots/Drums': [['Kick', 'Snare', 'Clap', 'Hi-Hat', 'Open Hat', 'Perc', 'Rim', 'Crash', 'Tom'], 'one-shot', 30],
    'One-Shots/Bass & 808s': [['808', 'Sub', 'Bass'], 'one-shot', 8],
    'One-Shots/Instruments': [['Piano', 'Rhodes', 'Pluck', 'Pad', 'Synth', 'Guitar', 'Brass', 'Stab'], 'one-shot', 10],
    'One-Shots/FX & Vocals': [['Riser', 'Impact', 'Vox', 'Chop', 'Sweep'], 'one-shot', 7],
    'Loops/Drum Loops': [['Drums', 'Top', 'Hat Loop', 'Perc Loop', 'Break'], 'loop', 16],
    'Loops/Melody Loops': [['Keys', 'Guitar', 'Synth', 'Strings', 'Bells'], 'loop', 14],
    'Loops/Bass Loops': [['Bass', '808'], 'loop', 5],
    'Loops/Vocal & FX Loops': [['Vocal', 'FX', 'Texture'], 'loop', 5],
    'Presets & MIDI': [['MIDI', 'Preset'], null, 3],
    'Unsorted': [[null], null, 2],
  };
  const CAT_KEYS = Object.keys(CATS);
  const CAT_TOTAL = CAT_KEYS.reduce((s, k) => s + CATS[k][2], 0);
  function pickCat() {
    let r = R() * CAT_TOTAL;
    for (const k of CAT_KEYS) { r -= CATS[k][2]; if (r <= 0) return k; }
    return CAT_KEYS[0];
  }
  const WORDS = ['Dusty', 'Warm', 'Crunchy', 'Hollow', 'Tight', 'Wide', 'Sour', 'Velvet', 'Grit', 'Glass', 'Smoke', 'Tape', 'Vinyl', 'Neon', 'Night', 'Bounce', 'Swing', 'Deep', 'Bright', 'Muffled'];
  const TAGS = ['dark', 'warm', 'punchy', 'vintage', 'clean', 'saturated', 'airy', 'sidechain', 'swing', 'analog', 'chopped', 'reversed'];
  const SOURCES = ['tag', 'filename', 'filename', 'folder', 'folder', 'inference', 'analysis'];

  const items = [];
  const byId = new Map();
  const kits = [];
  const kitById = new Map();

  function provFor(src) {
    const s = src || pick(SOURCES);
    return { source: s, confidence: (s === 'tag' || s === 'manual') ? null : Math.round(between(0.42, 0.99) * 100) / 100 };
  }

  function makeItem(o) {
    const rel = o.rel_path;
    const origin = o.origin || 'incoming';
    const id = hex(origin + '|' + rel.toLowerCase(), 16);
    const ext = (o.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
    const audioExt = ['.wav', '.aif', '.aiff', '.flac', '.mp3', '.ogg', '.rx2', '.rex'].includes(ext);
    const kind = audioExt ? 'audio' : ext === '.mid' ? 'midi' : ext === '.als' || ext === '.flp' ? 'project' : ext === '.fxp' ? 'preset' : ext === '.txt' || ext === '.pdf' ? 'document' : 'other';
    const preview = audioExt ? (['.wav', '.mp3', '.flac', '.ogg'].includes(ext) ? 'native' : 'unsupported') : 'none';
    const it = Object.assign({
      id, origin, name: o.name, rel_path: rel, abs_path: win(rel), ext, kind, preview,
      size: o.size, pack: o.pack || null, manufacturer: o.manufacturer || null,
      category: o.category, instrument: o.instrument || null, loop_type: o.loop_type || null,
      bpm: o.bpm || null, key: o.key || null, genre: o.genre || null, tags: o.tags || [],
      duration_s: kind === 'audio' ? o.duration_s : null,
      sample_rate: kind === 'audio' ? pick([44100, 44100, 48000]) : null,
      channels: kind === 'audio' ? pick([1, 2, 2]) : null,
      bits: kind === 'audio' ? pick([16, 24, 24]) : null,
      sha256: o.sha256 || hex('sha|' + rel, 64),
      kit_id: null, kit_name: null, excluded: false, warnings: o.warnings || [],
      review_status: 'ok', proposed_dest: null, final_path: null, dup_count: 1, prov: {},
      tags_raw: o.tags_raw || {},
    });
    for (const k of ['category', 'instrument', 'loop_type', 'bpm', 'key', 'genre', 'tags', 'manufacturer']) {
      const v = it[k];
      if (v === null || v === undefined || (Array.isArray(v) && !v.length) || v === 'Unsorted') it.prov[k] = { source: null, confidence: null };
      else it.prov[k] = provFor(k === 'manufacturer' ? 'folder' : (k === 'bpm' && it.tags_raw.acid_bpm) ? 'tag' : null);
    }
    items.push(it);
    byId.set(id, it);
    return it;
  }

  // ---- Packs -------------------------------------------------------------
  PACKS.forEach(([maker, abbr, title, genres], pi) => {
    const pack = maker + ' - ' + title;
    const n = int(108, 140);
    const counters = {};
    for (let i = 0; i < n; i++) {
      const cat = pickCat();
      const [insts, loopType] = CATS[cat];
      const inst = pick(insts);
      const isLoop = loopType === 'loop';
      const bpm = isLoop ? pick([70, 75, 80, 85, 88, 90, 92, 95, 100, 120, 122, 124, 126, 128, 140, 150, 160, 170, 174]) : null;
      const key = (isLoop && inst !== 'Drums' && inst !== 'Top' && inst !== 'Hat Loop' && inst !== 'Perc Loop' && inst !== 'Break') || ['Piano', 'Rhodes', 'Pluck', 'Pad', 'Synth', 'Stab', '808', 'Bass', 'Guitar'].includes(inst) ? (R() < 0.85 ? pick(KEYS) : null) : null;
      const cnt = (counters[inst] = (counters[inst] || 0) + 1);
      let ext = '.wav', r = R();
      if (r < 0.06) ext = '.aif'; else if (r < 0.1) ext = '.mp3'; else if (r < 0.13) ext = '.flac'; else if (r < 0.145) ext = '.rx2';
      let name, folder;
      if (cat === 'Presets & MIDI') {
        ext = inst === 'MIDI' ? '.mid' : '.fxp';
        name = `${abbr}_${pick(WORDS)}_${inst === 'MIDI' ? 'Chords' : 'Lead'}_${String(cnt).padStart(2, '0')}${ext}`;
        folder = inst === 'MIDI' ? 'MIDI' : 'Presets';
      } else if (cat === 'Unsorted') {
        name = pick(['Audio ', 'Untitled ', 'bounce_', 'sample ', 'rec_']) + int(1, 99) + pick(['.wav', '.wav', '.aif']);
        folder = pick(['Extras', 'Misc', 'Bonus']);
        ext = name.slice(name.lastIndexOf('.'));
      } else if (isLoop) {
        name = `${abbr}_${bpm}_${key ? key + '_' : ''}${inst.replace(/ /g, '')}_${pick(WORDS)}_${String(cnt).padStart(2, '0')}${ext}`;
        folder = 'Loops/' + inst.replace(/ Loop$/, '') + 's';
      } else {
        name = `${abbr}_${inst.replace(/ /g, '')}_${pick(WORDS)}${key ? '_' + key : ''}_${String(cnt).padStart(2, '0')}${ext}`;
        folder = 'One Shots/' + inst + 's';
      }
      const dur = isLoop ? Math.round((pick([4, 8, 8, 16]) * 4 * 60 / bpm) * 1000) / 1000 : (cat === 'Presets & MIDI' ? null : Math.round(between(0.12, 3.2) * 1000) / 1000);
      const size = cat === 'Presets & MIDI' ? int(800, 60000) : Math.round((dur || 1) * 44100 * 2 * 3 * between(0.4, 1.05));
      const warnings = [];
      if (ext === '.rx2') warnings.push('REX2 audio: header not parsed, duration unknown');
      if (cat === 'Unsorted') warnings.push('Category could not be determined from tags, filename or folder');
      if (R() < 0.02) warnings.push('WAV smpl chunk loop points outside data range');
      const it = makeItem({
        rel_path: `${pack}/${folder}/${name}`, name, pack, manufacturer: maker, category: cat,
        instrument: inst, loop_type: loopType, bpm, key, genre: R() < 0.8 ? pick(genres) : null,
        tags: R() < 0.6 ? [pick(TAGS), pick(TAGS)].filter((v, i, a) => a.indexOf(v) === i) : [],
        duration_s: dur, size, warnings, tags_raw: isLoop && R() < 0.4 ? { acid_bpm: bpm } : {},
      });
      if (cat === 'Unsorted') { it.review_status = 'held'; }
      else if (Object.values(it.prov).some((p) => p.confidence !== null && p.confidence < 0.55)) it.review_status = 'needs-review';
      if (pi === 3 && i % 41 === 7) { it.excluded = true; it.review_status = 'excluded'; }
    }
  });

  // ---- Kits --------------------------------------------------------------
  const KIT_DEFS = [
    [0, 'Sunday Drive', 88, 'Fm', 0.92],
    [1, 'Last Stop', 80, 'Am', 0.81],
    [4, 'Clay Hands', 92, 'Ebm', 0.88],
    [5, 'Zero Gravity', 140, 'C#m', 0.74],
    [7, 'Greenhouse', 75, 'D', 0.58],
    [9, 'Ladies First', 95, 'Gm', 0.9],
    [12, 'Lagos Ride', 112, 'F#m', 0.67],
    [14, 'Room 404', 124, 'Bbm', 0.84],
  ];
  KIT_DEFS.forEach(([pi, kname, bpm, key, conf], ki) => {
    const [maker, abbr, title, genres] = PACKS[pi];
    const pack = maker + ' - ' + title;
    const root_rel = `${pack}/Construction Kits/${kname}`;
    const kid = 'k' + hex(root_rel.toLowerCase() + '|' + kname.toLowerCase(), 16);
    const kit = {
      id: kid, name: kname, manufacturer: maker, pack, root_rel, member_ids: [], bpm, key,
      evidence: [
        `Folder "${kname}" sits under "Construction Kits"`,
        `All stems share ${bpm} BPM and key ${key} in their filenames`,
        'Contains a full mix plus stems and MIDI',
      ],
      confidence: conf, held: ki === 4, hold_reason: ki === 4 ? 'Low confidence: stems disagree on key (D vs Dm)' : null,
      excluded: false, manual: false,
      ref_checks: [
        { check: 'midi-present', performed: true, result: 'ok', detail: '2 MIDI files found in kit folder' },
        { check: 'shared-bpm-key', performed: true, result: ki === 4 ? 'warning' : 'ok', detail: ki === 4 ? '3 stems tagged Dm, 5 tagged D' : 'All stems agree' },
        { check: 'project-sample-refs', performed: false, result: 'not-checked', detail: 'Project files are not parsed; sample references were not verified' },
      ],
    };
    kits.push(kit); kitById.set(kid, kit);
    const stems = ['Full Mix', 'Drums', 'Kick', 'Snare', 'Hats', 'Bass', 'Keys', 'Lead', 'Pad', 'Vox', 'FX'];
    const ns = int(6, 10);
    for (let s = 0; s < ns; s++) {
      const stem = stems[s];
      const ext = R() < 0.1 ? '.aif' : '.wav';
      const name = `${abbr}_${kname.replace(/ /g, '')}_${bpm}_${key}_${stem.replace(/ /g, '')}${ext}`;
      const dur = Math.round(16 * 4 * 60 / bpm * 1000) / 1000;
      const isDrum = ['Drums', 'Kick', 'Snare', 'Hats'].includes(stem);
      const it = makeItem({
        rel_path: `${root_rel}/${name}`, name, pack, manufacturer: maker,
        category: isDrum ? 'Loops/Drum Loops' : stem === 'Bass' ? 'Loops/Bass Loops' : stem === 'Vox' || stem === 'FX' ? 'Loops/Vocal & FX Loops' : 'Loops/Melody Loops',
        instrument: stem, loop_type: 'loop', bpm, key: isDrum ? null : key, genre: genres[0], tags: ['kit'],
        duration_s: dur, size: Math.round(dur * 44100 * 6),
      });
      it.kit_id = kid; it.kit_name = kname; kit.member_ids.push(it.id);
    }
    for (const extra of [[`${kname} Chords.mid`, 'MIDI', 'Presets & MIDI'], [`${kname} Melody.mid`, 'MIDI', 'Presets & MIDI'], [`${kname} Project.als`, null, 'Presets & MIDI']]) {
      const it = makeItem({ rel_path: `${root_rel}/${extra[0]}`, name: extra[0], pack, manufacturer: maker, category: extra[2], instrument: extra[1], bpm, key, size: int(2000, 90000) });
      it.kit_id = kid; it.kit_name = kname; kit.member_ids.push(it.id);
    }
    if (kit.held) kit.member_ids.forEach((id) => { byId.get(id).review_status = 'held'; });
  });

  // ---- Duplicates (same content in another pack) ------------------------
  const audioIncoming = items.filter((i) => i.kind === 'audio' && !i.kit_id && i.category !== 'Unsorted');
  for (let d = 0; d < 64; d++) {
    const src = audioIncoming[int(0, audioIncoming.length - 1)];
    const p = PACKS[int(0, PACKS.length - 1)];
    const pack = p[0] + ' - ' + p[2];
    if (pack === src.pack) continue;
    const it = makeItem({
      rel_path: `${pack}/Bonus/${src.name}`, name: src.name, pack, manufacturer: p[0], category: src.category,
      instrument: src.instrument, loop_type: src.loop_type, bpm: src.bpm, key: src.key, genre: src.genre,
      tags: src.tags.slice(), duration_s: src.duration_s, size: src.size, sha256: src.sha256,
    });
    it.warnings.push('Exact duplicate of ' + src.rel_path);
  }
  // ---- Existing library files -------------------------------------------
  for (let l = 0; l < 42; l++) {
    const cat = pick(['One-Shots/Drums', 'One-Shots/Bass & 808s', 'Loops/Drum Loops', 'Loops/Melody Loops']);
    const inst = pick(CATS[cat][0]);
    const name = `Library_${inst.replace(/ /g, '')}_${String(l + 1).padStart(3, '0')}.wav`;
    const it = makeItem({ origin: 'library', rel_path: `Samples/${cat}/${name}`, name, category: cat, instrument: inst, loop_type: CATS[cat][1], bpm: CATS[cat][1] === 'loop' ? 90 : null, duration_s: between(0.2, 8), size: int(90000, 3000000) });
    it.review_status = 'done'; it.final_path = it.abs_path;
  }

  // Library already holds copies of a few kit stems -> those kit members become hard links.
  kits.slice(0, 4).forEach((k, n) => {
    const m = byId.get(k.member_ids[1 + n]);
    const it = makeItem({ origin: 'library', rel_path: `Samples/${m.category}/${m.name}`, name: m.name, category: m.category, instrument: m.instrument, loop_type: m.loop_type, bpm: m.bpm, key: m.key, duration_s: m.duration_s, size: m.size, sha256: m.sha256 });
    it.review_status = 'done'; it.final_path = it.abs_path;
  });

  function recomputeDups() {
    const groups = new Map();
    for (const it of items) { const g = groups.get(it.sha256) || []; g.push(it); groups.set(it.sha256, g); }
    for (const g of groups.values()) for (const it of g) it.dup_count = g.length;
    return groups;
  }
  function proposeDest(it) {
    if (it.origin === 'library') return it.abs_path;
    if (it.excluded) return null;
    const kit = it.kit_id ? kitById.get(it.kit_id) : null;
    if (kit && !kit.excluded) return `${DEST}\\Complete Kits\\${kit.manufacturer || 'Unknown Maker'}\\${kit.name}\\${it.name}`;
    if (!it.category || it.category === 'Unsorted') return null;
    return `${DEST}\\${it.category.replace(/\//g, '\\')}\\${it.name}`;
  }
  items.forEach((it) => { it.proposed_dest = proposeDest(it); });
  recomputeDups();

  // ---- Plan ---------------------------------------------------------------
  const st = {
    root: ROOT, dest_root: DEST, data_dir: 'C:\\Users\\Producer\\AppData\\Local\\SampleOrganizer',
    has_inventory: true,
    progress: { job: 'idle', phase: 'idle', running: false, done: 0, total: 0, bytes_done: 0, bytes_total: 0, current: '', warnings: 0, errors: [], message: '' },
    plan: null, execution: null, ops: [], results: [],
  };

  function buildPlan() {
    const groups = recomputeDups();
    const ops = [];
    const destKeys = new Map();
    items.filter((i) => i.origin === 'library').forEach((i) => destKeys.set(i.abs_path.toLowerCase(), i.sha256));
    const canonicalFor = new Map();
    for (const [sha, g] of groups) {
      const lib = g.find((i) => i.origin === 'library');
      const inKit = g.find((i) => i.kit_id && !i.excluded);
      canonicalFor.set(sha, lib || inKit || g.slice().sort((a, b) => a.rel_path < b.rel_path ? -1 : 1)[0]);
    }
    let collisions = 0, dupBytes = 0;
    for (const it of items) {
      if (it.origin !== 'incoming') continue;
      const kit = it.kit_id ? kitById.get(it.kit_id) : null;
      const op = { item_id: it.id, source: it.abs_path, dest: null, link_to: null, depends_on: null, canonical: null, sha256: it.sha256, size: it.size, kit_id: it.kit_id, category: it.category, flags: [], name: it.name, pack: it.pack, kit_name: it.kit_name, rel_path: it.rel_path };
      const canon = canonicalFor.get(it.sha256);
      if (it.excluded || (kit && kit.excluded)) { op.action = 'exclude'; op.reason = kit && kit.excluded ? `Kit "${kit.name}" excluded by you` : 'Excluded by you'; }
      else if (kit && kit.held) { op.action = 'hold'; op.reason = `Kit "${kit.name}" is held: ${kit.hold_reason || 'needs review'}`; op.flags.push('kit-held'); }
      else if (!it.category || it.category === 'Unsorted') { op.action = 'hold'; op.reason = 'Category unknown; assign one to organize this file'; op.flags.push('unknown-category'); }
      else if (canon !== it) {
        op.flags.push('duplicate'); op.canonical = canon.proposed_dest || canon.abs_path; dupBytes += it.size;
        if (it.kit_id) { op.action = 'link'; op.dest = it.proposed_dest; op.link_to = op.canonical; op.reason = 'Kit member duplicates a file kept elsewhere; hard link keeps the kit complete without a second copy'; }
        else { op.action = 'keep'; op.reason = 'Exact duplicate of ' + (canon.rel_path); }
      } else {
        op.action = 'move'; let dest = it.proposed_dest; const k = dest.toLowerCase();
        if (destKeys.has(k) && destKeys.get(k) !== it.sha256) {
          dest = dest.replace(/(\.[^.]+)$/, ` [${it.pack}]$1`); op.flags.push('name-collision'); collisions++;
          op.reason = 'Name already used by different audio; suffixed with pack name';
        } else op.reason = it.kit_id ? 'Kit kept together under Complete Kits' : 'Organized by category ' + it.category;
        destKeys.set(dest.toLowerCase(), it.sha256); op.dest = dest;
        const minConf = Math.min(...Object.values(it.prov).map((p) => p.confidence === null ? 1 : p.confidence));
        if (minConf < 0.55) op.flags.push('low-confidence');
      }
      if (it.warnings.length) op.flags.push('warning');
      op.id = 'o' + hex(op.action + '|' + it.id + '|' + (op.dest || ''), 16);
      ops.push(op);
    }
    const by_action = {}; const bytes_by_action = {};
    ops.forEach((o) => { by_action[o.action] = (by_action[o.action] || 0) + 1; bytes_by_action[o.action] = (bytes_by_action[o.action] || 0) + o.size; });
    const inc = items.filter((i) => i.origin === 'incoming');
    st.ops = ops;
    st.plan = {
      plan_hash: hex(ops.map((o) => o.id).join(',') + randHex(8), 64), created: new Date().toISOString(), stale: false, approved: false,
      summary: {
        total_files: inc.length, total_bytes: inc.reduce((s, i) => s + i.size, 0), audio_files: inc.filter((i) => i.kind === 'audio').length,
        packs: PACKS.length, kits: kits.filter((k) => k.member_ids.length).length,
        duplicate_groups: [...groups.values()].filter((g) => g.length > 1).length, duplicate_bytes_avoided: dupBytes,
        unknown_formats: inc.filter((i) => i.kind === 'other').length + inc.filter((i) => i.preview === 'unsupported' && i.ext.startsWith('.r')).length,
        collisions, by_action, bytes_by_action, held: by_action.hold || 0,
      },
    };
  }
  buildPlan();

  function makeResults(ops, frac) {
    const now = Date.now();
    return ops.slice(0, Math.floor(ops.length * frac)).map((o, i) => {
      let status = o.action === 'move' || o.action === 'link' ? 'done' : o.action === 'hold' ? 'held' : 'skipped';
      let error = null;
      if (status === 'done' && i % 97 === 13) { status = 'held'; error = 'Source changed since the plan was built (size differs); not moved'; }
      if (status === 'done' && i % 211 === 50) { status = 'failed'; error = 'Access denied: file is open in another program'; }
      if (status === 'done' && i % 157 === 3) status = 'already-done';
      if (status === 'held' && !error) error = o.reason;
      return { op_id: o.id, action: o.action, status, source: o.source, dest: o.dest, timestamp: new Date(now - (ops.length - i) * 850).toISOString(), error, file_id: status === 'done' ? '2:' + (100000 + i) : null, name: o.name };
    });
  }
  function setExecution(results) {
    const rs = {}; results.forEach((r) => { rs[r.status] = (rs[r.status] || 0) + 1; });
    st.results = results;
    st.execution = {
      ran: new Date().toISOString(), results_summary: rs,
      reports: {
        'Sample_Catalog.csv': DEST + '\\Catalog\\Sample_Catalog.csv',
        'Source_Path_Index.csv': DEST + '\\Catalog\\Source_Path_Index.csv',
        'Duplicate_Report.csv': DEST + '\\Catalog\\Duplicate_Report.csv',
        'Organization_Report.csv': DEST + '\\Catalog\\Organization_Report.csv',
      },
      verification: {
        catalog_paths_resolved: (rs.done || 0) + (rs['already-done'] || 0), catalog_paths_missing: 0,
        kit_assets_present: true, unique_hashes: 1874, physical_files: 1874,
        storage_avoided_bytes: st.plan.summary.duplicate_bytes_avoided, space_freed_bytes: Math.round(st.plan.summary.duplicate_bytes_avoided * 0.38),
        unresolved: results.filter((r) => r.status === 'failed' || r.status === 'held').slice(0, 25).map((r) => r.source),
      },
    };
  }
  setExecution(makeResults(st.ops, 0.22));

  function markStale() { if (st.plan) { st.plan.stale = true; st.plan.approved = false; } }

  // ---- Jobs -----------------------------------------------------------------
  let jobTimer = null;
  function runJob(job, phases, total, onDone) {
    const p = st.progress;
    Object.assign(p, { job, running: true, done: 0, total, bytes_done: 0, bytes_total: total * 2400000, warnings: 0, errors: [], message: '', phase: phases[0], current: '' });
    let tick = 0;
    clearInterval(jobTimer);
    jobTimer = setInterval(() => {
      tick++;
      p.done = Math.min(total, p.done + Math.ceil(total / 18));
      p.bytes_done = Math.round(p.bytes_total * p.done / total);
      p.phase = phases[Math.min(phases.length - 1, Math.floor(p.done / total * phases.length))];
      const it = items[(tick * 37) % items.length];
      p.current = it.abs_path;
      if (tick % 4 === 0) { p.warnings++; }
      if (tick === 7 && job === 'scan') p.errors.push({ path: win('Static Garden - Tape Hiss Botany/Extras/locked_take.wav'), error: 'Permission denied while hashing; file skipped' });
      if (p.done >= total) {
        clearInterval(jobTimer); jobTimer = null;
        Object.assign(p, { running: false, phase: 'complete', current: '', message: job + ' finished' });
        onDone && onDone();
        setTimeout(() => { if (!p.running) p.job = 'idle'; }, 200);
      }
    }, 160);
  }

  // ---- Query helpers --------------------------------------------------------
  const lc = (v) => (v === null || v === undefined ? '' : String(v)).toLowerCase();
  function haystack(it) {
    return [it.name, it.pack, it.manufacturer, it.instrument, it.kit_name, it.bpm, it.key, it.genre, it.category, (it.tags || []).join(' ')].map(lc).join(' \u0001 ');
  }
  function sampleFilter(q) {
    const words = lc(q.get('q')).split(/\s+/).filter(Boolean);
    const eq = (k, field) => { const v = q.get(k); return v ? (it) => lc(it[field || k]) === lc(v) : null; };
    const fs = [
      eq('pack'), eq('manufacturer'), eq('category'), eq('instrument'), eq('loop_type'), eq('key'), eq('genre'), eq('origin'),
      q.get('kind') ? (it) => q.get('kind').split(',').includes(it.kind) : null,
      q.get('status') ? (it) => it.review_status === q.get('status') : null,
      q.get('kit') ? (it) => (q.get('kit') === 'none' ? !it.kit_id : it.kit_id === q.get('kit')) : null,
      q.get('bpm_min') ? (it) => it.bpm !== null && it.bpm >= +q.get('bpm_min') : null,
      q.get('bpm_max') ? (it) => it.bpm !== null && it.bpm <= +q.get('bpm_max') : null,
      words.length ? (it) => { const h = haystack(it); return words.every((w) => h.includes(w)); } : null,
    ].filter(Boolean);
    return (it) => fs.every((f) => f(it));
  }
  const SORTS = { name: 'name', pack: 'pack', bpm: 'bpm', key: 'key', duration: 'duration_s', size: 'size', status: 'review_status' };
  function sortRows(rows, sort, dir) {
    const f = SORTS[sort]; if (!f) return rows;
    const m = dir === 'desc' ? -1 : 1;
    return rows.slice().sort((a, b) => {
      const x = a[f], y = b[f];
      if (x === y) return a.name < b.name ? -1 : 1;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: 'base' })) * m;
    });
  }
  function row(it) {
    const r = Object.assign({}, it); delete r.tags_raw;
    if (r.bars === undefined) {
      const b = r.loop_type === 'loop' && r.bpm && r.duration_s ? r.duration_s * r.bpm / 240 : 0;
      r.bars = b >= 1 ? Math.round(b) : null; r.on_grid = r.bars ? (r.name.length % 5 !== 0) : null;
    }
    return r;
  }
  /* ---- mix matching (simplified version of samplelib/matching.py) ---- */
  const ROLE = { 'One-Shots/Drums': 'drums', 'Loops/Drum Loops': 'drums', 'One-Shots/Bass & 808s': 'bass', 'Loops/Bass Loops': 'bass', 'One-Shots/Instruments': 'melody', 'Loops/Melody Loops': 'melody', 'One-Shots/FX & Vocals': 'vocal-fx', 'Loops/Vocal & FX Loops': 'vocal-fx' };
  const starters = [];
  function pairScore(a, b) {
    let s = 0, w = 0; const why = [];
    if (a.loop_type === 'loop' && b.loop_type === 'loop') {
      w += 0.45;
      if (a.bpm && b.bpm) {
        if (Math.abs(a.bpm - b.bpm) / a.bpm <= 0.02) { s += 0.45; why.push(`same tempo (${Math.round(b.bpm)} BPM)`); }
        else if (Math.abs(a.bpm - 2 * b.bpm) / a.bpm <= 0.02) { s += 0.36; why.push(`half-time (${Math.round(a.bpm)} vs ${Math.round(b.bpm)} BPM)`); }
        else if (Math.abs(a.bpm - b.bpm / 2) / a.bpm <= 0.02) { s += 0.36; why.push(`double-time (${Math.round(a.bpm)} vs ${Math.round(b.bpm)} BPM)`); }
        else why.push(`tempo clash (${Math.round(a.bpm)} vs ${Math.round(b.bpm)} BPM)`);
      } else { s += 0.18; why.push('tempo unknown'); }
    }
    if (ROLE[a.category] !== 'drums' && ROLE[b.category] !== 'drums') {
      w += 0.4;
      if (a.key && b.key) { if (a.key === b.key) { s += 0.4; why.push('same key'); } else why.push(`keys clash (${a.key} vs ${b.key})`); }
      else { s += 0.16; why.push('key unknown'); }
    }
    let sc = w ? s / w : 0.3;
    if (a.pack && a.pack === b.pack) { sc += 0.03; why.push('same pack'); }
    return [Math.min(1, sc), why];
  }
  function fixtureMatches(src, role, limit) {
    const out = [];
    for (const it of items) {
      const r = ROLE[it.category];
      if (it === src || it.kind !== 'audio' || it.excluded || !r || (role && r !== role) || (src.sha256 && it.sha256 === src.sha256)) continue;
      let [sc, why] = pairScore(src, it);
      if (!role && r === ROLE[src.category]) sc *= 0.85;
      if (sc >= 0.3) out.push({ row: row(it), score: Math.round(sc * 1000) / 1000, reasons: why });
    }
    out.sort((a, b) => b.score - a.score || a.row.name.localeCompare(b.row.name));
    return out.slice(0, limit || 30);
  }
  function fixtureStarter(b) {
    let seed = +b.seed || (1 + Math.floor(Math.random() * 999999));
    const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    const locked = b.locked || {};
    const out = { key: b.key || null, bpm: b.bpm || null, seed: +b.seed || seed, slots: {}, missing: {}, options: {} };
    const avoid = b.avoid || {};
    const loops = (role) => items.filter((i) => i.loop_type === 'loop' && ROLE[i.category] === role && !i.excluded);
    for (const id of Object.values(locked)) { const it = byId.get(id); if (it) { out.key = out.key || it.key; out.bpm = out.bpm || it.bpm; } }
    if (!out.key || !out.bpm) { const mel = loops('melody').filter((i) => i.key && i.bpm); if (mel.length) { const pk = mel[Math.floor(rnd() * mel.length)]; out.key = out.key || pk.key; out.bpm = out.bpm || pk.bpm; } }
    const tgt = { loop_type: 'loop', category: null, key: out.key, bpm: out.bpm };
    for (const slot of ['drums', 'bass', 'melody']) {
      let it = locked[slot] ? byId.get(locked[slot]) : null;
      if (!it) {
        const good = loops(slot).map((i) => [pairScore(tgt, i)[0], i]).filter((x) => x[0] >= 0.75).sort((x, y) => y[0] - x[0]).slice(0, 8);
        out.options[slot] = good.length;
        const fresh = good.filter((x) => x[1].id !== avoid[slot]);
        const pool = fresh.length ? fresh : good;
        it = pool.length ? pool[Math.floor(rnd() * pool.length)][1] : null;
      }
      if (!it) { out.slots[slot] = null; out.missing[slot] = `No ${slot} loops fit ${out.key || 'any key'} at ${out.bpm || 'any'} BPM`; continue; }
      const [sc, why] = pairScore(tgt, it);
      out.slots[slot] = { row: row(it), score: Math.round(sc * 1000) / 1000, reasons: why, locked: !!locked[slot] };
    }
    return out;
  }
  function kitOut(k) {
    const ms = k.member_ids.map((id) => byId.get(id)).filter(Boolean);
    return Object.assign({}, k, { member_count: ms.length, bytes: ms.reduce((s, i) => s + i.size, 0), proposed_root: `${DEST}\\Complete Kits\\${k.manufacturer || 'Unknown Maker'}\\${k.name}` });
  }
  function refreshItem(it) {
    const kit = it.kit_id ? kitById.get(it.kit_id) : null;
    if (it.excluded || (kit && kit.excluded)) it.review_status = 'excluded';
    else if (kit && kit.held) it.review_status = 'held';
    else if (!it.category || it.category === 'Unsorted') it.review_status = 'held';
    else if (it.review_status === 'excluded' || it.review_status === 'held') it.review_status = 'edited';
    it.proposed_dest = proposeDest(it);
  }
  function packsList(q) {
    const m = new Map();
    for (const it of items) {
      if (!it.pack) continue;
      const p = m.get(it.pack) || { name: it.pack, manufacturer: it.manufacturer, files: 0, audio: 0, kits: 0, bytes: 0 };
      p.files++; if (it.kind === 'audio') p.audio++; p.bytes += it.size; m.set(it.pack, p);
    }
    kits.forEach((k) => { const p = m.get(k.pack); if (p && k.member_ids.length) p.kits++; });
    const w = lc(q);
    return [...m.values()].filter((p) => !w || lc(p.name + ' ' + p.manufacturer).includes(w)).sort((a, b) => a.name.localeCompare(b.name));
  }

  const ok = (body) => ({ status: 200, body });
  const err = (status, error) => ({ status, body: { error } });
  function json(o) { return JSON.parse(JSON.stringify(o)); }

  function route(method, url, body) {
    const u = new URL(url, 'http://fixture.local');
    const p = u.pathname, q = u.searchParams;
    body = body || {};
    if (method === 'GET' && p === '/api/state') {
      return ok({ root: st.root, dest_root: st.dest_root, data_dir: st.data_dir, has_inventory: st.has_inventory, progress: st.progress, fixture: true,
        plan: st.plan && { plan_hash: st.plan.plan_hash, created: st.plan.created, stale: st.plan.stale, approved: st.plan.approved, summary: st.plan.summary },
        execution: st.execution });
    }
    if (p === '/api/progress') return ok(st.progress);
    if (p === '/api/scan' && method === 'POST') {
      if (st.progress.running) return err(409, 'A ' + st.progress.job + ' job is already running');
      if (body.root) st.root = body.root;
      runJob('scan', ['walking', 'hashing', 'metadata', 'classifying', 'analyzing', 'kits'], items.length, () => { st.has_inventory = true; markStale(); });
      return ok({ ok: true });
    }
    if (p === '/api/cancel' && method === 'POST') {
      clearInterval(jobTimer); Object.assign(st.progress, { running: false, phase: 'cancelled', message: 'Cancelled by user', job: 'idle' });
      return ok({ ok: true });
    }
    if (p === '/api/packs') return ok(packsList(q.get('q')));
    if (p === '/api/facets') {
      const f = (k) => [...new Set(items.map((i) => i[k]).filter((v) => v !== null && v !== undefined && v !== ''))].sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }));
      return ok({ categories: f('category'), instruments: f('instrument'), keys: f('key'), genres: f('genre'), manufacturers: f('manufacturer'), packs: f('pack'), loop_types: f('loop_type') });
    }
    if (p === '/api/samples') {
      const rows = sortRows(items.filter(sampleFilter(q)), q.get('sort') || 'name', q.get('dir') || 'asc');
      const off = +(q.get('offset') || 0), lim = +(q.get('limit') || 100);
      return ok({ total: rows.length, offset: off, items: rows.slice(off, off + lim).map(row) });
    }
    let m = p.match(/^\/api\/samples\/([^/]+)$/);
    if (m) {
      const it = byId.get(m[1]); if (!it) return err(404, 'No sample with id ' + m[1]);
      const meta = {}; for (const k of Object.keys(it.prov)) meta[k] = { value: it[k], source: it.prov[k].source, confidence: it.prov[k].confidence };
      return ok(Object.assign(row(it), { meta, tags_raw: it.tags_raw, audio: it.kind === 'audio' ? { format: it.ext.slice(1), sample_rate: it.sample_rate, channels: it.channels, bits: it.bits, duration_s: it.duration_s } : null }));
    }
    m = p.match(/^\/api\/samples\/([^/]+)\/matches$/);
    if (m && method === 'GET') {
      const src = byId.get(m[1]); if (!src) return err(404, 'No sample with id ' + m[1]);
      return ok({ items: fixtureMatches(src, q.get('role'), +(q.get('limit') || 30)) });
    }
    if (p === '/api/starter' && method === 'POST') return ok(fixtureStarter(body));
    if (p === '/api/starters' && method === 'GET') return ok({ items: starters.map((s) => Object.assign({}, s, { rows: s.ids.map((i) => byId.get(i)).filter(Boolean).map(row) })) });
    if (p === '/api/starters' && method === 'POST') {
      const s = { id: randHex(12), name: body.name || 'Untitled', key: body.key || null, bpm: body.bpm || null, ids: body.ids || [], created: new Date().toISOString() };
      starters.push(s); return ok({ ok: true, starter: s });
    }
    m = p.match(/^\/api\/starters\/([^/]+)$/);
    if (m && method === 'DELETE') {
      const i = starters.findIndex((s) => s.id === m[1]); if (i < 0) return err(404, 'Saved set not found');
      starters.splice(i, 1); return ok({ ok: true });
    }
    if (p.startsWith('/api/audio/')) return err(404, 'Audio unavailable in fixture mode');
    if (p === '/api/kits' && method === 'GET') {
      const w = lc(q.get('q'));
      return ok(kits.filter((k) => k.member_ids.length && (!q.get('pack') || k.pack === q.get('pack')) && (!w || lc(k.name + ' ' + k.pack + ' ' + k.manufacturer).includes(w))).map(kitOut));
    }
    m = p.match(/^\/api\/kits\/([^/]+)$/);
    if (m && method === 'GET' && m[1] !== 'edit' && m[1] !== 'create') {
      const k = kitById.get(m[1]); if (!k) return err(404, 'No kit with id ' + m[1]);
      return ok(Object.assign(kitOut(k), { members: k.member_ids.map((id) => row(byId.get(id))) }));
    }
    if (p === '/api/items/edit' && method === 'POST') {
      const ids = body.ids || [], ch = body.changes || {};
      const out = [];
      for (const id of ids) {
        const it = byId.get(id); if (!it) continue;
        for (const [k, v] of Object.entries(ch)) {
          if (k === 'excluded') { it.excluded = !!v; continue; }
          it[k] = v === '' ? null : v;
          it.prov[k] = { source: 'manual', confidence: null };
        }
        it.review_status = 'edited'; refreshItem(it); out.push(row(it));
      }
      markStale();
      return ok({ ok: true, updated: out.length, items: out });
    }
    if (p === '/api/kits/edit' && method === 'POST') {
      const k = kitById.get(body.kit_id); if (!k) return err(404, 'No kit with id ' + body.kit_id);
      const a = body.action;
      if (a === 'hold') { k.held = true; k.hold_reason = 'Held by you'; }
      else if (a === 'release') { k.held = false; k.hold_reason = null; }
      else if (a === 'exclude') k.excluded = true;
      else if (a === 'include') k.excluded = false;
      else if (a === 'rename') { if (!body.name || !body.name.trim()) return err(400, 'Kit name cannot be empty'); k.name = body.name.trim(); k.member_ids.forEach((id) => { byId.get(id).kit_name = k.name; }); }
      else if (a === 'remove_members' || a === 'dissolve') {
        const rm = a === 'dissolve' ? k.member_ids.slice() : (body.ids || []);
        rm.forEach((id) => { const it = byId.get(id); if (it) { it.kit_id = null; it.kit_name = null; refreshItem(it); } });
        k.member_ids = k.member_ids.filter((id) => !rm.includes(id));
      } else if (a === 'add_members') {
        (body.ids || []).forEach((id) => { const it = byId.get(id); if (it && !k.member_ids.includes(id)) { it.kit_id = k.id; it.kit_name = k.name; k.member_ids.push(id); } });
      } else return err(400, 'Unknown kit action ' + a);
      k.manual = true;
      k.member_ids.forEach((id) => refreshItem(byId.get(id)));
      markStale();
      return ok({ ok: true, kit: kitOut(k) });
    }
    if (p === '/api/kits/create' && method === 'POST') {
      const ids = (body.ids || []).filter((id) => byId.has(id));
      if (!body.name || !ids.length) return err(400, 'A kit needs a name and at least one sample');
      const first = byId.get(ids[0]);
      const k = { id: 'k' + randHex(16), name: body.name, manufacturer: first.manufacturer, pack: first.pack, root_rel: first.rel_path.split('/').slice(0, -1).join('/'), member_ids: [], bpm: first.bpm, key: first.key, evidence: ['Created manually from ' + ids.length + ' selected samples'], confidence: 1, held: false, hold_reason: null, excluded: false, manual: true, ref_checks: [{ check: 'project-sample-refs', performed: false, result: 'not-checked', detail: 'Manual kit: no automatic checks were run' }] };
      kits.push(k); kitById.set(k.id, k);
      ids.forEach((id) => { const it = byId.get(id); const old = it.kit_id && kitById.get(it.kit_id); if (old) old.member_ids = old.member_ids.filter((x) => x !== id); it.kit_id = k.id; it.kit_name = k.name; k.member_ids.push(id); refreshItem(it); });
      markStale();
      return ok({ ok: true, kit: kitOut(k) });
    }
    if (p === '/api/plan/build' && method === 'POST') {
      if (st.progress.running) return err(409, 'A ' + st.progress.job + ' job is already running');
      runJob('plan', ['grouping duplicates', 'resolving kits', 'checking collisions'], 600, buildPlan);
      return ok({ ok: true });
    }
    if (p === '/api/plan' && method === 'GET') {
      if (!st.plan) return err(404, 'No plan has been built yet');
      const w = lc(q.get('q'));
      let ops = st.ops.filter((o) => (!q.get('action') || o.action === q.get('action')) && (!q.get('flag') || o.flags.includes(q.get('flag'))) && (!w || lc(o.name + ' ' + o.pack + ' ' + o.kit_name + ' ' + o.rel_path + ' ' + o.dest).includes(w)));
      const g = q.get('group');
      const gk = (o) => g === 'pack' ? o.pack || '' : g === 'kit' ? o.kit_name || '\uffff' : g === 'category' ? o.category || '' : g === 'action' ? o.action : g === 'warning' ? (o.flags[0] || '\uffff') : '';
      if (g) ops = ops.slice().sort((a, b) => gk(a).localeCompare(gk(b)) || a.name.localeCompare(b.name));
      const off = +(q.get('offset') || 0), lim = +(q.get('limit') || 100);
      return ok({ plan_hash: st.plan.plan_hash, stale: st.plan.stale, approved: st.plan.approved, summary: st.plan.summary, total: ops.length, ops: ops.slice(off, off + lim), items: ops.slice(off, off + lim) });
    }
    if (p === '/api/plan/approve' && method === 'POST') {
      if (!st.plan) return err(409, 'No plan to approve');
      if (st.plan.stale) return err(409, 'The plan is stale: edits were made after it was built. Rebuild it and review again.');
      if (body.plan_hash !== st.plan.plan_hash) return err(409, 'Plan hash does not match the current plan. It was rebuilt since you reviewed it.');
      st.plan.approved = true;
      return ok({ ok: true });
    }
    if (p === '/api/execute' && method === 'POST') {
      if (!st.plan || !st.plan.approved || st.plan.stale) return err(409, 'This plan is not approved. Approve the current plan before running it.');
      if (body.plan_hash !== st.plan.plan_hash) return err(409, 'Plan hash does not match the approved plan.');
      runJob('execute', ['moving', 'linking', 'verifying', 'writing reports'], st.ops.length, () => { setExecution(makeResults(st.ops, 1)); });
      return ok({ ok: true });
    }
    if (p === '/api/log') {
      const w = lc(q.get('q'));
      const rs = st.results.filter((r) => (!q.get('status') || r.status === q.get('status')) && (!w || lc(r.name + ' ' + r.source + ' ' + r.dest + ' ' + r.error).includes(w)));
      const off = +(q.get('offset') || 0), lim = +(q.get('limit') || 100);
      return ok({ total: rs.length, items: rs.slice(off, off + lim) });
    }
    if (p === '/api/reveal' && method === 'POST') return ok({ ok: true, fixture: true });
    for (const ext of (window.FixtureExt || [])) { const r = ext(method, p, q, body, st, { ok, err, items }); if (r) return r; }
    return err(404, 'Fixture has no route for ' + method + ' ' + p);
  }

  window.FixtureAPI = {
    handle(method, url, body) {
      return new Promise((resolve) => {
        setTimeout(() => {
          let r;
          try { r = route(method, url, body); } catch (e) { r = err(500, 'Fixture error: ' + e.message); }
          resolve({ status: r.status, body: json(r.body) });
        }, 25 + Math.random() * 60);
      });
    },
    count: () => items.length,
  };
})();
