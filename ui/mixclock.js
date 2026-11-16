/* mixclock.js: timing math for Beat Starter "Play all". Pure functions, no DOM,
   so tests can run them in Node (require) as well as in the page.

   Every loop runs on one shared clock at the set tempo:
   - playbackRate = setBpm / loopBpm (after picking half/double time), so a
     79.3 BPM loop plays at 79 BPM. With the 0.5% matching rule the pitch
     change is under 9 cents, too small to hear as out of tune.
   - one cycle = the longest loop at the set tempo; at each cycle boundary
     every loop restarts together, so no drift can build up.
   - gains even out measured loudness (RMS) so no loop drowns the others. */
'use strict';

const MixClock = {
  /* Half/double factor that brings loopBpm closest to setBpm. */
  factor(setBpm, loopBpm) {
    let best = 1, err = Infinity;
    for (const m of [0.5, 1, 2]) {
      const e = Math.abs(setBpm - loopBpm * m);
      if (e < err) { err = e; best = m; }
    }
    return best;
  },

  /* loops: [{ bpm, bars, duration }] (duration = buffer seconds).
     -> { cycle, loops: [{ rate, loopEnd, period }] } where loopEnd is in buffer
     seconds and period is real seconds per repeat at the set tempo. */
  plan(loops, setBpm) {
    const out = loops.map((l) => {
      const bpm = l.bpm || setBpm;
      const m = setBpm && l.bpm ? MixClock.factor(setBpm, l.bpm) : 1;
      const rate = setBpm && l.bpm ? setBpm / (l.bpm * m) : 1;
      let loopEnd = l.duration;
      if (l.bars && bpm) loopEnd = Math.min(l.duration, (l.bars * 240) / bpm);
      return { rate, loopEnd, period: loopEnd / rate };
    });
    const cycle = Math.max(...out.map((o) => o.period), 0);
    return { cycle, loops: out };
  },

  rms(channels) {
    let sum = 0, n = 0;
    for (const ch of channels) {
      for (let k = 0; k < ch.length; k += 4) { sum += ch[k] * ch[k]; n += 1; }
    }
    return n ? Math.sqrt(sum / n) : 0;
  },

  /* rmsList -> gains that bring each loop to the same loudness, scaled so the
     sum of three loops does not clip. Silent loops get gain 1. */
  gains(rmsList) {
    const live = rmsList.filter((r) => r > 1e-4);
    if (!live.length) return rmsList.map(() => 1);
    const target = Math.exp(live.reduce((s, r) => s + Math.log(r), 0) / live.length);
    const head = 0.9 / Math.sqrt(Math.max(1, rmsList.length));
    return rmsList.map((r) => (r > 1e-4 ? Math.min(4, Math.max(0.25, target / r)) * head : head));
  },
};

if (typeof module !== 'undefined' && module.exports) module.exports = MixClock;
