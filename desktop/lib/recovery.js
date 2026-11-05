'use strict';
/* 启动时崩溃的自救（desktop/README.md“启动时崩溃”）。main.js 只管执行，规则都在这里，node --test 测。
   外壳每次启动先在本机目录记一笔“这次还没打开”（shell\startup.json），程序页面或出错页打开了、或正常退出了再清掉：
   - 上次没打开（启动阶段页面进程或显卡进程崩溃，或整个程序被结束）：这次先清掉显卡缓存；
   - 连着两次没打开：关掉显卡加速，并记进本机设置（local.json 的 "gpu": "off"），以后都这样开；
   - 关了显卡加速还打不开：不再自动重开，告诉使用者。
   环境变量 ZUZHIBU_GPU=on|off 优先于这些（排查用）。 */

// Chromium 放在用户数据目录里的显卡缓存
const GPU_CACHES = ['GPUCache', 'GrShaderCache', 'ShaderCache', 'DawnGraphiteCache', 'DawnWebGPUCache', 'GPUPersistentCache'];

function parseObject(text) {
  const raw = String(text || '').replace(/^﻿/, '').trim();
  if (!raw) return {};
  try {
    const data = JSON.parse(raw);
    return data && typeof data === 'object' && !Array.isArray(data) ? data : null;
  } catch {
    return null;
  }
}

/** 本机设置（local.json 的内容）里的显卡加速：'off'、'on'，没设或读不了是 ''。 */
function gpuSetting(text) {
  const data = parseObject(text);
  const value = data ? data.gpu : undefined;
  if (value === false || value === 'off') return 'off';
  if (value === true || value === 'on') return 'on';
  return '';
}

/** 把 "gpu": "off" 并进本机设置，别的项原样保留；原文不是 JSON 对象时返回 null（不动它）。 */
function withGpuOff(text) {
  const data = parseObject(text);
  return data ? JSON.stringify({ ...data, gpu: 'off' }, null, 2) + '\n' : null;
}

/** 启动记录（startup.json 的内容）：上次起连着几次没打开。 */
function failures(text) {
  const data = parseObject(text);
  const n = data ? data.failed : 0;
  return Number.isInteger(n) && n > 0 ? Math.min(n, 9) : 0;
}

/** 这次怎么开。prior：连着几次没打开；setting：本机设置的显卡加速；env：环境变量 ZUZHIBU_GPU。
    retry：这次启动时再崩，下次还有没有新办法（清缓存、关显卡加速都试过了就没有）。 */
function plan(prior, setting = '', env = '') {
  const force = String(env || '').trim().toLowerCase();
  const gpuOff = force !== 'on' && (force === 'off' || setting === 'off' || (prior >= 2 && setting !== 'on'));
  return {
    clearCache: prior >= 1,
    gpuOff,
    saveGpuOff: gpuOff && prior >= 2 && setting === '' && force === '',
    retry: prior === 0 || (prior === 1 && !gpuOff),
  };
}

/** 再开一次时做什么（写日志用）：下一次是第几次没打开。 */
function nextRemedy(prior) {
  return prior === 0 ? '清掉显卡缓存' : '关闭显卡加速';
}

/** 启动以后页面进程崩了：一分钟内最多重新加载两次，再崩就按启动失败处理。times：之前几次崩溃的时间（毫秒）。 */
function reloadAllowed(times, now, limit = 2, windowMs = 60 * 1000) {
  return times.filter((t) => now - t < windowMs).length < limit;
}

/** 进程结束算不算崩溃：正常退出不算。 */
function isCrash(details) {
  return Boolean(details) && details.reason !== 'clean-exit';
}

module.exports = { GPU_CACHES, gpuSetting, withGpuOff, failures, plan, nextRemedy, reloadAllowed, isCrash };
