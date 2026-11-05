/* 设置：大模型接口、更新人事动态。文字照维护者 2026-10-21 审定的文案稿，不要改字。
   接口（zuzhibu/server.py）：GET/POST /api/settings 读、存接口设置；POST /api/settings/check 检测，返回的 message 就是文案里的检测结果；
   GET /api/update 更新状态（state、step、percent、text 当前一步的提示语、message 结束后的提示语、plan 能不能开更新）；
   POST /api/update 开始；POST /api/update/stop 停止。提示语由后端按文案生成，这里直接显示。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, api = ZZB.api, h = ui.h;

  const PROVIDERS = [['openai', 'OpenAI 兼容接口'], ['anthropic', 'Anthropic 接口']];
  const CHECK_OK = '接口可用，支持联网检索。';
  const CHECK_BAD = '接口不可用：密钥无效或无权限，请核对后重试。';
  /* 进度条下方逐步显示的六步；已走过的几步只写步名，当前一步用后端给的提示语（带计数） */
  const STEPS = ['正在连接接口……', '正在查询公开信息……', '正在深度思考……', '正在逐条核实……', '正在获取简历……', '正在更新简历……'];

  function row(label, control, id, hint) {
    return h('div', { class: 'd-row' }, h('label', id ? { for: id } : null, label), h('div', { class: 'd-ctl' }, control, hint ? h('div', { class: 'd-tip' }, hint) : null));
  }
  function checkText(res) { return res.code || res.message && /^接口|^该接口/.test(res.message) ? res.message : (res.ok ? CHECK_OK : CHECK_BAD); }

  function apiSection() {
    const sec = ZZB.panels.section('大模型接口');
    sec.appendChild(h('p', { class: 'd-lede' }, '填写本人的大模型接口后，可随时更新人事动态，所用额度由本人账户承担。不填写，也可查看每月更新的资料。'));
    const body = h('div');
    sec.appendChild(body);
    api.settings().catch(function () { return { api: {}, providers: [] }; }).then(function (st) {
      const a = st.api || {}, byId = {};
      (st.providers || []).forEach(function (p) { byId[p.id] = p; });
      const f = {
        provider: h('select', { class: 'sel-inp', id: 'f-provider' }, PROVIDERS.map(function (p) { return h('option', { value: p[0], selected: (a.provider || 'openai') === p[0] }, p[1]); })),
        base_url: h('input', { class: 'inp', id: 'f-base', type: 'url', value: a.base_url || '', autocomplete: 'off', spellcheck: 'false' }),
        model: h('input', { class: 'inp', id: 'f-model', type: 'text', value: a.model || '', autocomplete: 'off', spellcheck: 'false' }),
        api_key: h('input', { class: 'inp', id: 'f-key', type: 'password', value: a.api_key || '', autocomplete: 'off' })
      };
      function placeholders() { const p = byId[f.provider.value] || {}; f.base_url.placeholder = p.base_url || ''; f.model.placeholder = p.model || ''; }
      f.provider.addEventListener('change', placeholders);
      placeholders();
      const payload = function () { return { api: { provider: f.provider.value, base_url: f.base_url.value, model: f.model.value, api_key: f.api_key.value } }; };
      const status = h('span', { class: 'status' });
      function show(res) { status.className = 'status' + (res.ok ? ' ok' : ' bad'); status.textContent = checkText(res); }
      if (st.check) show(st.check);
      const save = h('button', { class: 'btn ghost', type: 'button', on: { click: async function () {
        try { const res = await api.saveSettings(payload()); if (res.cleared_key) f.api_key.value = ''; ui.say('已保存'); } catch (e) { ui.say(e.message, 4000); }
      } } }, '保存');
      const test = h('button', { class: 'btn', type: 'button', on: { click: async function () {
        test.disabled = true; status.className = 'status'; status.textContent = '';
        try { await api.saveSettings(payload()); show(await api.checkApi()); } catch (e) { show({ ok: false }); }
        test.disabled = false;
      } } }, '检测接口');
      body.append(row('接口类型', f.provider, 'f-provider'), row('接口地址', f.base_url, 'f-base'), row('模型名称', f.model, 'f-model'),
        row('密钥', f.api_key, 'f-key', '只保存在本机'),
        h('div', { class: 'btns' }, h('span', { class: 'btns-msg' }, status), save, test));
    });
    return sec;
  }

  function updateSection() {
    const sec = ZZB.panels.section('更新人事动态');
    const btn = h('button', { class: 'btn', type: 'button' }, '立即更新');
    const why = h('span', { class: 'status' });
    const bar = h('div', { class: 'pbar', hidden: true }, h('i'));
    const steps = h('ol', { class: 'steps', hidden: true });
    const result = h('p', { class: 'status result', hidden: true });
    sec.append(h('div', { class: 'upd' }, btn, why), bar, steps, result);
    let timer = 0, state = 'idle';

    function render(st) {
      state = st.state || (st.running ? 'running' : 'idle');
      const active = state === 'running' || state === 'stopping';
      const plan = st.plan || {};
      btn.textContent = active ? '停止更新' : '立即更新';
      btn.classList.toggle('ghost', active);
      btn.disabled = state === 'stopping' || (!active && plan.ok === false);
      why.textContent = !active && plan.ok === false ? (plan.reason || '') : '';
      const step = Math.max(0, Math.min(6, Number(st.step) || 0));
      bar.hidden = !active && !step;
      bar.classList.toggle('run', active);
      bar.firstChild.style.width = Math.max(0, Math.min(100, Number(st.percent) || 0)) + '%';
      ui.clear(steps);
      for (let i = 1; i <= step; i++) {
        const cur = active && i === step;
        steps.appendChild(h('li', { class: cur ? 'cur' : 'done' }, h('span', { class: 'mk', 'aria-hidden': 'true' }), cur && st.text ? st.text : STEPS[i - 1]));
      }
      steps.hidden = !step || !active;
      const msg = active ? '' : (st.message || '');
      result.hidden = !msg; result.textContent = msg;
      result.className = 'status result' + (state === 'done' ? ' ok' : '');
    }
    async function refresh() {
      let st;
      try { st = await api.updateState(); } catch (e) { return; }
      render(st);
      clearTimeout(timer);
      if ((st.state || (st.running ? 'running' : '')) === 'running' || st.state === 'stopping') timer = setTimeout(refresh, 1000);
    }
    btn.addEventListener('click', async function () {
      const active = state === 'running' || state === 'stopping';
      btn.disabled = true;
      try {
        const res = active ? await api.stopUpdate() : await api.startUpdate();
        if (res && res.ok === false && res.message) why.textContent = res.message;
      } catch (e) { ui.say(e.message, 4000); }
      refresh();
    });
    refresh();
    return { el: sec, stop: function () { clearTimeout(timer); } };
  }

  ZZB.panels.register('settings', function () {
    const body = h('div', { class: 'd-body' });
    const upd = updateSection();
    body.append(apiSection(), upd.el);
    return { el: ZZB.panels.dialog('设置', 'settings', body), stop: upd.stop };
  });
})();
