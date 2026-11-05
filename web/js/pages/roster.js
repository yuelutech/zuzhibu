/* 五栏名单：照名单文件的分组和顺序逐级展示。style：lead 单人大像、row 一排、grid 网格、list 只列姓名和职务。
   国会页上面有筛选条：分组跳转、搜索框（姓名、职务）、身份、党派、州三个下拉。筛选对两院领导块和议员名单都生效，按人算：
   一个人的身份、党派、州取他在议员名单（filter 分组）里的那一条，搜索看他各处的姓名和职务；符合的人在他出现的各块里都显示，
   一个都不剩的块整块收起。不在议员名单里的（参议院议长）只在没选身份、党派、州时出现，搜姓名能搜到。
   “共 n 人”按人数算，领导块和议员名单里都有的只算一个。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, api = ZZB.api, h = ui.h;
  ZZB.pages = ZZB.pages || {};

  const SEATS = ['参议员', '众议员', '无表决权代表'];
  const PARTIES = ['共和党', '民主党', '无党派'];
  const BLANK = { q: '', seat: '', party: '', state: '' };
  let saved = Object.assign({}, BLANK); /* 筛选条件在这次打开软件期间保留，进人物页再回来还在 */

  /* 身份按名单的选区认：有选区的是众议员，众议员名单里没有选区的是无表决权代表，其余是参议员。 */
  function seatOf(m, house) { return m.district !== undefined && m.district !== null ? '众议员' : house ? '无表决权代表' : '参议员'; }
  /* 搜索不分大小写，不管间隔号、空格、点和连字符 */
  function fold(s) { return String(s || '').toLowerCase().replace(/[\s·・.\-'’]/g, ''); }

  /* 单人大像的职务和分组标题说的是一回事（“总统”与“美国总统”）时不重复写，照人民网像下只写姓名。 */
  function redundant(g, post) { return g.style === 'lead' && g.title && post && (post.indexOf(g.title) >= 0 || g.title.indexOf(post) >= 0); }

  function card(m, g, ctx) {
    const ph = ui.portrait(m.portrait, 'ph', m.name);
    if (ctx.hero === m.id) ph.style.viewTransitionName = 'hero';
    return h('a', { class: 'card', href: '#/p/' + m.id, data: { id: m.id }, on: {
      mouseenter: function () { api.prefetch(m.id); },
      click: function () { ui.$$('.ph', document).forEach(function (x) { x.style.viewTransitionName = ''; }); ph.style.viewTransitionName = 'hero'; }
    } }, ph, h('div', { class: 'nm' }, m.name),
    m.post && !redundant(g, m.post) ? h('div', { class: 'ps' }, m.post) : null,
    m.party ? h('div', { class: 'pt' }, m.party) : null);
  }

  /* 名单里每组有标题就显示分隔标题；没有标题的组自成一块，和上一组之间画细线隔开。同一块分几组（如首席大法官单人大像、
     大法官一排）时，后面的组写同一个标题：只在第一组显示标题，后面的组紧接着排。 */
  function group(g, ctx, dense, cont) {
    const cls = 'group ' + g.style + (dense ? ' dense' : '') + (cont ? ' cont' : g.title ? '' : ' untitled');
    return h('section', { class: cls, id: 'g-' + ctx.gi++ },
      g.title && !cont ? h('div', { class: 'g-head' }, h('h2', { class: 'g-title' }, g.title)) : null,
      g.note ? h('div', { class: 'g-note' }, '（' + g.note + '）') : null,
      g.style === 'list'
        ? h('div', { class: 'list' }, g.members.map(function (m) {
          return h('a', { class: 'li', href: '#/p/' + m.id, data: { id: m.id }, on: { mouseenter: function () { api.prefetch(m.id); } } }, h('b', null, m.name), h('span', null, m.post));
        }))
        : h('div', { class: 'cards' }, g.members.map(function (m) { return card(m, g, ctx); })));
  }

  /* 一行放不下时按行均分：8 人折成 4+4，17 人折成 6+6+5，不留一两个人单独一行。 */
  function balance(root) {
    ui.$$('.cards', root).forEach(function (box) {
      const cards = box.children;
      if (!cards.length) return;
      box.style.maxWidth = '';
      const gap = parseFloat(getComputedStyle(box).columnGap) || 0;
      const tw = cards[0].getBoundingClientRect().width;
      const visible = Array.prototype.filter.call(cards, function (c) { return !c.hidden; }).length;
      const cap = Math.max(1, Math.floor((box.clientWidth + gap) / (tw + gap)));
      if (visible <= cap || visible > 60) return;
      const per = Math.ceil(visible / Math.ceil(visible / cap));
      box.style.maxWidth = (per * (tw + gap) - gap + 1) + 'px';
      box.style.marginLeft = box.style.marginRight = 'auto';
    });
  }

  /* 国会页按人筛：人物编号 → {seat, party, state, hay}。身份、党派、州取议员名单里那一条，hay 收他在各块里的姓名和职务。 */
  function people(groups) {
    const out = {};
    groups.forEach(function (g) {
      const house = g.filter && g.members.some(function (m) { return m.district !== undefined && m.district !== null; });
      g.members.forEach(function (m) {
        const p = out[m.id] = out[m.id] || { words: [] };
        p.words.push(m.name, m.name_en, m.post);
        if (g.filter) { p.seat = seatOf(m, house); p.party = m.party; p.state = m.state; }
      });
    });
    Object.keys(out).forEach(function (id) { out[id].hay = fold(out[id].words.join(' ')); });
    return out;
  }
  /* 这个人符不符合筛选条件 f（{q, seat, party, state}）；不在议员名单里的人没有身份、党派、州，选了这三项就不符合。 */
  function matches(p, f) {
    const q = fold(f.q);
    return !!p && (!f.seat || p.seat === f.seat) && (!f.party || p.party === f.party) && (!f.state || p.state === f.state) && (!q || p.hay.indexOf(q) >= 0);
  }
  ZZB.congress = { people: people, matches: matches }; /* 测试用（web/tests 用 Node 跑） */

  /* 下拉面板放进窗口：身份、党派从按钮左边往右展开，右边放不下就和按钮右对齐；州的面板和按钮右对齐；
     都不出窗口左右两边（各留 12 像素）。 */
  function fit(pop, wide) {
    pop.style.left = pop.style.right = '';
    const box = pop.parentElement.getBoundingClientRect();
    const vw = document.documentElement.clientWidth, w = pop.offsetWidth; /* 版面宽度，不受弹出动画的缩放影响 */
    let x = wide || box.left + w > vw - 12 ? box.right - w : box.left;
    x = Math.max(12, Math.min(x, vw - 12 - w));
    pop.style.left = (x - box.left) + 'px';
    pop.style.right = 'auto';
  }

  ZZB.pages.roster = function () {
    let root, data, f = Object.assign({}, BLANK), E = {}, drops = [], onScroll = null, timer = 0, who = {};
    const filterable = function () { return data.groups.some(function (g) { return g.filter; }); };
    const cont = function (i) { return i > 0 && !!data.groups[i].title && data.groups[i].title === data.groups[i - 1].title; };
    const active = function () { return !!(fold(f.q) || f.seat || f.party || f.state); };

    function apply() {
      const q = fold(f.q);
      const ok = function (p) { return matches(p, f); };
      const shown = {};
      ui.$$('.group', root).forEach(function (sec, i) {
        const g = data.groups[i];
        if (!g) return;
        let n = 0;
        ui.$$('[data-id]', sec).forEach(function (el, k) {
          const id = g.members[k].id, yes = ok(who[id]);
          el.hidden = !yes;
          if (yes) { n++; shown[id] = true; }
        });
        sec.hidden = n === 0;
      });
      const on = active(), count = Object.keys(shown).length;
      E.empty.hidden = count > 0;
      E.count.hidden = E.clear.hidden = !on;
      E.countN.textContent = count;
      E.search.classList.toggle('set', !!q);
      drops.forEach(function (d) { d.mark(); });
      ui.$$('.anchors a', root).forEach(function (a) { const s = root.querySelector('#' + a.dataset.g); a.hidden = !!s && s.hidden; });
      balance(root);
      requestAnimationFrame(spy);
      saved = Object.assign({}, f);
    }

    /* 下拉：按钮写当前选的值，点开列出选项；上下键挑、回车定、Esc 收起，同一时间只开一个。wide 是州的多列面板。 */
    function dropdown(all, values, key, wide) {
      const text = h('span', null, all);
      const btn = h('button', { class: 'pick-btn', type: 'button', 'aria-haspopup': 'listbox', 'aria-expanded': 'false' }, text);
      btn.insertAdjacentHTML('beforeend', ui.ICONS.chev);
      const opts = [''].concat(values).map(function (v, i) {
        return h('button', { type: 'button', role: 'option', class: wide && i === 0 ? 'pick-all' : null, data: { v: v }, on: { click: function () {
          f[key] = v; close(true); apply();
        } } }, v || all);
      });
      /* 面板的类名用 pick-col、pick-wide：别用 list（名单页“只列姓名”的版式，是三列网格） */
      const pop = h('div', { class: 'pick-pop ' + (wide ? 'pick-wide' : 'pick-col'), role: 'listbox', hidden: true },
        wide ? [opts[0], h('div', { class: 'pick-grid' }, opts.slice(1))] : opts);
      function open() {
        drops.forEach(function (d) { d.close(); });
        pop.hidden = false; btn.setAttribute('aria-expanded', 'true');
        fit(pop, wide);
        (pop.querySelector('.on') || opts[0]).focus({ preventScroll: true });
      }
      function close(back) {
        if (pop.hidden) return;
        pop.hidden = true; btn.setAttribute('aria-expanded', 'false');
        if (back) btn.focus({ preventScroll: true });
      }
      btn.addEventListener('click', function () { if (pop.hidden) open(); else close(); });
      btn.addEventListener('keydown', function (e) { if (e.key === 'ArrowDown' && pop.hidden) { e.preventDefault(); open(); } });
      pop.addEventListener('keydown', function (e) {
        const i = opts.indexOf(document.activeElement);
        const cols = wide ? getComputedStyle(pop.lastChild).gridTemplateColumns.split(' ').length : 1;
        let to = null; /* 州面板：上下跨一行，左右逐个；“全部州”在第一行上面 */
        if (e.key === 'ArrowDown') to = wide && i > 0 ? i + cols : i + 1;
        else if (e.key === 'ArrowUp') to = wide ? (i <= cols ? 0 : i - cols) : i - 1;
        else if (wide && e.key === 'ArrowRight') to = i + 1;
        else if (wide && e.key === 'ArrowLeft') to = i - 1;
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
        else if (e.key === 'Tab') close();
        else if (to !== null) { e.preventDefault(); opts[Math.min(opts.length - 1, Math.max(0, to))].focus({ preventScroll: true }); }
      });
      const el = h('div', { class: 'pick' }, btn, pop);
      el.close = close;
      el.mark = function () {
        opts.forEach(function (o) { const on = o.dataset.v === f[key]; o.classList.toggle('on', on); o.setAttribute('aria-selected', on ? 'true' : 'false'); });
        text.textContent = f[key] || all;
        btn.classList.toggle('set', !!f[key]);
      };
      drops.push(el);
      return el;
    }

    function searchBox() {
      let composing = false;
      const input = h('input', { type: 'search', placeholder: '搜索姓名、职务', 'aria-label': '搜索国会议员', spellcheck: 'false', autocomplete: 'off', value: f.q });
      const x = h('button', { class: 'csearch-x', type: 'button', 'aria-label': '清空搜索', on: { click: function () { input.value = f.q = ''; apply(); input.focus(); } } });
      x.insertAdjacentHTML('beforeend', ui.ICONS.close);
      const later = function () { clearTimeout(timer); timer = setTimeout(apply, 90); };
      input.addEventListener('compositionstart', function () { composing = true; });
      input.addEventListener('compositionend', function () { composing = false; f.q = input.value; later(); });
      input.addEventListener('input', function () { if (!composing) { f.q = input.value; later(); } });
      input.addEventListener('keydown', function (e) { if (e.key === 'Escape' && input.value) { e.preventDefault(); e.stopPropagation(); input.value = f.q = ''; apply(); } });
      E.search = h('label', { class: 'csearch' }, input, x);
      E.search.insertAdjacentHTML('afterbegin', '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/></svg>');
      E.input = input;
      return E.search;
    }

    function toolbar() {
      const anchors = h('div', { class: 'anchors' }, data.groups.map(function (g, i) {
        return g.title && !cont(i) ? h('a', { href: '#g-' + i, data: { g: 'g-' + i }, on: { click: function (e) {
          e.preventDefault(); const t = root.querySelector('#g-' + i); if (t) ctxMain().scrollTo({ top: t.offsetTop - 64, behavior: ui.reduce.matches ? 'auto' : 'smooth' });
        } } }, g.title) : null;
      }));
      /* 州按名单里的顺序（名单按州名英文字母排，无表决权代表所在的属地列在最后） */
      const states = [];
      data.groups.forEach(function (g) { if (g.filter) g.members.forEach(function (m) { if (m.state && states.indexOf(m.state) < 0) states.push(m.state); }); });
      E.countN = h('b', null, '0');
      E.count = h('span', { class: 'ccount', hidden: true, 'aria-live': 'polite' }, '共', E.countN, '人');
      E.clear = h('button', { class: 'clear-f', type: 'button', hidden: true, on: { click: function () {
        f = Object.assign({}, BLANK); E.input.value = ''; apply();
      } } }, '清除筛选');
      E.tool = h('div', { class: 'ctool' }, h('div', { class: 'ctool-in' }, anchors, searchBox(),
        dropdown('全部身份', SEATS, 'seat'), dropdown('全部党派', PARTIES, 'party'), dropdown('全部州', states, 'state', true), E.count, E.clear));
      document.addEventListener('mousedown', E.outside = function (e) { drops.forEach(function (d) { if (!d.contains(e.target)) d.close(); }); });
      return E.tool;
    }
    function ctxMain() { return document.getElementById('main'); }

    function spy() {
      const main = ctxMain();
      const secs = ui.$$('.group', root).filter(function (s) { return !s.hidden; });
      const y = main.scrollTop + 90;
      let cur = secs[0];
      secs.forEach(function (s) { if (s.offsetTop <= y) cur = s; });
      ui.$$('.anchors a', root).forEach(function (a) { a.classList.toggle('on', !!cur && a.dataset.g === cur.id); });
      E.tool.classList.toggle('stuck', E.tool.getBoundingClientRect().top <= main.getBoundingClientRect().top + 0.5 && main.scrollTop > 4);
    }

    return {
      load: function (ctx) { return api.roster(ctx.arg); },
      render: function (r, d, ctx) {
        root = r; data = d;
        ctx.gi = 0;
        const dense = filterable();
        const wrap = h('div', { class: 'roster' + (dense ? ' cong' : '') });
        if (dense) {
          f = Object.assign({}, saved);
          who = people(data.groups);
          root.appendChild(toolbar());
        }
        data.groups.forEach(function (g, i) { wrap.appendChild(group(g, ctx, dense && g.filter, cont(i))); });
        if (dense) { E.empty = h('p', { class: 'c-empty', hidden: true }, '没有符合条件的议员'); wrap.appendChild(E.empty); }
        root.appendChild(wrap);
        balance(root);
        window.addEventListener('resize', E.resize = function () { requestAnimationFrame(function () { balance(root); }); });
        if (dense) {
          apply();
          onScroll = function () { requestAnimationFrame(spy); };
          ctxMain().addEventListener('scroll', onScroll, { passive: true });
          requestAnimationFrame(spy);
        }
      },
      unmount: function () {
        clearTimeout(timer);
        if (onScroll) ctxMain().removeEventListener('scroll', onScroll);
        if (E.outside) document.removeEventListener('mousedown', E.outside);
        if (E.resize) window.removeEventListener('resize', E.resize);
      }
    };
  };
})();
