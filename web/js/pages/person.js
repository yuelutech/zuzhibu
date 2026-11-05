/* 人物页：只放标题（××简历）、标准像、标准简历（首段、现任、按时间排的经历），不放来源、相关动态和下载入口。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, api = ZZB.api, h = ui.h;
  ZZB.pages = ZZB.pages || {};

  ZZB.pages.person = function () {
    return {
      load: function (ctx) { return api.profile(ctx.arg); },
      render: function (root, p, ctx) {
        const photo = ui.portrait(p.portrait, 'p-photo', p.name);
        photo.style.viewTransitionName = 'hero';
        const text = h('div', { class: 'p-text sel' },
          p.lead ? h('p', null, p.lead) : null,
          p.current ? h('p', null, p.current) : null);
        const careers = p.careers && p.careers.length ? h('div', { class: 'p-careers sel' }, p.careers.map(function (c) {
          return [h('div', { class: 'p-period' }, c.period), h('div', null, c.text)];
        })) : null;
        root.appendChild(h('div', { class: 'page' },
          ctx.backButton(),
          h('article', { class: 'sheet profile' },
            h('h1', { class: 'p-title sel' }, p.title || p.name),
            h('div', { class: 'p-rule', 'aria-hidden': 'true' }),
            h('div', { class: 'p-top' }, photo, text),
            careers)));
        document.title = p.name + ' · AI政见·组织部';
      }
    };
  };
})();
