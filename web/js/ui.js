/* 小工具：DOM 构造、标准像、提示、本机浏览器存储、日期。无框架。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  /* h('div', {class:'x', on:{click:fn}, data:{id:1}}, '文本', [node, ...]) */
  function h(tag, attrs) {
    const el = document.createElement(tag);
    const kids = Array.prototype.slice.call(arguments, 2);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        const v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'class') el.className = v;
        else if (k === 'html') el.innerHTML = v; /* 只用于本文件内的图标字符串，不放数据 */
        else if (k === 'text') el.textContent = v;
        else if (k === 'on') Object.keys(v).forEach(function (ev) { el.addEventListener(ev, v[ev]); });
        else if (k === 'data') Object.keys(v).forEach(function (dk) { el.dataset[dk] = v[dk]; });
        else if (k === 'style') el.style.cssText = v;
        else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'hidden' || k === 'selected') el[k] = v;
        else el.setAttribute(k, v === true ? '' : v);
      });
    }
    append(el, kids);
    return el;
  }
  function append(el, kids) {
    kids.forEach(function (kid) {
      if (kid === null || kid === undefined || kid === false) return;
      if (Array.isArray(kid)) return append(el, kid);
      el.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
    });
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

  const ICONS = {
    back: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 5-7 7 7 7"/></svg>',
    chev: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
    close: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    out: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg>',
    /* 没有照片时的标准像占位：半身剪影 */
    blank: '<svg class="blank" viewBox="0 0 120 160" preserveAspectRatio="xMidYMax slice" aria-hidden="true"><circle cx="60" cy="62" r="25" fill="currentColor"/><path d="M14 160c2-34 21-52 46-52s44 18 46 52z" fill="currentColor"/></svg>'
  };

  /* 标准像：图片淡入；没有照片或加载失败显示剪影。 */
  function portrait(src, cls, alt) {
    const box = h('div', { class: cls || 'ph' });
    box.insertAdjacentHTML('beforeend', ICONS.blank);
    if (src) {
      const img = h('img', { alt: alt || '', loading: 'lazy', decoding: 'async', draggable: 'false' });
      img.addEventListener('load', function () { img.classList.add('ok'); const b = box.querySelector('.blank'); if (b) b.remove(); });
      img.addEventListener('error', function () { img.remove(); });
      img.src = src;
      box.appendChild(img);
      if (img.complete && img.naturalWidth) img.classList.add('ok');
    }
    return box;
  }

  let toastTimer = 0;
  function say(msg, ms) {
    const t = $('#toast');
    if (!t) return;
    t.textContent = msg;
    t.hidden = false;
    t.style.animation = 'none'; void t.offsetWidth; t.style.animation = '';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, ms || 2600);
  }

  const store = {
    get: function (k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } }
  };

  function ymd(iso) { /* 日期按 年-月-日 写：2026-10-14；数据只到月的照实写到月（2026-10），不补日 */
    const m = /^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?/.exec(iso || '');
    if (!m) return iso || '';
    const two = function (v) { return ('0' + v).slice(-2); };
    return m[1] + (m[2] ? '-' + two(m[2]) + (m[3] ? '-' + two(m[3]) : '') : '');
  }

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');

  ZZB.ui = { $: $, $$: $$, h: h, append: append, clear: clear, ICONS: ICONS, portrait: portrait, say: say, store: store, ymd: ymd, reduce: reduce };
})();
