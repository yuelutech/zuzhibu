'use strict';
/* 外壳的规则：哪些地址在窗口里开、哪些交给系统浏览器，标题栏配色的取值，窗口位置的恢复，本机服务地址的识别。
   不依赖 Electron，单独测试（desktop/test/policy.test.js）。 */

const { pathToFileURL } = require('node:url');

/** 本机服务的地址：python -m zuzhibu 启动后在输出里打出的 http://127.0.0.1:<端口>/ */
function serviceUrlFrom(line) {
  const m = /http:\/\/127\.0\.0\.1:(\d{2,5})\//.exec(String(line || ''));
  if (!m) return null;
  const port = Number(m[1]);
  return port > 0 && port < 65536 ? `http://127.0.0.1:${port}/` : null;
}

function parse(url) {
  try {
    return new URL(String(url));
  } catch {
    return null;
  }
}

/** 本机服务的页面：同一个 127.0.0.1 端口。 */
function isServicePage(url, serviceUrl) {
  const u = parse(url);
  const base = parse(serviceUrl);
  return Boolean(u && base && u.origin === base.origin);
}

/** 外壳自带的页面（启动中、出错页）：pages 目录下的本地文件。 */
function isShellPage(url, pagesDir) {
  const u = parse(url);
  if (!u || u.protocol !== 'file:') return false;
  const root = pathToFileURL(pagesDir.endsWith('/') || pagesDir.endsWith('\\') ? pagesDir : pagesDir + '/').href;
  return u.href.toLowerCase().startsWith(root.toLowerCase());
}

/** 只有 http、https 能交给系统浏览器；file:、javascript:、自定义协议一概不放行。 */
function isWebUrl(url) {
  const u = parse(url);
  return Boolean(u && (u.protocol === 'http:' || u.protocol === 'https:') && u.hostname);
}

const COLOR = /^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

/** 页面设标题栏配色：颜色只收 #rgb、#rrggbb、#rrggbbaa，高度 0–120 的整数；不合规的项不改，全不合规返回 null。 */
function overlayOptions(opts) {
  if (!opts || typeof opts !== 'object') return null;
  const out = {};
  if (typeof opts.color === 'string' && COLOR.test(opts.color.trim())) out.color = opts.color.trim();
  if (typeof opts.symbolColor === 'string' && COLOR.test(opts.symbolColor.trim())) out.symbolColor = opts.symbolColor.trim();
  const h = Number(opts.height);
  if (opts.height !== undefined && opts.height !== null && Number.isInteger(h) && h >= 0 && h <= 120) out.height = h;
  return Object.keys(out).length ? out : null;
}

/** 恢复窗口位置：上次的位置还有一大半落在某块屏幕的工作区里才用，否则居中用默认大小。 */
function restoreBounds(saved, workAreas, defaults) {
  const def = { width: defaults.width, height: defaults.height };
  if (!saved || typeof saved !== 'object') return def;
  const w = Math.round(Number(saved.width));
  const h = Math.round(Number(saved.height));
  if (!(w >= defaults.minWidth && h >= defaults.minHeight)) return def;
  const out = { width: w, height: h };
  const x = Math.round(Number(saved.x));
  const y = Math.round(Number(saved.y));
  if (!Number.isFinite(x) || !Number.isFinite(y)) return out;
  const visible = (workAreas || []).some((a) => {
    const ix = Math.max(0, Math.min(x + w, a.x + a.width) - Math.max(x, a.x));
    const iy = Math.max(0, Math.min(y + h, a.y + a.height) - Math.max(y, a.y));
    return ix * iy >= 0.5 * w * h && y >= a.y - 8;
  });
  return visible ? { ...out, x, y } : out;
}

module.exports = { serviceUrlFrom, isServicePage, isShellPage, isWebUrl, overlayOptions, restoreBounds };
