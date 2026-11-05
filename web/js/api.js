/* 数据接口层：只走本机主程序的 /api/…（zuzhibu/server.py）。本文件是前端唯一的网络出口。
   结果按地址缓存在内存里，切换页面不重复请求；数据更新后调用 reset() 清缓存。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});

  const API = {
    base: 'api',
    cache: {},
    reset() { this.cache = {}; },
    get(path) {
      if (!this.cache[path]) this.cache[path] = fetchJSON(this.base + path).catch((e) => { delete this.cache[path]; throw e; });
      return this.cache[path];
    },
    post(path, body) { return fetchJSON(this.base + path, { method: 'POST', body: body || {} }); },
    app() { return this.get('/app'); },
    roster(id) { return this.get('/rosters/' + encodeURIComponent(id)); },
    index() { return this.get('/index'); },
    profile(id) { return this.get('/profile/' + encodeURIComponent(id)); },
    news() { return this.get('/news'); },
    article(id) { return this.get('/news/' + encodeURIComponent(id)); },
    about() { return this.get('/about'); },
    license() { return this.get('/license'); },
    /* 设置：大模型接口（保存、检测）、更新人事动态（状态、开始、停止） */
    settings() { return fetchJSON(this.base + '/settings'); },
    saveSettings(body) { return this.post('/settings', body); },
    checkApi() { return this.post('/settings/check', {}); },
    updateState() { return fetchJSON(this.base + '/update'); },
    startUpdate() { return this.post('/update', {}); },
    stopUpdate() { return this.post('/update/stop', {}); },
    /* 关窗退出：页面在时每分钟报一次到，关掉时说一声 */
    ping(client) { return fetchJSON(this.base + '/ping?client=' + encodeURIComponent(client), { timeout: 8000 }); },
    bye(client) {
      try {
        fetch(this.base + '/bye', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client: client }) });
      } catch (e) { /* 页面正在关闭，失败也无妨 */ }
    },
    prefetch(id) { this.profile(id).catch(function () { /* 预取失败不提示 */ }); }
  };

  function fetchJSON(url, opts) {
    opts = opts || {};
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl && opts.timeout ? setTimeout(function () { ctrl.abort(); }, opts.timeout) : 0;
    const init = { method: opts.method || 'GET', headers: { Accept: 'application/json' }, cache: 'no-store' };
    if (ctrl) init.signal = ctrl.signal;
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    return fetch(url, init).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { const err = new Error((data && data.error) || ('HTTP ' + res.status)); err.status = res.status; throw err; }
        return data;
      });
    }).finally(function () { if (timer) clearTimeout(timer); });
  }

  ZZB.api = API;
})();
