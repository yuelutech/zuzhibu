'use strict';
/* 页面能用的外壳接口只有 window.shell 这四项（contextIsolation 开、nodeIntegration 关、沙箱运行）：
   version          外壳版本号
   platform         系统（win32 等）
   openExternal(u)  用系统浏览器打开 http、https 链接（别的协议不放行），返回 Promise<boolean>
   setTitleBarOverlay({ color, symbolColor, height })
                    设右上角系统按钮区的底色、按钮颜色（#rgb、#rrggbb、#rrggbbaa）和高度（0–120），返回 Promise<boolean> */

const { contextBridge, ipcRenderer } = require('electron');

const flag = '--zuzhibu-version=';
const version = (process.argv.find((a) => a.startsWith(flag)) || '').slice(flag.length);

contextBridge.exposeInMainWorld('shell', Object.freeze({
  version,
  platform: process.platform,
  openExternal: (url) => ipcRenderer.invoke('shell:open-external', String(url)),
  setTitleBarOverlay: (opts) => {
    const o = opts && typeof opts === 'object' ? opts : {};
    return ipcRenderer.invoke('shell:title-bar-overlay', { color: o.color, symbolColor: o.symbolColor, height: o.height });
  },
}));
