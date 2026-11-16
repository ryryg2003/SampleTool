// Full producer flow through the real UI, against a DISPOSABLE fixture it creates itself.
// Scan -> find pack -> search + filter -> audition -> correct category -> rebuild -> approve -> run
// -> verify the file moved and the Move Log shows it.
// Usage: node tests/ui_flow.js [shotDir]      (needs python3/py on PATH; never touches real folders)
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');

const REPO = path.resolve(__dirname, '..');
const PY = process.platform === 'win32' ? 'python' : 'python3';
const shotDir = process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'slo-flow-shots-'));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'slo-flow-'));
const root = path.join(tmp, 'Sample Packs');
const PORT = 8800 + Math.floor(Math.random() * 100);
let failures = 0;
const check = (name, ok, detail) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? '  — ' + detail : ''}`); if (!ok) failures++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = (p) => new Promise((res, rej) => http.get(`http://127.0.0.1:${PORT}${p}`, (r) => { let b = ''; r.on('data', (c) => (b += c)); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej));
async function waitIdle(timeout = 60000) {
  const t0 = Date.now();
  await sleep(300);
  while (Date.now() - t0 < timeout) { const p = await api('/api/progress'); if (!p.running) return p; await sleep(150); }
  throw new Error('job timeout');
}

(async () => {
  execFileSync(PY, [path.join(REPO, 'tools', 'make_fixtures.py'), root], { stdio: 'ignore' });
  const srv = spawn(PY, ['-m', 'samplelib', '--root', root, '--data-dir', path.join(tmp, 'data'), '--port', String(PORT), '--no-browser'], { cwd: REPO, stdio: 'ignore' });
  for (let i = 0; i < 50; i++) { try { await api('/api/state'); break; } catch { await sleep(200); } }
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  const shot = (n) => page.screenshot({ path: path.join(shotDir, n + '.png') });
  try {
    await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'load' });
    // 1. Scan
    await page.click('.rail-btn[data-view="scan"]');
    await page.click('#scan-start');
    await waitIdle();
    await sleep(800);
    await shot('flow-01-scan');
    check('scan summary shown', await page.isVisible('#scan-summary'));
    const st = await api('/api/state');
    check('scan found files', st.counts.files > 30, JSON.stringify(st.counts));

    // 2. Find a pack by partial maker name
    await page.click('.rail-btn[data-view="library"]');
    await page.fill('#pack-q', 'nightf');
    await sleep(400);
    const packs = await page.$$eval('#pack-list .pk', (b) => b.map((x) => x.dataset.pack));
    check('pack search by partial maker', packs.length === 1 && packs[0].startsWith('Nightfall'), packs.join('|'));
    await page.click('#pack-list .pk');
    await sleep(600);

    // 3. Search + filter
    await page.fill('#lib-q', 'hat');
    await sleep(500);
    await page.selectOption('#lib-filters [data-f="category"]', 'One-Shots/Drums');
    await sleep(600);
    const names = await page.$$eval('#lib-table .vt-row:not(.is-ph)', (r) => r.map((x) => x.textContent));
    check('search + filter narrows to hat', names.length >= 1 && names.every((t) => /hat/i.test(t)), names.join(' / ').slice(0, 200));
    await shot('flow-02-filtered');

    // 4. Audition with the keyboard
    await page.locator('#lib-table .vt-row:not(.is-ph)').filter({ hasText: 'Closed Hat 03' }).first().click();
    await page.keyboard.press('Space');
    await sleep(700);
    const audio = await page.evaluate(() => { const a = document.getElementById('audio'); return { src: a.currentSrc, paused: a.paused, t: a.currentTime }; });
    check('audition loads the sample', /\/api\/audio\//.test(audio.src), JSON.stringify(audio));
    await shot('flow-03-audition');
    await page.keyboard.press('Space');

    // >>> media checks (owned by the media feature; insert between these lines)
    {
      // AIFF auditions in place (converted to WAV by the server), waveform, seek, retrigger
      const rowSel = '#lib-table .vt-row:not(.is-ph)';
      await page.click('#lib-clear');
      await page.fill('#lib-q', 'Bass Hit G');
      await sleep(700);
      await page.locator(rowSel).filter({ hasText: 'Bass Hit G.aif' }).first().click();
      await page.keyboard.press('l'); // loop on: the sample is only 0.6 s long
      await sleep(900);
      const a1 = await page.evaluate(() => { const a = document.getElementById('audio'); return { src: a.currentSrc, err: a.error && a.error.code, paused: a.paused, dur: a.duration, ready: a.readyState }; });
      check('AIFF row is previewable', (await api('/api/samples?q=Bass%20Hit%20G')).items[0].preview === 'native');
      check('AIFF audio element loads without error', /\/api\/audio\//.test(a1.src) && !a1.err && a1.ready >= 1 && a1.dur > 0.5 && a1.dur < 0.7, JSON.stringify(a1));
      const adv = await page.evaluate(() => new Promise((res) => {
        const a = document.getElementById('audio'); const ts = [];
        const iv = setInterval(() => { ts.push(a.currentTime); if (ts.length === 6) { clearInterval(iv); res(ts); } }, 70);
      }));
      check('AIFF playback advances', adv.some((t, i) => i && t > adv[i - 1]), adv.map((t) => t.toFixed(3)).join(','));
      await sleep(300);
      const wave = await page.evaluate(() => ({ layers: !!Wave.layers, ink: Wave.inkCount(), w: Wave.cv.width, ph: document.getElementById('tp-wave').classList.contains('is-empty') }));
      check('waveform canvas is drawn', wave.layers && !wave.ph && wave.ink > wave.w * 3, JSON.stringify(wave));
      await page.evaluate(() => document.getElementById('audio').pause());
      const box = await page.locator('#tp-wave').boundingBox();
      await page.mouse.click(box.x + box.width * 0.75, box.y + box.height / 2);
      const t3 = await page.evaluate(() => document.getElementById('audio').currentTime);
      check('clicking the waveform seeks', t3 > 0.38 && t3 < 0.6, String(t3));
      await sleep(250);
      await shot('flow-03b-waveform');
      // retrigger: an arrow key restarts the (same) row from 0 even at the end of the list
      await page.locator('#lib-table .vt-body').focus();
      await page.keyboard.press('o');
      await page.evaluate(() => { const a = document.getElementById('audio'); a.currentTime = 0.4; });
      await page.keyboard.press('ArrowDown');
      const rt = await page.evaluate(() => ({ t: document.getElementById('audio').currentTime, paused: document.getElementById('audio').paused }));
      check('retrigger restarts from the top on arrow keys', rt.t < 0.2 && !rt.paused, JSON.stringify(rt));
      await page.keyboard.press('o');
      await page.keyboard.press('l');
      await page.evaluate(() => Player.stop());
      // restore the selection the next step edits
      await page.fill('#lib-q', 'Closed Hat 03');
      await sleep(700);
      await page.locator(rowSel).filter({ hasText: 'Closed Hat 03' }).first().click();
      await sleep(300);
      await page.evaluate(() => Player.stop());
    }
    // <<< media checks
    //
    //
    // >>> collections checks (owned by the collections feature; insert between these lines)
    // <<< collections checks
    //
    //

    // 5. Correct its category with the bulk editor
    await page.click('#sel-edit').catch(async () => { await page.keyboard.press('e'); });
    await page.waitForSelector('#bulk-dlg[open]', { timeout: 5000 });
    await page.selectOption('#bulk-grid [data-v="category"]', 'One-Shots/FX & Vocals');
    await page.check('#bulk-grid [data-set="category"]');
    await shot('flow-04-edit');
    await page.click('#bulk-go');
    await sleep(800);
    const row = (await api('/api/samples?q=closed%20hat%2003')).items[0];
    check('edit recorded as manual', row && row.category === 'One-Shots/FX & Vocals' && row.prov.category.source === 'manual', JSON.stringify(row && row.prov.category));
    check('proposed destination updated', row && /FX & Vocals/.test(row.proposed_dest || ''), row && row.proposed_dest);

    // 6. Review: rebuild, approve, run
    await page.click('.rail-btn[data-view="review"]');
    await sleep(500);
    if (await page.isVisible('#stale-rebuild')) await page.click('#stale-rebuild'); else await page.click('#plan-build');
    await waitIdle();
    await sleep(800);
    await page.click('#plan-approve-open');
    await page.waitForSelector('#approve-dlg[open]');
    const goDisabled = await page.isDisabled('#approve-go');
    check('approve needs the review checkbox', goDisabled);
    await page.check('#approve-check');
    await shot('flow-05-approve');
    await page.click('#approve-go');
    await sleep(600);
    check('plan approved', (await api('/api/state')).plan.approved);
    await page.click('#plan-run');
    await page.waitForSelector('#run-dlg[open]');
    await page.click('#run-go');
    await waitIdle();
    await sleep(1000);

    // 7. Verify on disk and in the Move Log
    const dest = path.join(root, 'Samples', 'One-Shots', 'FX & Vocals', 'Closed Hat 03.wav');
    const src = path.join(root, 'Nightfall Audio - Midnight Trap', 'One Shots', 'Hats', 'Closed Hat 03.wav');
    check('file moved to corrected category', fs.existsSync(dest), dest);
    check('source removed after verified move', !fs.existsSync(src));
    for (const r of ['Sample_Catalog.csv', 'Source_Path_Index.csv', 'Duplicate_Report.csv', 'Organization_Report.csv'])
      check('report ' + r, fs.existsSync(path.join(root, 'Samples', 'Catalog', r)));
    await page.click('.rail-btn[data-view="log"]');
    await sleep(500);
    await page.fill('#log-q', 'Closed Hat 03');
    await sleep(600);
    const logRows = await page.$$eval('#log-table .vt-row:not(.is-ph)', (r) => r.map((x) => x.textContent));
    check('move log lists the move as done', logRows.some((t) => /Closed Hat 03/.test(t) && /done/i.test(t)), logRows.join(' / ').slice(0, 300));
    await page.fill('#log-q', '');
    await sleep(500);
    await shot('flow-06-log');
    check('verification reconciles', (await api('/api/state')).execution.verification.ok === true);
    // >>> undo checks (owned by the undo feature; insert between these lines)
    // <<< undo checks
    //
    //
    // >>> settings checks (owned by the settings feature; insert between these lines)
    // <<< settings checks
    //
    //
  } catch (e) {
    check('flow ran to completion', false, e.message);
    await shot('flow-error').catch(() => {});
  } finally {
    check('no console errors', errors.length === 0, errors.slice(0, 3).join(' | '));
    await browser.close();
    srv.kill();
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {}
    console.log(`\nscreenshots: ${shotDir}\n${failures ? failures + ' FAILED' : 'ALL PASSED'}`);
    process.exit(failures ? 1 : 0);
  }
})();
