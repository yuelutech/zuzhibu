/* 路由与页面切换。#/ 封面（打开软件先到这里）；#/whitehouse 等五栏名单；#/p/<人物> 人物页；#/news 人事动态；#/news/<稿件> 单篇。
   页面先取数据再切换，切换用浏览器的视图过渡（标准像从名单移到人物页）；返回时回到原来的滚动位置。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, api = ZZB.api, h = ui.h, $ = ui.$;

  const app = { cfg: null, key: null, page: null, last: '' };
  const scrolls = {}, states = {};
  let seq = 0;

  function parse() {
    const raw = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
    const parts = raw.split('?')[0].split('/');
    return { route: parts[0] || '', arg: parts.slice(1).join('/') };
  }
  function go(hash) { if (hash === location.hash) route(); else location.hash = hash; }
  function section(id) { return (app.cfg.sections || []).find(function (s) { return s.id === id; }); }

  function resolve(r) {
    if (!r.route) return { key: 'home', page: ZZB.pages.home, nav: '' };
    if (r.route === 'p' && r.arg) return { key: 'p/' + r.arg, page: ZZB.pages.person, nav: app.last, arg: r.arg };
    if (r.route === 'news' && r.arg) return { key: 'news/' + r.arg, page: ZZB.pages.article, nav: 'news', arg: r.arg };
    if (r.route === 'news') return { key: 'news', page: ZZB.pages.news, nav: 'news', title: '人事动态' };
    if (section(r.route)) return { key: r.route, page: ZZB.pages.roster, nav: r.route, arg: r.route, title: section(r.route).title };
    return null;
  }

  function backButton(fallback) {
    return function () {
      const b = h('button', { class: 'back', type: 'button', on: { click: function () {
        if (history.length > 1 && app.prev) history.back(); else go(fallback());
      } } });
      b.insertAdjacentHTML('beforeend', ui.ICONS.back);
      b.appendChild(document.createTextNode('返回'));
      return b;
    };
  }

  async function route() {
    const r = parse();
    const target = resolve(r);
    if (!target) { location.replace('#/'); return; }
    if (target.key === app.key) return;
    const my = ++seq;
    const main = $('#main'), view = $('#view');
    const fromKey = app.key;
    if (fromKey) scrolls[fromKey] = main.scrollTop;
    const page = target.page();
    const state = (states[target.key] = states[target.key] || {});
    const ctx = { arg: target.arg, go: go, state: state, hero: fromKey && fromKey.indexOf('p/') === 0 ? fromKey.slice(2) : null,
      backButton: backButton(function () { return target.nav === 'news' ? '#/news' : '#/' + (app.last || app.cfg.home); }) };
    let data;
    try { data = await page.load(ctx); } catch (e) {
      if (my !== seq) return;
      swap(function () { ui.clear(view).appendChild(h('div', { class: 'err' }, h('p', null, e.status === 404 ? '没有找到这一页' : ('读不到数据：' + e.message)))); }, false);
      app.key = target.key; app.page = null;
      return;
    }
    if (my !== seq) return;
    if (data && data.sections && data.sections.length && data.sections.indexOf(target.nav) < 0) target.nav = data.sections[0]; /* 人物页：高亮所在的栏目 */
    if (section(target.nav)) { app.last = target.nav; ui.store.set('zzb.last', target.nav); }
    app.prev = fromKey;
    swap(function () {
      if (app.page && app.page.unmount) app.page.unmount();
      ui.clear(view);
      app.page = page; app.key = target.key;
      page.render(view, data, ctx);
      if (target.title) document.title = target.title + ' · ' + app.cfg.name;
      ZZB.shell.mark(target.nav);
      main.scrollTop = scrolls[target.key] || 0;
    }, !!fromKey);
  }

  /* 视图过渡：支持的浏览器（桌面外壳的 Chromium）里淡入淡出、标准像飞到新位置；不支持或要求减少动效时直接换。 */
  function swap(fn, animate) {
    if (animate && document.startViewTransition && !ui.reduce.matches) {
      const t = document.startViewTransition(fn);
      t.finished.finally(function () { ui.$$('.ph', document).forEach(function (x) { x.style.viewTransitionName = ''; }); });
    } else {
      fn();
      $('#view').classList.remove('fade-in'); void $('#view').offsetWidth;
      if (animate !== false) $('#view').classList.add('fade-in');
    }
  }

  async function reload() {
    api.reset();
    app.cfg = await api.app();
    app.key = null;
    route();
  }

  /* 每分钟向本机主程序报一次到（安装版据此判断窗口还开着），顺带看数据有没有联网更新过；页面关掉时说一声。 */
  function heartbeat() {
    const client = Math.random().toString(36).slice(2) + Date.now().toString(36);
    let seen = null;
    async function beat() {
      let res;
      try { res = await api.ping(client); } catch (e) { return; }
      if (seen && res.data !== seen) { api.reset(); ui.say('数据已更新，切换页面即可看到', 5000); }
      seen = res.data;
    }
    beat();
    setInterval(beat, 60000);
    window.addEventListener('pagehide', function () { api.bye(client); });
  }

  async function boot() {
    try { app.cfg = await api.app(); } catch (e) {
      $('#view').appendChild(h('div', { class: 'err' }, h('p', null, '读不到数据，请用本机主程序打开：'), h('pre', { class: 'sel' }, 'python -m zuzhibu'), h('p', { class: 'status' }, e.message)));
      return;
    }
    app.last = ui.store.get('zzb.last', app.cfg.home);
    ZZB.shell.init(app.cfg, go);
    window.addEventListener('hashchange', route);
    heartbeat();
    route();
  }

  ZZB.app = { reload: reload, go: go };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
