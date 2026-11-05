/* 封面：打开软件先看到这一页。上面是软件图标、“AI政见·组织部”和副题，中间六个栏目的入口，下面是最近几条人事动态（标题加日期）。
   点栏目进入；标题栏左边的图标和名字点了回到封面。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, api = ZZB.api, h = ui.h;
  ZZB.pages = ZZB.pages || {};
  const RECENT = 6;

  ZZB.pages.home = function () {
    return {
      load: function () {
        return Promise.all([api.app(), api.news().catch(function () { return { items: [] }; })])
          .then(function (r) { return { cfg: r[0], items: (r[1].items || []).slice(0, RECENT) }; });
      },
      render: function (root, d) {
        const mast = h('header', { class: 'h-mast' },
          h('img', { class: 'h-logo', src: 'favicon.svg', alt: '', width: '88', height: '88', draggable: 'false' }),
          h('h1', { class: 'h-name' }, 'AI政见', h('i', null, '·'), '组织部'),
          h('p', { class: 'h-sub' }, '美国政要干部简历资料库'),
          h('div', { class: 'h-rule', 'aria-hidden': 'true' }, h('i')));
        const entries = h('nav', { class: 'h-entries sheet', 'aria-label': '栏目' }, (d.cfg.sections || []).map(function (s) {
          return h('a', { href: '#/' + s.id }, h('span', null, s.title));
        }));
        const rows = d.items.map(function (a) {
          return h('a', { class: 'nrow', href: '#/news/' + encodeURIComponent(a.id), on: { mouseenter: function () { api.article(a.id).catch(function () {}); } } },
            h('span', { class: 'nt' }, a.title), h('time', { datetime: a.date }, ui.ymd(a.date)));
        });
        const news = rows.length ? h('section', { class: 'h-news sheet' },
          h('div', { class: 'h-news-head' }, h('h2', null, '人事动态'), h('a', { href: '#/news' }, '更多')), rows) : null;
        root.appendChild(h('div', { class: 'home' }, mast, entries, news));
        document.title = d.cfg.name;
      }
    };
  };
})();
