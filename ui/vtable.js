/* vtable.js: virtualized, server-paged table. Only visible rows are in the DOM,
   pages are fetched on demand with offset/limit, so 100k rows stay responsive. */
'use strict';

class VTable {
  constructor(el, opts) {
    this.el = el;
    this.o = Object.assign({ rowHeight: 28, pageSize: 200, overscan: 8, label: 'Table', multiselect: false }, opts);
    this.rh = this.o.rowHeight;
    this.pages = new Map();
    this.total = null;
    this.gen = 0;
    this.cursor = -1;
    this.error = null;
    this.sort = this.o.sort || null;
    this.dir = this.o.dir || 'asc';
    el.classList.add('vt');
    el.style.setProperty('--cols', this.o.columns.map((c) => c.width).join(' '));
    el.style.setProperty('--rh', this.rh + 'px');
    el.innerHTML = '';
    this.head = document.createElement('div');
    this.head.className = 'vt-head';
    this.head.setAttribute('role', 'row');
    this.body = document.createElement('div');
    this.body.className = 'vt-body';
    this.body.tabIndex = 0;
    this.body.setAttribute('role', 'grid');
    this.body.setAttribute('aria-label', this.o.label);
    if (this.o.multiselect) this.body.setAttribute('aria-multiselectable', 'true');
    this.spacer = document.createElement('div'); this.spacer.className = 'vt-spacer';
    this.rows = document.createElement('div'); this.rows.className = 'vt-rows'; this.rows.setAttribute('role', 'rowgroup');
    this.state = document.createElement('div'); this.state.className = 'vt-state';
    this.body.append(this.spacer, this.rows, this.state);
    el.append(this.head, this.body);
    this.renderHead();

    this.body.addEventListener('scroll', () => this.schedule(), { passive: true });
    this.ro = new ResizeObserver(() => { this.syncHeadPad(); this.schedule(); });
    this.ro.observe(this.body);
    this.rows.addEventListener('click', (ev) => {
      const r = ev.target.closest('.vt-row'); if (!r) return;
      const i = +r.dataset.i; const row = this.getRow(i); if (!row) return;
      const act = ev.target.closest('[data-act]');
      if (act && this.o.onAction) { ev.stopPropagation(); this.o.onAction(act.dataset.act, row, i, ev); return; }
      this.setCursor(i, false);
      if (this.o.onRowClick) this.o.onRowClick(i, row, ev);
      if (!act) this.body.focus({ preventScroll: true });
    });
    this.rows.addEventListener('dblclick', (ev) => {
      const r = ev.target.closest('.vt-row'); if (!r || ev.target.closest('[data-act]')) return;
      const row = this.getRow(+r.dataset.i); if (row && this.o.onRowDblClick) this.o.onRowDblClick(+r.dataset.i, row, ev);
    });
    this.body.addEventListener('keydown', (ev) => this.onKey(ev));
    // Opt-in draggable rows: {draggable: true, onDragStart(i, row, ev) -> false to cancel, onDragEnd?(ev)}.
    // Rows are not re-rendered while a drag is in flight so the drag source stays in the DOM.
    this.rows.addEventListener('dragstart', (ev) => {
      if (!this.o.draggable) return;
      const r = ev.target.closest && ev.target.closest('.vt-row');
      const row = r && !r.classList.contains('is-ph') ? this.getRow(+r.dataset.i) : null;
      if (!row || !this.o.onDragStart || this.o.onDragStart(+r.dataset.i, row, ev) === false) { ev.preventDefault(); return; }
      this.dragging = true;
      r.classList.add('is-drag');
    });
    this.rows.addEventListener('dragend', (ev) => {
      if (!this.dragging) return;
      this.dragging = false;
      if (this.o.onDragEnd) this.o.onDragEnd(ev);
      this.render();
    });
  }

  renderHead() {
    this.head.innerHTML = this.o.columns.map((c) => {
      const cls = 'vt-th ' + (c.cls || '');
      if (!c.sort) return `<span class="${cls}" role="columnheader">${c.label ? esc(c.label) : `<span class="sr">${esc(c.sr || '')}</span>`}</span>`;
      const on = this.sort === c.sort;
      const aria = on ? (this.dir === 'asc' ? 'ascending' : 'descending') : 'none';
      return `<span class="${cls}" role="columnheader" aria-sort="${aria}"><button type="button" class="vt-sort${on ? ' on' : ''}" data-sort="${c.sort}" title="Sort by ${esc(c.label)}">${esc(c.label)}<i class="arrow" aria-hidden="true">${on ? (this.dir === 'asc' ? '▲' : '▼') : ''}</i></button></span>`;
    }).join('');
    $$('.vt-sort', this.head).forEach((b) => b.addEventListener('click', () => {
      const s = b.dataset.sort;
      if (this.sort === s) this.dir = this.dir === 'asc' ? 'desc' : 'asc';
      else { this.sort = s; this.dir = 'asc'; }
      this.renderHead();
      if (this.o.onSort) this.o.onSort(this.sort, this.dir);
      this.reset();
    }));
    this.syncHeadPad();
  }

  syncHeadPad() {
    const sb = this.body.offsetWidth - this.body.clientWidth;
    this.head.style.paddingRight = sb + 'px';
  }

  /* Drop all cached pages and reload from the top. */
  reset(keepScroll) {
    this.gen++;
    this.pages.clear();
    this.error = null;
    if (!keepScroll) { this.total = null; this.cursor = -1; this.body.scrollTop = 0; }
    if (!keepScroll) { /* motion.css: entry animation on fresh lists only, never on scroll re-renders */
      this.el.classList.add('vt-enter'); clearTimeout(this._enterT);
      this._enterT = setTimeout(() => this.el.classList.remove('vt-enter'), 900);
    }
    this.loadPage(Math.floor((keepScroll ? this.body.scrollTop / this.rh : 0) / this.o.pageSize));
    this.render();
  }
  refresh() { this.reset(true); }

  async loadPage(p) {
    if (this.pages.has(p)) return this.pages.get(p);
    const gen = this.gen;
    const ps = this.o.pageSize;
    const prom = this.o.fetch(p * ps, ps).then((res) => {
      if (gen !== this.gen) return null;
      this.total = res.total;
      this.pages.set(p, res.items);
      this.error = null;
      if (this.o.onTotal) this.o.onTotal(res.total, res);
      this.schedule();
      return res.items;
    }).catch((e) => {
      if (gen !== this.gen) return null;
      this.pages.delete(p);
      this.error = e;
      this.render();
      return null;
    });
    this.pages.set(p, prom);
    return prom;
  }

  getRow(i) {
    const pg = this.pages.get(Math.floor(i / this.o.pageSize));
    return Array.isArray(pg) ? pg[i % this.o.pageSize] : null;
  }
  loadedRows() {
    const out = [];
    for (const [p, items] of this.pages) if (Array.isArray(items)) items.forEach((r, k) => out.push([p * this.o.pageSize + k, r]));
    return out;
  }
  /* Fetch every row (bounded) — used for Ctrl+A select all. */
  async loadAll(max) {
    const total = Math.min(this.total || 0, max || 50000);
    const n = Math.ceil(total / this.o.pageSize);
    for (let p = 0; p < n; p += 4) {
      const batch = [];
      for (let k = p; k < Math.min(n, p + 4); k++) batch.push(this.loadPage(k));
      await Promise.all(batch);
    }
    const out = [];
    for (let p = 0; p < n; p++) { const it = this.pages.get(p); if (Array.isArray(it)) out.push(...it); }
    return out.slice(0, total);
  }

  schedule() {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => { this.raf = 0; this.render(); });
  }

  render() {
    if (this.dragging) return; // re-rendered on dragend
    const total = this.total || 0;
    this.spacer.style.height = total * this.rh + 'px';
    this.body.setAttribute('aria-rowcount', String(total));
    if (this.error) {
      this.state.className = 'vt-state is-error';
      this.state.innerHTML = `<div><strong>Couldn't load this list.</strong><p>${esc(this.error.message)}</p><button class="btn btn-small" type="button">Try again</button></div>`;
      $('button', this.state).onclick = () => this.reset();
      this.rows.innerHTML = '';
      return;
    }
    if (this.total === null) {
      this.state.className = 'vt-state is-loading';
      this.state.innerHTML = '<div class="loading"><i></i><i></i><i></i><span>Loading</span></div>';
      this.rows.innerHTML = '';
      return;
    }
    if (total === 0) {
      this.state.className = 'vt-state is-empty';
      this.state.innerHTML = this.o.emptyHtml ? this.o.emptyHtml() : '<div><strong>Nothing here.</strong></div>';
      const b = $('[data-empty-act]', this.state);
      if (b && this.o.onEmptyAction) b.onclick = () => this.o.onEmptyAction(b.dataset.emptyAct);
      this.rows.innerHTML = '';
      return;
    }
    this.state.className = 'vt-state';
    this.state.innerHTML = '';
    const h = this.body.clientHeight || 400;
    const first = Math.max(0, Math.floor(this.body.scrollTop / this.rh) - this.o.overscan);
    const last = Math.min(total - 1, Math.ceil((this.body.scrollTop + h) / this.rh) + this.o.overscan);
    const ps = this.o.pageSize;
    for (let p = Math.floor(first / ps); p <= Math.floor(last / ps); p++) if (!this.pages.has(p)) this.loadPage(p);
    const cols = this.o.columns;
    let html = '';
    let prev = first > 0 ? this.getRow(first - 1) : null;
    for (let i = first; i <= last; i++) {
      const row = this.getRow(i);
      if (!row) { html += `<div class="vt-row is-ph" style="top:${i * this.rh}px" aria-hidden="true"><span class="ph"></span></div>`; prev = null; continue; }
      const sel = this.o.isSelected ? this.o.isSelected(row, i) : false;
      const extra = this.o.rowClass ? this.o.rowClass(row, i, prev) : '';
      html += `<div class="vt-row${i === this.cursor ? ' cursor' : ''}${sel ? ' sel' : ''} ${extra}" role="row" id="${this.el.id}-r${i}" data-i="${i}" aria-rowindex="${i + 1}" aria-selected="${sel ? 'true' : 'false'}"${this.o.draggable ? ' draggable="true"' : ''} style="top:${i * this.rh}px">`;
      for (const c of cols) html += `<span class="vt-td ${c.cls || ''}" role="gridcell"${c.title ? ` title="${esc(c.title(row))}"` : ''}>${c.render(row, i, prev)}</span>`;
      html += '</div>';
      prev = row;
    }
    this.rows.innerHTML = html;
    if (this.cursor >= 0) this.body.setAttribute('aria-activedescendant', `${this.el.id}-r${this.cursor}`);
    else this.body.removeAttribute('aria-activedescendant');
  }

  setCursor(i, scroll) {
    const total = this.total || 0;
    if (!total) return;
    i = Math.max(0, Math.min(total - 1, i));
    this.cursor = i;
    if (scroll !== false) this.scrollTo(i);
    this.render();
    const row = this.getRow(i);
    if (row && this.o.onCursor) this.o.onCursor(i, row);
    else if (!row) this.loadPage(Math.floor(i / this.o.pageSize)).then(() => { const r = this.getRow(i); if (r && this.cursor === i && this.o.onCursor) this.o.onCursor(i, r); });
  }
  scrollTo(i) {
    const top = i * this.rh, h = this.body.clientHeight;
    if (top < this.body.scrollTop) this.body.scrollTop = top;
    else if (top + this.rh > this.body.scrollTop + h) this.body.scrollTop = top + this.rh - h;
  }

  onKey(ev) {
    const page = Math.max(1, Math.floor(this.body.clientHeight / this.rh) - 1);
    let n = null;
    if (ev.key === 'ArrowDown') n = this.cursor + 1;
    else if (ev.key === 'ArrowUp') n = Math.max(0, this.cursor - 1);
    else if (ev.key === 'PageDown') n = this.cursor + page;
    else if (ev.key === 'PageUp') n = this.cursor - page;
    else if (ev.key === 'Home') n = 0;
    else if (ev.key === 'End') n = (this.total || 1) - 1;
    if (n === null) return;
    ev.preventDefault();
    if (this.cursor < 0 && ev.key === 'ArrowDown') n = 0;
    this.setCursor(n);
    if (this.o.onKeyMove) this.o.onKeyMove(this.cursor, this.getRow(this.cursor), ev);
  }
}
