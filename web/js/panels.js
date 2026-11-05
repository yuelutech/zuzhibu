/* 三条线菜单打开的面板（设置、关于）：居中弹窗，遮罩淡入；Esc、点遮罩、右上角关闭。面板内容在 settings.js、about.js。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, h = ui.h, $ = ui.$;

  let current = null, lastFocus = null;
  const builders = {};

  function dialog(title, cls, body) {
    const box = h('div', { class: 'dialog ' + (cls || ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title });
    const x = h('button', { class: 'x', type: 'button', 'aria-label': '关闭', html: ui.ICONS.close, on: { click: close } });
    box.appendChild(h('div', { class: 'd-head' }, h('h2', null, title), x));
    box.appendChild(body);
    return box;
  }

  function open(name) {
    if (!builders[name]) return;
    close(true);
    const scrim = $('#scrim');
    lastFocus = document.activeElement;
    const built = builders[name]();
    current = built.el;
    current.zzbStop = built.stop;
    document.body.appendChild(current);
    scrim.hidden = false;
    requestAnimationFrame(function () { scrim.classList.add('open'); current.classList.add('open'); });
  }
  function close(instant) {
    const scrim = $('#scrim');
    if (!current) return;
    const el = current; current = null;
    if (el.zzbStop) el.zzbStop();
    el.classList.remove('open'); scrim.classList.remove('open');
    if (instant === true) { el.remove(); scrim.hidden = true; return; }
    setTimeout(function () { el.remove(); if (!current) scrim.hidden = true; }, 260);
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && current) close(); });
  document.addEventListener('mousedown', function (e) { if (current && e.target === $('#scrim')) close(); });

  /* 面板里的一节：红色短竖条加标题 */
  function section(title) { return h('section', { class: 'd-sec' }, h('h3', null, title)); }

  ZZB.panels = {
    open: open, close: close, dialog: dialog, section: section,
    register: function (name, build) { builders[name] = build; }
  };
})();
