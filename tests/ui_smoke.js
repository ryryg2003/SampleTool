#!/usr/bin/env node
/* UI smoke test for Sample Library Organizer.
 *
 *   node tests/ui_smoke.js <baseUrl> [screenshotDir]
 *
 * Examples
 *   python3 -m http.server 8765 -d ui &   node tests/ui_smoke.js "http://127.0.0.1:8765/?fixture=1"
 *   node tests/ui_smoke.js http://127.0.0.1:8123/            (real server)
 *
 * In fixture mode (URL has ?fixture=1, or the app falls back because /api/state is
 * unreachable) the full flow runs: bulk edit, kit edits, approve and run.
 * Against a real server only read-only interactions run: search, filters, sort,
 * keyboard, selection, opening (and cancelling) the edit and approval dialogs,
 * Review and Move Log browsing. Nothing is edited, approved or executed.
 * Exits non-zero on any failure or console error.
 */
'use strict';
const { chromium } = require('playwright');

const base = process.argv[2];
const shotDir = process.argv[3] || '';
if (!base) { console.error('usage: node tests/ui_smoke.js <baseUrl> [screenshotDir]'); process.exit(2); }

const results = [];
let failed = 0;
function check(name, cond, detail) {
  results.push(`${cond ? 'PASS' : 'FAIL'}  ${name}${!cond && detail ? '  — ' + detail : ''}`);
  if (!cond) failed++;
}

function url(hash) {
  const u = new URL(base);
  if (!/\.html$/.test(u.pathname) && !u.pathname.endsWith('/')) u.pathname += '/';
  u.hash = hash;
  return u.toString();
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error' && !/api\/audio|favicon/.test(m.text())) consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => d.accept());
  const shot = async (n) => { if (shotDir) await page.screenshot({ path: `${shotDir}/${n}.png` }); };
  const rows = '#lib-table .vt-row:not(.is-ph)';
  const countOf = async (sel) => parseInt(((await page.textContent(sel)) || '0').replace(/[^\d]/g, ''), 10) || 0;
  const settle = (ms) => page.waitForTimeout(ms || 350);

  try {
    // ---------- Library ----------
    await page.goto(url('library'));
    await page.waitForSelector(rows, { timeout: 15000 });
    const fixture = await page.evaluate(() => document.body.classList.contains('is-fixture'));
    console.log(`mode: ${fixture ? 'fixture' : 'real server'}`);
    await settle();
    const total0 = await countOf('#lib-count');
    check('library loads rows', total0 > 0, `count=${total0}`);
    check('no horizontal page overflow (library)', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    check('nav always visible', (await page.locator('.rail-btn').count()) === 6);
    await shot('01-library');

    // search (debounced)
    await page.press('body', '/');
    check('"/" focuses search', await page.evaluate(() => document.activeElement.id === 'lib-q'));
    const firstName = (await page.textContent(`${rows} .nm`)).trim();
    const term = fixture ? 'kick' : firstName.replace(/\.[^.]+$/, '').slice(0, 6);
    await page.fill('#lib-q', term);
    await settle(500);
    const totalQ = await countOf('#lib-count');
    check('search narrows results', totalQ > 0 && totalQ <= total0, `q=${term} ${totalQ}/${total0}`);
    check('search chip shown', (await page.locator('#lib-chips .chip').count()) >= 1);
    await page.press('#lib-q', 'Escape'); // clears field natively
    await page.fill('#lib-q', '');
    await settle(400);

    // filter
    const opt = await page.$$eval('#lib-filters [data-f="category"] option', (os) => os.map((o) => o.value).filter(Boolean));
    if (opt.length) {
      const want = opt.includes('One-Shots/Drums') ? 'One-Shots/Drums' : opt[0];
      await page.selectOption('#lib-filters [data-f="category"]', want);
      await settle(500);
      const n = await countOf('#lib-count');
      check('category filter applies', n > 0 && n <= total0, `${want}: ${n}`);
      await page.fill('#lib-filters [data-f="bpm_min"]', '1');
      await settle(600);
      check('combined filter chips', (await page.locator('#lib-chips .chip').count()) === 2);
      await page.click('#lib-clear');
      await settle(500);
      check('clear all restores full library', (await countOf('#lib-count')) === total0 && (await page.locator('#lib-chips .chip').count()) === 0);
    } else check('category facets present', false);

    // sort
    await page.click('#lib-table .vt-sort[data-sort="bpm"]');
    await settle(400);
    await page.click('#lib-table .vt-sort[data-sort="bpm"]');
    await settle(500);
    const sortAttr = await page.getAttribute('#lib-table [role=columnheader]:has([data-sort="bpm"])', 'aria-sort');
    check('sort header shows direction', sortAttr === 'descending', sortAttr);
    const bpms = (await page.$$eval(rows, (rs) => rs.slice(0, 10).map((r) => parseFloat(r.children[4].textContent)))).filter((x) => !isNaN(x));
    check('BPM sorted descending', bpms.length > 1 && bpms.every((b, i) => i === 0 || b <= bpms[i - 1]), bpms.join(','));
    await page.click('#lib-table .vt-sort[data-sort="name"]');
    await settle(400);

    // keyboard navigation + inspector + player
    await page.focus('#lib-table .vt-body');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await settle(400);
    const cur = await page.$eval('#lib-table .vt-row.cursor', (r) => r.dataset.i).catch(() => null);
    check('arrow keys move cursor', cur === '2', `cursor=${cur}`);
    const curName = await page.$eval('#lib-table .vt-row.cursor .nm', (e) => e.textContent).catch(() => '');
    const insName = await page.textContent('#inspector .ins-name').catch(() => '');
    check('inspector follows selection', curName && insName === curName, `${curName} vs ${insName}`);
    check('provenance badges shown', (await page.locator('#inspector .prov').count()) >= 4);
    await page.keyboard.press('Space');
    await settle(300);
    const tpMsg = (await page.textContent('#tp-msg')).trim();
    const playing = await page.evaluate(() => !document.getElementById('audio').paused);
    check('Space auditions (plays or explains why not)', playing || tpMsg.length > 0, tpMsg);
    if (playing) { await page.keyboard.press('Space'); await settle(200); }
    await page.keyboard.press('l');
    check('L toggles loop', (await page.getAttribute('#tp-loop', 'aria-pressed')) === 'true');
    await page.keyboard.press('l');
    // unsupported preview message
    const np = page.locator(`${rows}:has(.ext-np)`).first();
    if (await np.count()) {
      await np.click();
      await settle(300);
      check('unsupported format explained', /Preview not supported in browser for \.\w+/.test(await page.textContent('#tp-msg')));
    }

    // multi-select
    const r = page.locator(rows);
    await r.nth(0).click();
    await r.nth(2).click({ modifiers: ['Control'] });
    await r.nth(4).click({ modifiers: ['Shift'] });
    await settle(200);
    const selN = await countOf('#sel-n');
    check('ctrl/shift click multi-select', selN === 3, `selected=${selN}`);
    await page.focus('#lib-table .vt-body');
    await page.keyboard.press('Control+a');
    await page.waitForFunction((n) => parseInt((document.getElementById('sel-n').textContent || '0').replace(/[^\d]/g, ''), 10) === n, Math.min(total0, 50000), { timeout: 15000 }).catch(() => {});
    check('Ctrl+A selects all results', (await countOf('#sel-n')) === Math.min(total0, 50000), `${await countOf('#sel-n')} vs ${total0}`);
    await page.keyboard.press('Escape');
    await settle(200);
    check('Esc clears selection', await page.isHidden('#selbar'));

    // bulk edit
    await r.nth(1).click();
    await r.nth(3).click({ modifiers: ['Control'] });
    await page.keyboard.press('e');
    await page.waitForSelector('#bulk-dlg[open]');
    check('bulk edit dialog opens', true);
    await shot('02-bulk-edit');
    if (fixture) {
      await page.fill('#bulk-grid [data-v="genre"]', 'Smoke Test Genre');
      await page.click('#bulk-go');
      await settle(800);
      check('bulk edit applied', !(await page.isVisible('#bulk-dlg[open]')));
      const genres = await page.evaluate(async () => Promise.all([...Library.sel.keys()].map(async (id) => (await API.get('/api/samples/' + id)).genre)));
      check('bulk edit persisted on both rows', genres.length === 2 && genres.every((g) => g === 'Smoke Test Genre'), genres.join(','));
      await r.nth(1).click();
      await settle(500);
      check('inspector shows manual provenance', (await page.locator('#inspector .prov-manual').count()) >= 1);
    } else {
      await page.keyboard.press('Escape');
      await settle(200);
    }

    // pack sidebar + kits tab
    await page.fill('#pack-q', '');
    const packBtn = page.locator('#pack-list .pk:not(.pk-all)').first();
    await packBtn.click();
    await settle(600);
    check('pack click shows pack title', (await page.textContent('#lib-h')) !== 'Full library');
    if (!(await page.isHidden('#lib-tabs'))) {
      await page.click('#ltab-kits');
      await settle(400);
      const k = page.locator('#lib-kits summary').first();
      if (await k.count()) { await k.click(); await settle(500); check('kit group expands with members', (await page.locator('#lib-kits .kgm').count()) > 0); }
      await shot('03-pack-kits');
      await page.click('#ltab-sounds');
    }
    await page.fill('#pack-q', 'zzzz-no-such-pack');
    await settle(400);
    check('pack search empty state', /No pack or maker/.test(await page.textContent('#pack-list')));
    await page.fill('#pack-q', '');
    await page.click('#lib-chips .chip-x').catch(() => {});

    // ---------- Review ----------
    await page.click('.rail-btn[data-view="review"]');
    await settle(800);
    if (fixture && await page.isVisible('#stale-banner')) {
      check('stale banner after edit', true);
      await shot('04-review-stale');
      await page.click('#stale-rebuild');
      await page.waitForFunction(() => !document.getElementById('plan-approve-open').disabled, null, { timeout: 15000 });
      check('rebuild clears stale', await page.isHidden('#stale-banner'));
    }
    const opRows = '#op-table .vt-row:not(.is-ph)';
    const hasPlan = await page.evaluate(() => !!(App.state && App.state.plan));
    if (hasPlan) {
      await settle(1000); // let a just-finished rebuild reload the table
      await page.waitForSelector(opRows, { timeout: 10000 });
      check('op table renders rows', (await page.locator(opRows).count()) > 0);
      await page.click('#op-group [data-g="action"]');
      await settle(500);
      // Filter on an action the current plan really contains (prefer hold, then keep).
      const byAction = await page.evaluate(() => (App.state.plan && App.state.plan.summary && App.state.plan.summary.by_action) || {});
      const pick = ['hold', 'keep', 'link', 'move'].find((a) => byAction[a] > 0) || 'move';
      await page.selectOption('#op-action', pick);
      await settle(500);
      const acts = await page.$$eval(`${opRows} .act`, (as) => as.map((a) => a.textContent.trim().toLowerCase()));
      check('action filter', acts.length > 0 && acts.every((a) => a.startsWith(pick)), pick + ': ' + acts.slice(0, 5).join(','));
      await page.locator(opRows).first().click();
      await settle(200);
      check('op detail opens', await page.isVisible('#op-detail'));
      await page.selectOption('#op-action', '');
      await settle(400);
      await shot('05-review');
      await page.click('#rtab-kits');
      await settle(700);
      check('kit panel shows ref checks', (await page.locator('#kit-detail .chk').count()) > 0 || (await page.locator('#kit-list .side-msg').count()) > 0);
      if (fixture && (await page.locator('#kit-detail [data-ka="hold"]').count())) {
        await page.click('#kit-detail [data-ka="hold"]');
        await settle(600);
        check('kit hold marks plan stale', await page.isVisible('#stale-banner'));
        await page.click('#kit-detail [data-ka="release"]');
        await settle(500);
        await page.click('#stale-rebuild');
        await page.waitForFunction(() => !document.getElementById('plan-approve-open').disabled, null, { timeout: 15000 });
      }
      await shot('06-review-kits');
      await page.click('#rtab-ops');

      // approval flow
      if (!(await page.isDisabled('#plan-approve-open'))) {
        await page.click('#plan-approve-open');
        await page.waitForSelector('#approve-dlg[open]');
        check('approve button gated by checkbox', await page.isDisabled('#approve-go'));
        await page.check('#approve-check');
        check('checkbox enables approve', !(await page.isDisabled('#approve-go')));
        const label = await page.textContent('#approve-go');
        check('approve button names hash8', /Approve plan [0-9a-f]{8}$/.test(label.trim()), label);
        await shot('07-approve');
        if (fixture) {
          await page.click('#approve-go');
          await settle(600);
          check('plan approved', !(await page.isDisabled('#plan-run')));
          await page.click('#plan-run');
          await page.waitForSelector('#run-dlg[open]');
          await page.click('#run-go');
          await page.waitForFunction(() => location.hash === '#log', null, { timeout: 20000 });
          check('run approved plan lands on Move Log', true);
        } else {
          await page.keyboard.press('Escape');
        }
      }
    } else {
      check('no-plan empty state', /No plan yet/.test(await page.textContent('#op-table')));
    }

    // ---------- Move Log ----------
    await page.click('.rail-btn[data-view="log"]');
    await settle(800);
    const logRows = '#log-table .vt-row:not(.is-ph)';
    const hasExec = await page.evaluate(() => !!(App.state && App.state.execution));
    if (hasExec) {
      await page.waitForSelector(logRows, { timeout: 10000 });
      check('log rows render', (await page.locator(logRows).count()) > 0);
      check('reports listed with paths', (await page.locator('#reports .replist li').count()) > 0);
      check('verification summary shown', /storage avoided/.test(await page.textContent('#verify')));
      await page.click('#log-status [data-s="held"]');
      await settle(500);
      const st = await page.$$eval(`${logRows} .rs`, (as) => as.map((a) => a.textContent));
      check('log status filter', st.every((s) => s === 'Held'), st.slice(0, 4).join(','));
      await page.click('#log-status [data-s=""]');
      await settle(300);
    } else check('log empty state', /Nothing has run yet/.test(await page.textContent('#log-table')));
    await shot('08-log');

    // ---------- Scan ----------
    await page.click('.rail-btn[data-view="scan"]');
    await settle(600);
    check('scan root prefilled', (await page.inputValue('#scan-root')).length > 0);
    check('read-only promise visible', /never moves, renames/.test(await page.textContent('.scan-promise')));
    if (fixture) {
      await page.click('#scan-start');
      await page.waitForSelector('#scan-progress:not([hidden])');
      await settle(700);
      await shot('09-scan-progress');
      check('scan progress shows counts', /[\d,]+ \/ [\d,]+ files/.test(await page.textContent('#sp-count')));
      await page.waitForFunction(() => !document.getElementById('scan-start').disabled, null, { timeout: 20000 });
      await settle(600);
      check('scan summary cards', (await page.locator('#scan-summary .card').count()) >= 8);
    }
    await shot('10-scan');

    // >>> media checks (owned by the media feature; insert between these lines)
    {
      await page.goto(url('library'));
      await page.waitForSelector(rows, { timeout: 15000 });
      await settle();
      const firstRow = page.locator(rows).first();
      await firstRow.click();
      await settle(500);
      const wave = await page.evaluate(() => ({ cls: document.getElementById('tp-wave').className, ph: document.getElementById('tp-wave-ph').textContent, vis: getComputedStyle(document.getElementById('tp-wave-ph')).display !== 'none', w: document.getElementById('tp-wave-c').width }));
      check('waveform area renders a placeholder', wave.vis && wave.ph.length > 0 && wave.w > 0, JSON.stringify(wave));
      check('scrub stays an accessible range control', await page.evaluate(() => { const s = document.getElementById('tp-scrub'); return s.type === 'range' && s.getAttribute('aria-label') === 'Playback position'; }));
      await page.locator('#lib-table .vt-body').focus();
      const os0 = await page.getAttribute('#tp-oneshot', 'aria-pressed');
      await page.keyboard.press('o');
      check('O toggles retrigger', (await page.getAttribute('#tp-oneshot', 'aria-pressed')) !== os0);
      check('retrigger persisted', await page.evaluate(() => localStorage.getItem('slo.oneshot') === String(document.getElementById('tp-oneshot').getAttribute('aria-pressed') === 'true')));
      if ((await page.getAttribute('#tp-oneshot', 'aria-pressed')) !== 'true') await page.click('#tp-oneshot');
      check('retrigger turns on arrow auditions', await page.evaluate(() => Player.autoOn()));
      const c0 = await page.evaluate(() => Library.table.cursor);
      await page.keyboard.press('ArrowDown');
      await settle(200);
      check('arrows still move with retrigger on', (await page.evaluate(() => Library.table.cursor)) === c0 + 1);
      await page.click('#tp-oneshot');
      check('retrigger button toggles off', (await page.getAttribute('#tp-oneshot', 'aria-pressed')) === 'false');
      const drag = await page.evaluate(() => {
        const el = document.querySelector('#lib-table .vt-row:not(.is-ph)');
        const dt = new DataTransfer();
        el.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt }));
        const out = { draggable: el.getAttribute('draggable'), dl: dt.getData('DownloadURL'), uri: dt.getData('text/uri-list'), txt: dt.getData('text/plain') };
        el.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer: dt }));
        return out;
      });
      check('rows are draggable', drag.draggable === 'true');
      check('dragstart sets DownloadURL to the original file', /^[\w/-]+:[^:]+:http:\/\/127\.0\.0\.1:\d+\/api\/audio\/[\w-]+\?download=1$/.test(drag.dl), drag.dl);
      check('dragstart sets uri-list and path', drag.uri.startsWith('file:///') && drag.txt.length > 3, drag.uri + ' | ' + drag.txt);
      await settle(200);
      check('table renders after drag', (await page.locator(rows).count()) > 0);
      check('inspector offers copy file to clipboard', (await page.locator('#ins-clipfile').count()) === 1);
      await page.keyboard.press('?');
      check('keys dialog lists retrigger', /Retrigger/.test(await page.textContent('#keys-dlg')));
      await page.keyboard.press('Escape');
      check('no horizontal page overflow (deck)', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await shot('11-media-deck');
      await page.setViewportSize({ width: 1920, height: 1080 });
      await settle(300);
      await shot('11-media-deck-1920');
      await page.setViewportSize({ width: 1366, height: 768 });
      await settle(200);
    }
    // <<< media checks
    //
    //
    // >>> collections checks (owned by the collections feature; insert between these lines)
    // <<< collections checks
    //
    //
    // >>> undo checks (owned by the undo feature; insert between these lines)
    await page.click('.rail-btn[data-view="log"]');
    await settle(700);
    check('undo button exists in Move Log', (await page.locator('#view-log #log-undo').count()) === 1);
    if (fixture) {
      check('undo button visible and enabled', (await page.isVisible('#log-undo')) && (await page.isEnabled('#log-undo')));
      await page.click('#log-undo');
      await page.waitForSelector('#undo-dlg[open]', { timeout: 5000 });
      check('undo dialog lists moves and links', /moved back/.test(await page.textContent('#undo-table')) && /kit hard link/.test(await page.textContent('#undo-table')));
      await shot('11-undo-dialog');
      await page.click('#undo-go');
      await page.waitForFunction(() => location.hash === '#review', null, { timeout: 10000 });
      await page.waitForSelector('#undo-banner:not([hidden])', { timeout: 10000 });
      await settle(700);
      check('undo plan banner renders', /Undo plan/.test(await page.textContent('#undo-banner')) && /go back to their original folders/.test(await page.textContent('#undo-banner')));
      check('undo plan cards', /move back/.test(await page.textContent('#plan-cards')) && /remove kit link/.test(await page.textContent('#plan-cards')));
      check('rebuild button names the undo plan', /undo/i.test(await page.textContent('#plan-build')));
      check('undo columns relabelled', /Goes back to/.test(await page.textContent('#op-table .vt-head')));
      const acts = await page.$$eval('#op-table .vt-row:not(.is-ph) .act', (b) => b.map((x) => x.textContent));
      check('undo ops use undo actions only', acts.length > 0 && acts.every((a) => ['Move', 'Unlink', 'Hold', 'Keep'].includes(a)), acts.slice(0, 8).join(','));
      await page.selectOption('#op-action', 'move');
      await settle(500);
      const mv = await page.$$eval('#op-table .vt-row:not(.is-ph) .act', (b) => b.map((x) => x.textContent));
      check('undo moves back listed', mv.length > 0 && mv.every((a) => a === 'Move'), mv.slice(0, 4).join(','));
      await page.selectOption('#op-action', '');
      await settle(400);
      check('no horizontal page overflow (undo review)', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.click('#op-table .vt-row:not(.is-ph)');
      await settle(300);
      check('undo op detail shows where it goes back', /Goes back to|File stays at/.test(await page.textContent('#op-detail')));
      await shot('12-undo-review');
      await page.setViewportSize({ width: 1920, height: 1080 });
      await settle(500);
      check('no horizontal page overflow (undo review 1920)', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await shot('12-undo-review-1920');
      await page.setViewportSize({ width: 1366, height: 768 });
      await settle(300);
    }
    // <<< undo checks
    //
    //
    // >>> findsounds checks (owned by the findsounds feature; insert between these lines)
    {
      // ---------- Find Sounds ----------
      await page.click('.rail-btn[data-view="find"]');
      await page.waitForSelector('#find-h', { timeout: 5000 });
      await settle(400);
      check('find: view renders', /Find Sounds/.test(await page.textContent('#find-h')));
      if (fixture) {
        check('find: no-key state offers inline key and settings link', await page.isVisible('#fs-key-in') && (await page.locator('#fs-to-settings').count()) === 1);
        await shot('11-find-nokey');
        await page.fill('#fs-key-in', 'fixturekey123456');
        await page.click('#fs-keyform button[type=submit]');
        await page.waitForSelector('#fs-q', { state: 'visible' });
        await settle(300);
        check('find: CC0 chip on by default', (await page.getAttribute('#fs-cc0', 'aria-pressed')) === 'true');
        check('find: attribution off by default', (await page.getAttribute('#fs-attr', 'aria-pressed')) === 'false');
        check('find: NonCommercial never offered', (await page.locator('#view-find button:has-text("NonCommercial"), #view-find input[value*="NonCommercial" i]').count()) === 0 && /never shown/.test(await page.textContent('.fs-nc')));
        check('find: quota visible', /\d+ \/ 2,000/.test(await page.textContent('#fs-quota')));
        await page.fill('#fs-q', 'kick');
        await page.press('#fs-q', 'Enter');
        await page.waitForSelector('#fs-results .fs-list .fs-row', { timeout: 5000 });
        const n0 = await page.locator('#fs-results .fs-list .fs-row').count();
        check('find: search renders results', n0 > 0, `rows=${n0}`);
        check('find: only CC0 badges by default', (await page.locator('#fs-results .fs-badge.fs-by').count()) === 0 && (await page.locator('#fs-results .fs-badge.fs-cc0').count()) === n0);
        check('find: Open on Freesound opens a new tab', (await page.getAttribute('#fs-results .fs-row a.btn', 'target')) === '_blank');
        check('find: quota counts the search', /1 \/ 2,000/.test(await page.textContent('#fs-quota')));
        await page.fill('#fs-q', '');
        await page.click('#fs-attr');
        await page.waitForSelector('#fs-results .fs-badge.fs-by', { timeout: 5000 });
        check('find: attribution toggle adds CC BY results', (await page.getAttribute('#fs-attr', 'aria-pressed')) === 'true');
        await page.waitForSelector('#fs-pager:not([hidden])', { timeout: 5000 });
        await page.click('#fs-next');
        await settle(400);
        check('find: next page', /Page 2 of/.test(await page.textContent('#fs-page')));
        await page.click('#fs-results .fs-row [data-act="dl"]');
        await page.waitForSelector('#fs-results .fs-saved', { timeout: 5000 });
        check('find: download marks row saved', (await page.locator('#fs-results .fs-saved').count()) === 1);
        check('find: preview fallback labelled lower quality', /lower quality/.test(await page.textContent('#fs-side')));
        check('find: no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await shot('12-find-results');
        await page.setViewportSize({ width: 1920, height: 1080 });
        await settle(300);
        await shot('12b-find-results-1080');
        check('find: no horizontal overflow at 1920', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.setViewportSize({ width: 1366, height: 768 });
        await page.fill('#fs-q', 'ratelimit');
        await page.press('#fs-q', 'Enter');
        await page.waitForSelector('.fs-b-wait', { timeout: 5000 });
        check('find: rate-limited banner shows wait', /try again in \d+ s/i.test(await page.textContent('#fs-banner')));
        await shot('13-find-ratelimited');
        await page.fill('#fs-q', 'offline');
        await page.press('#fs-q', 'Enter');
        await page.waitForSelector('.fs-b-off', { timeout: 5000 });
        check('find: offline banner', /Offline/.test(await page.textContent('#fs-banner')));
        await page.fill('#fs-q', '');
      } else {
        check('find: shows key form or search box', await page.isVisible('#fs-key-in') || await page.isVisible('#fs-q'));
        await shot('11-find');
      }
    }
    // <<< findsounds checks
    //
    //
    // >>> settings checks (owned by the settings feature; insert between these lines)
    // <<< settings checks
    //
    //
    // ---------- Focus ring visible ----------
    await page.focus('.rail-btn[data-view="library"]');
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    const ring = await page.evaluate(() => getComputedStyle(document.activeElement).boxShadow);
    check('keyboard focus ring visible', ring && ring !== 'none', ring);
    check('no horizontal page overflow (end)', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  } catch (e) {
    check('script ran to completion', false, e.message.split('\n')[0]);
    await shot('zz-failure').catch(() => {});
  }
  check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 5).join(' | '));
  await browser.close();
  console.log(results.join('\n'));
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})();
