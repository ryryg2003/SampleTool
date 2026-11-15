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
    check('nav always visible', (await page.locator('.rail-btn').count()) === 4);
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
      await page.selectOption('#op-action', 'hold');
      await settle(500);
      const acts = await page.$$eval(`${opRows} .act`, (as) => as.map((a) => a.textContent));
      check('action filter', acts.length > 0 && acts.every((a) => a === 'Hold'), acts.slice(0, 5).join(','));
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
