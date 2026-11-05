/* 窗口外壳：标题栏里的栏目、搜索、三条线菜单，外部链接，和桌面外壳（window.shell，Electron preload 提供）的对接。
   普通浏览器里没有 window.shell：菜单照常用，外部链接在新标签打开。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, api = ZZB.api, h = ui.h, $ = ui.$;
  const native = window.shell || null;
  const BAR = '#b8141c';

  const shell = {
    native: native,
    /* 外部链接用系统浏览器打开 */
    open: function (url) {
      if (native && typeof native.openExternal === 'function') native.openExternal(url);
      else window.open(url, '_blank', 'noopener');
    },
    init: function (cfg, go) {
      if (native && typeof native.setTitleBarOverlay === 'function') {
        try { native.setTitleBarOverlay({ color: BAR, symbolColor: '#ffffff', height: 48 }); } catch (e) { /* 旧外壳没有这个接口 */ }
      }
      renderNav(cfg);
      initSearch(go);
      initMenu();
      document.addEventListener('click', function (e) { /* 页面里标了 data-external 的链接 */
        const a = e.target.closest && e.target.closest('a[data-external]');
        if (a) { e.preventDefault(); shell.open(a.href); }
      });
    },
    mark: markNav
  };

  /* ── 栏目 ── */
  function renderNav(cfg) {
    const nav = $('#nav');
    ui.$$('a', nav).forEach(function (a) { a.remove(); });
    cfg.sections.forEach(function (s) {
      nav.appendChild(h('a', { href: '#/' + s.id, data: { id: s.id } }, s.title));
    });
    window.addEventListener('resize', function () { markNav(nav.dataset.on || '', true); });
  }
  function markNav(id, instant) {
    const nav = $('#nav'), bar = $('#nav-bar');
    nav.dataset.on = id;
    let on = null;
    ui.$$('a', nav).forEach(function (a) { const yes = a.dataset.id === id; a.classList.toggle('on', yes); if (yes) on = a; });
    if (!on) { bar.style.opacity = '0'; return; }
    const w = Math.max(18, on.offsetWidth - 30);
    if (instant || bar.style.opacity !== '1') { bar.style.transition = 'none'; }
    bar.style.width = w + 'px';
    bar.style.transform = 'translateX(' + (on.offsetLeft + (on.offsetWidth - w) / 2) + 'px)';
    bar.style.opacity = '1';
    if (bar.style.transition === 'none') { void bar.offsetWidth; bar.style.transition = ''; }
  }

  /* ── 搜索：只搜名单里的人，在本机内存里即时筛 ── */
  function initSearch(go) {
    const box = $('#search'), q = $('#q'), pop = $('#search-pop');
    let index = null, hits = [], sel = 0;
    function load() { if (!index) index = api.index().then(function (r) { return r.items || []; }).catch(function () { index = null; return []; }); return index; }
    function score(p, s) {
      const n = p.name.toLowerCase(), e = (p.name_en || '').toLowerCase(), t = (p.post || '').toLowerCase();
      if (n === s || e === s) return 0;
      if (n.indexOf(s) === 0) return 1;
      if (n.replace(/[·.\s]/g, '').indexOf(s.replace(/[·.\s]/g, '')) >= 0) return 2;
      if (e.split(/\s+/).some(function (w) { return w.indexOf(s) === 0; })) return 3;
      if (e.indexOf(s) >= 0) return 4;
      if (t.indexOf(s) >= 0) return 5;
      return -1;
    }
    function mark(name, s) {
      const i = name.toLowerCase().indexOf(s);
      return i < 0 ? name : [name.slice(0, i), h('mark', null, name.slice(i, i + s.length)), name.slice(i + s.length)];
    }
    async function run() {
      const s = q.value.trim().toLowerCase();
      if (!s) { pop.hidden = true; return; }
      const all = await load();
      hits = all.map(function (p) { return [score(p, s), p]; }).filter(function (x) { return x[0] >= 0; })
        .sort(function (a, b) { return a[0] - b[0]; }).slice(0, 8).map(function (x) { return x[1]; });
      sel = 0;
      ui.clear(pop);
      if (!hits.length) pop.appendChild(h('div', { class: 'sr-empty' }, '没有找到'));
      hits.forEach(function (p, i) {
        pop.appendChild(h('div', { class: 'sr' + (i === 0 ? ' on' : ''), role: 'option', on: {
          mousedown: function (e) { e.preventDefault(); pick(i); },
          mouseenter: function () { setSel(i); api.prefetch(p.id); }
        } }, ui.portrait(p.portrait, 'sr-ph'),
        h('div', { class: 'sr-tx' }, h('div', { class: 'sr-nm' }, mark(p.name, s)), h('div', { class: 'sr-ps' }, p.post)),
        h('span', { class: 'sr-sec' }, p.section_title)));
      });
      pop.hidden = false;
    }
    function setSel(i) { sel = i; ui.$$('.sr', pop).forEach(function (el, k) { el.classList.toggle('on', k === i); }); }
    function pick(i) {
      const p = hits[i];
      if (!p) return;
      q.value = ''; pop.hidden = true; q.blur();
      go('#/p/' + p.id);
    }
    q.addEventListener('focus', function () { box.classList.add('focus'); load(); if (q.value.trim()) run(); });
    q.addEventListener('blur', function () { box.classList.remove('focus'); setTimeout(function () { pop.hidden = true; }, 120); });
    q.addEventListener('input', run);
    q.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' && hits.length) { e.preventDefault(); setSel((sel + 1) % hits.length); }
      else if (e.key === 'ArrowUp' && hits.length) { e.preventDefault(); setSel((sel - 1 + hits.length) % hits.length); }
      else if (e.key === 'Enter') { e.preventDefault(); pick(sel); }
      else if (e.key === 'Escape') { q.value = ''; pop.hidden = true; q.blur(); }
    });
    document.addEventListener('keydown', function (e) {
      const typing = /INPUT|TEXTAREA|SELECT/.test((e.target && e.target.tagName) || '');
      if ((e.key === '/' && !typing) || ((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'k'))) { e.preventDefault(); q.focus(); q.select(); }
    });
  }

  /* ── 三条线菜单：设置、关于 ── */
  function initMenu() {
    const btn = $('#menu-btn'), menu = $('#menu');
    function close() { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); }
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      const open = menu.hidden;
      menu.hidden = !open; btn.setAttribute('aria-expanded', String(open));
    });
    menu.addEventListener('click', function (e) {
      const b = e.target.closest('[data-open]');
      if (!b) return;
      close();
      ZZB.panels.open(b.dataset.open);
    });
    document.addEventListener('mousedown', function (e) { if (!menu.hidden && !menu.contains(e.target) && e.target !== btn && !btn.contains(e.target)) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    window.addEventListener('blur', close);
  }

  ZZB.shell = shell;
})();
