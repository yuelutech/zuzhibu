'use strict';
/* 商店版（微软应用商店的 MSIX 包）的本机目录。不依赖 Electron，单独测试（desktop/test/store.test.js）。
   商店版装在 WindowsApps\<包全名>\app\ 下，Electron 认得出（process.windowsStore）。系统把商店版在 AppData 下新建的文件
   另存到包的私有位置，改已有的文件却改在原处：和下载版共用 %LOCALAPPDATA%\zuzhibu 会两边错乱（一边换了数据快照、删了旧的，
   另一边还指着旧的），资源管理器也看不到私有位置，“打开日志所在文件夹”打不开。所以商店版的本机目录放包自己的数据目录
   %LOCALAPPDATA%\Packages\<包系列名>\LocalState：不另存、资源管理器打得开、卸载时随包删掉。 */

const path = require('node:path');

// 包全名：名称_版本_架构_资源编号_发布者编号（资源编号多半是空的，所以常见两个下划线连着）
const FULL_NAME = /^([A-Za-z0-9.-]{3,50})_(\d+\.\d+\.\d+\.\d+)_([A-Za-z0-9]+)_([A-Za-z0-9.~-]*)_([a-z0-9]{13})$/;

/** 程序路径 …\<包全名>\app\<程序>.exe → 包系列名 名称_发布者编号；不是装在包里的返回 null。 */
function packageFamilyName(execPath) {
  const dir = path.win32.dirname(String(execPath || ''));
  if (path.win32.basename(dir).toLowerCase() !== 'app') return null;
  const m = FULL_NAME.exec(path.win32.basename(path.win32.dirname(dir)));
  return m ? `${m[1]}_${m[5]}` : null;
}

/** 商店版的本机目录 %LOCALAPPDATA%\Packages\<包系列名>\LocalState；认不出包系列名返回 null，外壳照常用 %LOCALAPPDATA%\zuzhibu。 */
function storeHome(execPath, localAppData) {
  const family = packageFamilyName(execPath);
  return family && localAppData ? path.win32.join(localAppData, 'Packages', family, 'LocalState') : null;
}

module.exports = { packageFamilyName, storeHome };
