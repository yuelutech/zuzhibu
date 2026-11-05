'use strict';
/* 启动时崩溃自救的规则测试（lib/recovery.js）。 */

const test = require('node:test');
const assert = require('node:assert/strict');
const recovery = require('../lib/recovery');

test('连着几次没打开：先清显卡缓存，再关显卡加速，都试过就不再重开', () => {
  assert.deepEqual(recovery.plan(0), { clearCache: false, gpuOff: false, saveGpuOff: false, retry: true });
  assert.deepEqual(recovery.plan(1), { clearCache: true, gpuOff: false, saveGpuOff: false, retry: true });
  assert.deepEqual(recovery.plan(2), { clearCache: true, gpuOff: true, saveGpuOff: true, retry: false });
  assert.deepEqual(recovery.plan(5), { clearCache: true, gpuOff: true, saveGpuOff: true, retry: false });
});

test('本机设置已关显卡加速：照关，不重复写；再崩重开一次，还崩就不再重开', () => {
  assert.deepEqual(recovery.plan(0, 'off'), { clearCache: false, gpuOff: true, saveGpuOff: false, retry: true });
  assert.deepEqual(recovery.plan(1, 'off'), { clearCache: true, gpuOff: true, saveGpuOff: false, retry: false });
  assert.deepEqual(recovery.plan(2, 'off'), { clearCache: true, gpuOff: true, saveGpuOff: false, retry: false });
});

test('本机设置写明开着、或环境变量指定时照办', () => {
  assert.equal(recovery.plan(3, 'on').gpuOff, false, '设置里写了 "gpu": "on" 就不替使用者关');
  assert.equal(recovery.plan(3, 'on').retry, false);
  assert.equal(recovery.plan(0, '', 'off').gpuOff, true);
  assert.equal(recovery.plan(2, '', 'off').saveGpuOff, false, '环境变量只管这一次，不写进设置');
  assert.equal(recovery.plan(2, 'off', 'ON').gpuOff, false);
});

test('读本机设置的显卡加速', () => {
  assert.equal(recovery.gpuSetting('{"gpu": "off"}'), 'off');
  assert.equal(recovery.gpuSetting('\uFEFF{"python": "x", "gpu": false}'), 'off');
  assert.equal(recovery.gpuSetting('{"gpu": "on"}'), 'on');
  for (const text of ['', '{}', '{"gpu": "maybe"}', 'not json', '[1]', null, undefined]) {
    assert.equal(recovery.gpuSetting(text), '', String(text));
  }
});

test('“关闭显卡加速”并进本机设置，别的项原样保留', () => {
  assert.deepEqual(JSON.parse(recovery.withGpuOff('')), { gpu: 'off' });
  assert.deepEqual(JSON.parse(recovery.withGpuOff('\uFEFF{"python": "C:\\\\py\\\\python.exe", "fonts": "D:\\\\f"}')),
    { python: 'C:\\py\\python.exe', fonts: 'D:\\f', gpu: 'off' });
  assert.deepEqual(JSON.parse(recovery.withGpuOff('{"gpu": "on", "library": "x"}')), { gpu: 'off', library: 'x' });
  assert.ok(recovery.withGpuOff('{}').endsWith('\n'));
  assert.equal(recovery.withGpuOff('{"python": '), null, '坏了的本机设置不动');
  assert.equal(recovery.withGpuOff('[]'), null);
});

test('读启动记录', () => {
  assert.equal(recovery.failures('{"failed": 2}'), 2);
  assert.equal(recovery.failures('{"failed": 40}'), 9);
  for (const text of ['', '{}', '{"failed": -1}', '{"failed": "2"}', '{"failed": 1.5}', 'x']) {
    assert.equal(recovery.failures(text), 0, text);
  }
});

test('写日志：下一次重开做什么', () => {
  assert.equal(recovery.nextRemedy(0), '清掉显卡缓存');
  assert.equal(recovery.nextRemedy(1), '关闭显卡加速');
});

test('启动以后页面进程崩溃：一分钟内最多重新加载两次', () => {
  const now = 1_000_000;
  assert.ok(recovery.reloadAllowed([], now));
  assert.ok(recovery.reloadAllowed([now - 5000], now));
  assert.ok(!recovery.reloadAllowed([now - 5000, now - 1000], now));
  assert.ok(recovery.reloadAllowed([now - 120_000, now - 61_000], now), '一分钟以前的不算');
});

test('正常退出不算崩溃', () => {
  assert.ok(!recovery.isCrash({ reason: 'clean-exit' }));
  for (const reason of ['crashed', 'abnormal-exit', 'killed', 'oom', 'launch-failed', 'integrity-failure']) {
    assert.ok(recovery.isCrash({ reason }), reason);
  }
  assert.ok(!recovery.isCrash(null));
});

test('要清的显卡缓存都在用户数据目录的第一层', () => {
  for (const name of recovery.GPU_CACHES) assert.match(name, /^[A-Za-z]+$/);
  assert.ok(recovery.GPU_CACHES.includes('GPUCache') && recovery.GPU_CACHES.includes('GrShaderCache'));
});
