'use strict';
/* 商店版本机目录的测试：node --test test/（仓库的 python -m unittest 也会调，见 tests/test_desktop_shell.py）。 */

const test = require('node:test');
const assert = require('node:assert/strict');
const store = require('../lib/store');

const PKG = 'C:\\Program Files\\WindowsApps\\YueluTech.Zuzhibu_1.0.0.0_x64__8wekyb3d8bbwe';

test('从装在包里的程序路径认出包系列名', () => {
  assert.equal(store.packageFamilyName(`${PKG}\\app\\AI政见·组织部.exe`), 'YueluTech.Zuzhibu_8wekyb3d8bbwe');
  assert.equal(store.packageFamilyName('D:\\WindowsApps\\12345Yuelu.AI-Zuzhibu_2.3.0.0_x64_zh-cn_abcdefghjkmnp\\app\\a.exe'),
    '12345Yuelu.AI-Zuzhibu_abcdefghjkmnp', '装在别的盘、带资源编号的也认');
});

test('不是装在包里的不认', () => {
  for (const bad of [
    'C:\\Users\\u\\AppData\\Local\\Programs\\zuzhibu-desktop\\AI政见·组织部.exe', // 下载版
    'D:\\repo\\dist\\win-unpacked\\AI政见·组织部.exe', // 解包目录
    `${PKG}\\AI政见·组织部.exe`, // 不在 app\ 下
    'C:\\Program Files\\WindowsApps\\Bad_Name_1.0.0.0_x64__8wekyb3d8bbwe\\app\\a.exe', // 名称里有下划线
    'C:\\Program Files\\WindowsApps\\YueluTech.Zuzhibu_1.0.0_x64__8wekyb3d8bbwe\\app\\a.exe', // 版本不是四段
    'C:\\Program Files\\WindowsApps\\YueluTech.Zuzhibu_1.0.0.0_x64__8WEKYB3D8BBWE\\app\\a.exe', // 发布者编号是小写
    '', null, undefined,
  ]) {
    assert.equal(store.packageFamilyName(bad), null, String(bad));
  }
});

test('商店版的本机目录在包自己的数据目录里', () => {
  assert.equal(store.storeHome(`${PKG}\\app\\AI政见·组织部.exe`, 'C:\\Users\\u\\AppData\\Local'),
    'C:\\Users\\u\\AppData\\Local\\Packages\\YueluTech.Zuzhibu_8wekyb3d8bbwe\\LocalState');
  assert.equal(store.storeHome('D:\\repo\\dist\\win-unpacked\\a.exe', 'C:\\Users\\u\\AppData\\Local'), null);
  assert.equal(store.storeHome(`${PKG}\\app\\a.exe`, ''), null, '没有 LOCALAPPDATA 就不猜');
});
