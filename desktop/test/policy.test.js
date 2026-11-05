'use strict';
/* 外壳规则的测试：node --test test/（仓库的 python -m unittest 也会调，见 tests/test_desktop_shell.py）。 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const policy = require('../lib/policy');

test('从本机服务的输出里认出地址', () => {
  assert.equal(policy.serviceUrlFrom('AI政见·组织部：http://127.0.0.1:51234/（Ctrl+C 退出）'), 'http://127.0.0.1:51234/');
  assert.equal(policy.serviceUrlFrom('正在铺底…'), null);
  assert.equal(policy.serviceUrlFrom('http://127.0.0.1:0/'), null);
  assert.equal(policy.serviceUrlFrom('http://evil.example:80/'), null, '只认 127.0.0.1');
});

test('窗口里只开本机服务和外壳自带的页面', () => {
  const base = 'http://127.0.0.1:51234/';
  assert.ok(policy.isServicePage('http://127.0.0.1:51234/#/me', base));
  assert.ok(!policy.isServicePage('http://127.0.0.1:51235/', base), '别的端口不算');
  assert.ok(!policy.isServicePage('http://localhost:51234/', base));
  assert.ok(!policy.isServicePage('https://github.com/', base));
  const pages = path.join(__dirname, '..', 'pages');
  assert.ok(policy.isShellPage(pathToFileURL(path.join(pages, 'error.html')).href + '?retry=1', pages));
  assert.ok(!policy.isShellPage(pathToFileURL(path.join(__dirname, 'x.html')).href, pages), '外壳目录以外的本地文件不算');
  assert.ok(!policy.isShellPage('https://example.com/pages/error.html', pages));
});

test('交给系统浏览器的只有 http、https', () => {
  assert.ok(policy.isWebUrl('https://www.whitehouse.gov/'));
  assert.ok(policy.isWebUrl('http://example.com/a?b=1'));
  for (const bad of ['file:///C:/Windows/system32/calc.exe', 'javascript:alert(1)', 'ms-settings:privacy', 'mailto:a@b.c', '', 'not a url', 'https://']) {
    assert.ok(!policy.isWebUrl(bad), bad);
  }
});

test('标题栏配色只收合规的值', () => {
  assert.deepEqual(policy.overlayOptions({ color: '#C7161E', symbolColor: '#fff', height: 48 }),
    { color: '#C7161E', symbolColor: '#fff', height: 48 });
  assert.deepEqual(policy.overlayOptions({ color: 'red', symbolColor: '#FFFFFF80', height: 500 }), { symbolColor: '#FFFFFF80' });
  assert.deepEqual(policy.overlayOptions({ height: 0 }), { height: 0 });
  assert.equal(policy.overlayOptions({ color: 'url(x)', height: 4.5 }), null);
  assert.equal(policy.overlayOptions(null), null);
});

test('窗口位置：落在屏幕上才恢复，否则居中用默认大小', () => {
  const size = { width: 1320, height: 900, minWidth: 420, minHeight: 560 };
  const screens = [{ x: 0, y: 0, width: 1920, height: 1040 }];
  assert.deepEqual(policy.restoreBounds({ x: 100, y: 50, width: 1200, height: 800 }, screens, size), { x: 100, y: 50, width: 1200, height: 800 });
  assert.deepEqual(policy.restoreBounds({ x: 5000, y: 50, width: 1200, height: 800 }, screens, size), { width: 1200, height: 800 }, '拔掉的副屏上');
  assert.deepEqual(policy.restoreBounds({ x: 100, y: -300, width: 1200, height: 800 }, screens, size), { width: 1200, height: 800 }, '标题栏跑到屏幕上方');
  assert.deepEqual(policy.restoreBounds({ x: 0, y: 0, width: 100, height: 100 }, screens, size), { width: 1320, height: 900 }, '小于最小尺寸');
  assert.deepEqual(policy.restoreBounds(null, screens, size), { width: 1320, height: 900 });
});
