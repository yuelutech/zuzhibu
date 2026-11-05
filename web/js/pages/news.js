/* 人事动态：照共产党员网人事任免栏目。列表是标题加日期；单篇从上到下是标题、正文、“人事任免”横图、发布时间和来源。
   稿件（公开什么、怎么合篇、标题、正文、来源一栏）都由本地服务给（zuzhibu/renmian.py），界面不设类别标签和筛选。
   正文里只写姓名，不加链接。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, api = ZZB.api, h = ui.h;
  ZZB.pages = ZZB.pages || {};
  const PAGE = 20;

  ZZB.pages.news = function () {
    let shown = PAGE;
    return {
      load: function () { return api.news(); },
      render: function (root, d, ctx) {
        const items = d.items || [];
        shown = Math.max(PAGE, ctx.state.shown || PAGE);
        const list = h('div', { class: 'nlist-rows' });
        const more = h('button', { class: 'more', type: 'button', on: { click: function () { shown += PAGE; ctx.state.shown = shown; fill(); } } }, '加载更多');
        function fill() {
          for (let i = list.children.length; i < Math.min(shown, items.length); i++) {
            const a = items[i];
            list.appendChild(h('a', { class: 'nrow', href: '#/news/' + encodeURIComponent(a.id), on: { mouseenter: function () { api.article(a.id).catch(function () {}); } } },
              h('span', { class: 'nt' }, a.title), h('time', { datetime: a.date }, ui.ymd(a.date))));
          }
          more.hidden = shown >= items.length;
        }
        fill();
        root.appendChild(h('div', { class: 'newsroom' },
          h('div', { class: 'banner' }, h('h1', null, '人事动态')),
          h('div', { class: 'sheet nlist' }, list, items.length ? more : h('p', { class: 'loading' }, '暂无人事动态'))));
      }
    };
  };

  ZZB.pages.article = function () {
    return {
      load: function (ctx) { return api.article(ctx.arg); },
      render: function (root, a, ctx) {
        const banner = h('a', { class: 'a-banner', href: '#/news', 'aria-label': '人事动态' },
          h('img', { src: 'img/renmian.png', srcset: 'img/renmian.png 1x, img/renmian@2x.png 2x', width: '750', height: '230', alt: '人事任免', draggable: 'false' }));
        root.appendChild(h('div', { class: 'page' },
          ctx.backButton(),
          h('article', { class: 'article' },
            h('h1', { class: 'a-title sel' }, a.title),
            h('div', { class: 'a-body sel' }, (a.paragraphs || []).map(function (p) { return h('p', null, p); })),
            banner,
            h('div', { class: 'a-foot sel' }, h('span', null, '发布时间：' + ui.ymd(a.date)), a.source_line ? h('span', null, '来源：' + a.source_line) : null))));
        document.title = a.title + ' · AI政见·组织部';
      }
    };
  };
})();
