'use strict';
/* AI政见·组织部 桌面外壳（docs/INSTALL.md、desktop/README.md）。
   - 启动内置的 Python 本地服务（python -m zuzhibu --no-window --port 0），读出它的地址，等它答应了再在窗口里打开；
   - 退出时结束本地服务（服务也认外壳的进程号，外壳意外崩溃时自己退出）；
   - 只开一个实例，记住窗口大小和位置，服务起不来时给出错页；
   - 启动时页面进程或显卡进程崩溃：清掉显卡缓存重开一次，还崩就关掉显卡加速重开并记进本机设置（lib/recovery.js）；
   - 窗口用系统画的最小化、最大化、关闭按钮（titleBarOverlay），配色由页面经 preload 设置；
   - 外链一律交给系统浏览器；页面拿不到 Node，只有 preload 暴露的 window.shell。
   安装版：resources/runtime/python.exe，程序和附带数据在 resources/pyapp，本机目录 %LOCALAPPDATA%\zuzhibu。
   商店版（微软应用商店的 MSIX 包）：本机目录放包自己的数据目录 %LOCALAPPDATA%\Packages\<包系列名>\LocalState（lib/store.js）。
   从仓库运行：npm start，或设 ZUZHIBU_REPO=仓库目录；Python 取 ZUZHIBU_PYTHON、本机设置 local.json 的 python，再不然 PATH 里的 python。 */

const { app, BrowserWindow, dialog, ipcMain, shell, screen, session, Menu } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const policy = require('./lib/policy');
const recovery = require('./lib/recovery');
const store = require('./lib/store');

const TITLE = 'AI政见·组织部';
const PAGES = path.join(__dirname, 'pages');
const REPO = process.env.ZUZHIBU_REPO || (app.isPackaged ? '' : path.resolve(__dirname, '..'));
const HOME = process.env.ZUZHIBU_HOME || (process.windowsStore && store.storeHome(process.execPath, process.env.LOCALAPPDATA))
  || path.join(process.env.LOCALAPPDATA || app.getPath('appData'), 'zuzhibu');
const LOGS = path.join(HOME, 'logs');
const TEST_DIR = process.env.ZUZHIBU_SHELL_TEST || ''; // 试装用：窗口不显示，写就绪报告，见到 quit 文件就退出
const OVERLAY = { color: '#B8141C', symbolColor: '#FFFFFF', height: 48 }; // 和界面标题栏同色同高（web/css/app.css）
const SIZE = { width: 1320, height: 900, minWidth: 420, minHeight: 560 };
const START_TIMEOUT = 90 * 1000; // 首次运行要先把附带数据铺进本机目录
const START_GRACE = 5 * 1000; // 程序页面打开后这么久之内崩溃，仍算启动时崩溃
const LOAD_TIMEOUT = 30 * 1000; // “正在打开”页这么久还没加载出来，按启动时崩溃处理

// 本机目录：窗口状态和浏览器配置放 %LOCALAPPDATA%\zuzhibu\shell（从仓库运行时 shell-dev，和安装版互不干扰）
app.setPath('userData', path.join(HOME, REPO ? 'shell-dev' : 'shell'));

let win = null;
let service = null; // { child, url }
let quitting = false;

// 启动时崩溃的自救（lib/recovery.js）：本机设置 local.json 记显卡加速，shell\startup.json 记连着几次没打开
const LOCAL_SETTINGS = path.join(process.env.APPDATA || app.getPath('appData'), 'zuzhibu', 'local.json');
const STARTUP_FILE = path.join(app.getPath('userData'), 'startup.json');
const PRIOR = recovery.failures(readText(STARTUP_FILE));
const PLAN = recovery.plan(PRIOR, recovery.gpuSetting(readText(LOCAL_SETTINGS)), process.env.ZUZHIBU_GPU);
// 试装用：ZUZHIBU_SHELL_TEST_CRASH="renderer,gpu" 让第 1、2 次启动分别在启动阶段弄崩页面进程、显卡进程
const TEST_CRASH = TEST_DIR ? String(process.env.ZUZHIBU_SHELL_TEST_CRASH || '').split(',').map((s) => s.trim()) : [];
let starting = true; // 启动阶段：到程序页面（或出错页）打开后 START_GRACE 为止
let opened = false;
let recovering = false;
let testLaunch = 0;
const pageCrashes = []; // 启动以后页面进程崩溃的时间

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return '';
  }
}

// ──────────────────────────────────────────────── 日志

function logFile(name) {
  fs.mkdirSync(LOGS, { recursive: true });
  const file = path.join(LOGS, name);
  try {
    if (fs.statSync(file).size > 1024 * 1024) fs.renameSync(file, file + '.1');
  } catch { /* 还没有 */ }
  return file;
}

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function note(text) {
  try {
    fs.appendFileSync(logFile('shell.log'), `${stamp()} ${text}\n`, 'utf8');
  } catch { /* 写不了日志不影响使用 */ }
}

// ──────────────────────────────────────────────── 本地服务

function pythonForRepo() {
  if (process.env.ZUZHIBU_PYTHON) return process.env.ZUZHIBU_PYTHON;
  try {
    const conf = path.join(process.env.APPDATA || '', 'zuzhibu', 'local.json');
    const data = JSON.parse(fs.readFileSync(conf, 'utf8').replace(/^﻿/, ''));
    if (data && data.python) return String(data.python);
  } catch { /* 没有本机设置 */ }
  return 'python';
}

function serviceCommand() {
  if (REPO) return { exe: pythonForRepo(), cwd: REPO };
  return { exe: path.join(process.resourcesPath, 'runtime', 'python.exe'), cwd: path.join(process.resourcesPath, 'pyapp') };
}

function healthy(url, timeoutMs) {
  return new Promise((resolve) => {
    const req = http.get(url + 'api/health', { timeout: timeoutMs }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        try { resolve(res.statusCode === 200 && JSON.parse(body).ok === true); } catch { resolve(false); }
      });
    });
    req.on('timeout', () => req.destroy());
    req.on('error', () => resolve(false));
  });
}

function startService() {
  return new Promise((resolve, reject) => {
    const cmd = serviceCommand();
    const args = ['-X', 'utf8', '-m', 'zuzhibu', '--no-window', '--port', '0', '--home', HOME, '--parent-pid', String(process.pid)];
    const log = fs.createWriteStream(logFile('service.log'), { flags: 'a' });
    log.on('error', () => { /* 写不了日志不影响使用 */ });
    log.write(`\n—— ${stamp()} 外壳启动本地服务：${cmd.exe} ${args.join(' ')}（目录 ${cmd.cwd}）\n`);
    let child;
    try {
      // ZUZHIBU_HOME：Python 那边没拿到 --home 的地方（立即更新的子进程等）也认同一个本机目录（商店版不是默认位置）
      child = spawn(cmd.exe, args, { cwd: cmd.cwd, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, PYTHONUNBUFFERED: '1', ZUZHIBU_HOME: HOME } });
    } catch (err) {
      log.end();
      reject(new Error(`没能启动 Python：${err.message}`));
      return;
    }
    let settled = false;
    let buffer = '';
    const finish = (err, url) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (err) {
        if (child.exitCode === null) child.kill();
        reject(err);
      } else {
        resolve({ child, url });
      }
    };
    const timer = setTimeout(() => finish(new Error('本机服务长时间没有就绪')), START_TIMEOUT);
    child.stdout.on('data', (chunk) => {
      log.write(chunk);
      if (settled) return;
      buffer = (buffer + chunk.toString('utf8')).slice(-4096);
      const url = policy.serviceUrlFrom(buffer);
      if (url) {
        buffer = '';
        waitHealthy(url, child).then((ok) => (ok ? finish(null, url) : finish(new Error('本机服务没有应答'))));
      }
    });
    child.stderr.on('data', (chunk) => log.write(chunk));
    child.on('error', (err) => finish(new Error(`没能启动 Python：${err.message}`)));
    child.on('exit', (code) => {
      finish(new Error(`本机服务启动失败（退出码 ${code}）`));
      if (service && service.child === child) onServiceLost(code);
    });
    child.on('close', (code) => log.end(`—— ${stamp()} 本地服务退出，退出码 ${code}\n`));
  });
}

async function waitHealthy(url, child) {
  const deadline = Date.now() + 30 * 1000;
  while (Date.now() < deadline && child.exitCode === null) {
    if (await healthy(url, 2000)) return true;
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

function stopService() {
  if (service && service.child.exitCode === null) service.child.kill();
  service = null;
}

function onServiceLost(code) {
  service = null;
  if (quitting || !win || win.isDestroyed()) return;
  note(`本地服务意外退出（退出码 ${code}）`);
  showError('本机服务意外退出了。');
}

async function openApp() {
  if (!win || win.isDestroyed()) return;
  stopService();
  // “正在打开”页和本地服务同时开始：页面进程出了事，不耽误服务启动
  const loading = win.loadFile(path.join(PAGES, 'loading.html')).catch((err) => note(`“正在打开”页没有加载：${err.message}`));
  let started;
  try {
    started = await startService();
  } catch (err) {
    note(`本地服务没有起来：${err.message}`);
    await loading;
    showError(err.message);
    return;
  }
  if (quitting || !win || win.isDestroyed()) {
    started.child.kill();
    return;
  }
  service = started;
  note(`本地服务就绪：${service.url}`);
  await loading;
  if (win && !win.isDestroyed() && !quitting) {
    win.loadURL(service.url).catch((err) => note(`程序页面没有加载：${err.message}`));
  }
}

function showError(reason) {
  if (!win || win.isDestroyed()) return;
  win.loadFile(path.join(PAGES, 'error.html'), { query: { reason, log: path.join(LOGS, 'service.log') } })
    .catch((err) => note(`出错页没有加载：${err.message}`));
}

// ──────────────────────────────────────────────── 启动时崩溃的自救

function writeFailures(n) {
  try {
    if (n > 0) {
      fs.mkdirSync(path.dirname(STARTUP_FILE), { recursive: true });
      fs.writeFileSync(STARTUP_FILE, JSON.stringify({ failed: n }), 'utf8');
    } else {
      fs.rmSync(STARTUP_FILE, { force: true });
    }
  } catch { /* 记不下来，下次照常启动 */ }
}

function clearGpuCaches() {
  const removed = [];
  for (const name of recovery.GPU_CACHES) {
    const dir = path.join(app.getPath('userData'), name);
    if (!fs.existsSync(dir)) continue;
    try {
      fs.rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
      removed.push(name);
    } catch (err) {
      note(`没能清掉显卡缓存 ${name}：${err.message}`);
    }
  }
  note(`上次没有打开：清掉显卡缓存${removed.length ? `（${removed.join('、')}）` : '（本来就没有）'}`);
}

function saveGpuOff() {
  const merged = recovery.withGpuOff(readText(LOCAL_SETTINGS));
  if (merged === null) {
    note(`本机设置 ${LOCAL_SETTINGS} 不是有效的 JSON，没有写入；这次照样关闭显卡加速`);
    return;
  }
  try {
    fs.mkdirSync(path.dirname(LOCAL_SETTINGS), { recursive: true });
    const tmp = `${LOCAL_SETTINGS}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, merged, 'utf8');
    fs.renameSync(tmp, LOCAL_SETTINGS);
    note(`连着两次没有打开：关闭显卡加速，已记进本机设置 ${LOCAL_SETTINGS}（"gpu": "off"，删掉这一项即恢复）`);
  } catch (err) {
    note(`没能写本机设置：${err.message}；这次照样关闭显卡加速`);
  }
}

// 拿到“只开一个实例”以后、app ready 以前调用：清缓存、关显卡加速都要在显卡进程起来以前做
function prepareLaunch() {
  note(`外壳启动 ${app.getVersion()}${REPO ? `（从仓库运行：${REPO}）` : ''}${PRIOR ? `，上次没有打开（连着 ${PRIOR} 次）` : ''}`
    + `${PLAN.gpuOff ? '，关闭显卡加速' : ''}`);
  if (PLAN.clearCache) clearGpuCaches();
  if (PLAN.saveGpuOff) saveGpuOff();
  if (PLAN.gpuOff) app.disableHardwareAcceleration();
  writeFailures(PRIOR + 1); // 程序页面或出错页打开了、或正常退出了再清掉
  if (TEST_DIR) recordTestLaunch();
}

function markOpened() {
  if (opened) return;
  opened = true;
  writeFailures(0);
  setTimeout(() => { starting = false; }, START_GRACE);
}

function onCrash(what, details) {
  if (!recovery.isCrash(details)) return;
  note(`${what}异常结束：${details.reason}${details.exitCode === undefined ? '' : `（退出码 ${details.exitCode}）`}`);
  if (quitting || recovering) return;
  if (starting) {
    restartAfterCrash(`启动时${what}崩溃`);
    return;
  }
  if (what !== '页面进程') return; // 显卡进程崩了，Chromium 会自己重开
  if (!recovery.reloadAllowed(pageCrashes, Date.now())) {
    restartAfterCrash('页面进程一分钟内崩溃三次');
    return;
  }
  pageCrashes.push(Date.now());
  if (win && !win.isDestroyed()) win.webContents.reload();
}

// app.exit 偶尔会卡住（试装时见过一次：页面进程刚崩、还有命令在等它回复），卡住了自己重开的那份也起不来：
// 3 秒还没退出就强行结束自己（重开由 app.relaunch 留下的助手进程在本进程结束后接着做）
function exitNow(code = 0) {
  setTimeout(() => {
    note('退出 3 秒还没完成，强行结束');
    process.kill(process.pid);
  }, 3000);
  app.exit(code);
}

// app.relaunch 先开一个助手进程，助手认准本进程、等它退出后再开新的一份。助手只等它 1 秒来认人，电脑忙时它起得慢，
// 本进程已经退了，它就可能认错（进程号被别的程序用了）而一直等。所以开了助手再留 2 秒（窗口先藏起来）才退出
const RELAUNCH_GRACE = 2 * 1000;
const RELAUNCHED = '--zuzhibu-relaunched';

function restartAfterCrash(reason) {
  if (!PLAN.retry) {
    giveUp(reason);
    return;
  }
  recovering = true;
  quitting = true;
  writeFailures(PRIOR + 1);
  note(`${reason}：${recovery.nextRemedy(PRIOR)}后重新打开`);
  stopService();
  if (win && !win.isDestroyed()) win.hide();
  app.relaunch({ args: process.argv.slice(1).filter((a) => a !== RELAUNCHED).concat(RELAUNCHED) });
  setTimeout(() => exitNow(), RELAUNCH_GRACE);
}

// 清过显卡缓存、关着显卡加速还打不开：不再自动重开（免得窗口反复崩），说一句人话，细节在日志里
function giveUp(reason) {
  recovering = true;
  quitting = true;
  note(`${reason}：清过显卡缓存、关着显卡加速仍打不开，不再自动重新打开`);
  writeFailures(0);
  stopService();
  if (TEST_DIR || !app.isReady()) {
    if (TEST_DIR) fs.writeFileSync(path.join(TEST_DIR, 'gave-up'), stamp(), 'utf8');
    exitNow();
    return;
  }
  const choice = dialog.showMessageBoxSync({ type: 'warning', title: TITLE, message: '软件没能打开', detail: '请重新启动电脑后再打开。',
    buttons: ['确定', '打开日志所在文件夹'], defaultId: 0, cancelId: 0, noLink: true });
  if (choice === 1) shell.openPath(LOGS).finally(() => exitNow());
  else exitNow();
}

// ──────────────────────────────────────────────── 窗口

function stateFile() {
  return path.join(app.getPath('userData'), 'window-state.json');
}

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(stateFile(), 'utf8'));
  } catch {
    return null;
  }
}

function saveState() {
  if (!win || win.isDestroyed()) return;
  const b = win.getNormalBounds();
  const data = { x: b.x, y: b.y, width: b.width, height: b.height, maximized: win.isMaximized() };
  try {
    fs.mkdirSync(path.dirname(stateFile()), { recursive: true });
    fs.writeFileSync(stateFile(), JSON.stringify(data), 'utf8');
  } catch { /* 记不住位置不影响使用 */ }
}

function isOurs(url) {
  return policy.isShellPage(url, PAGES) || Boolean(service && policy.isServicePage(url, service.url));
}

function openOutside(url) {
  if (policy.isWebUrl(url)) shell.openExternal(url);
}

function createWindow() {
  const saved = loadState();
  const workAreas = screen.getAllDisplays().map((d) => d.workArea);
  const bounds = policy.restoreBounds(saved, workAreas, SIZE);
  win = new BrowserWindow({
    ...bounds,
    minWidth: SIZE.minWidth,
    minHeight: SIZE.minHeight,
    title: TITLE,
    show: false,
    skipTaskbar: Boolean(TEST_DIR),
    backgroundColor: '#F6F4EF',
    titleBarStyle: 'hidden',
    titleBarOverlay: OVERLAY,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
      additionalArguments: [`--zuzhibu-version=${app.getVersion()}`],
    },
  });
  if (saved && saved.maximized) win.maximize();
  win.once('ready-to-show', () => { if (!TEST_DIR) win.show(); });

  const contents = win.webContents;
  contents.setWindowOpenHandler(({ url }) => {
    openOutside(url);
    return { action: 'deny' };
  });
  const guard = (event, legacyUrl) => {
    const url = (event && event.url) || legacyUrl || '';
    if (policy.isShellPage(url, PAGES)) {
      const q = new URL(url).searchParams;
      if (q.has('retry')) { event.preventDefault(); openApp(); return; }
      if (q.has('logs')) { event.preventDefault(); shell.showItemInFolder(path.join(LOGS, 'service.log')); return; }
      return;
    }
    if (service && policy.isServicePage(url, service.url)) return;
    event.preventDefault();
    openOutside(url);
  };
  contents.on('will-navigate', guard);
  contents.on('will-redirect', guard);
  contents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    if (input.key === 'F5' || (input.control && input.key.toLowerCase() === 'r')) { contents.reload(); event.preventDefault(); }
    if ((REPO || process.env.ZUZHIBU_DEVTOOLS) && (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i'))) {
      contents.toggleDevTools();
      event.preventDefault();
    }
  });
  contents.on('render-process-gone', (_e, details) => onCrash('页面进程', details));
  contents.on('did-finish-load', () => {
    const url = contents.getURL();
    if (service && policy.isServicePage(url, service.url)) {
      markOpened();
      if (TEST_DIR) writeTestReport();
    } else if (policy.isShellPage(url, PAGES) && url.split('?')[0].endsWith('/error.html')) {
      markOpened();
    }
  });
  const watchdog = setTimeout(() => {
    if (!quitting) restartAfterCrash(`“正在打开”页 ${LOAD_TIMEOUT / 1000} 秒没有加载出来`);
  }, LOAD_TIMEOUT);
  contents.once('did-finish-load', () => clearTimeout(watchdog));
  if (TEST_DIR) injectTestCrash(contents);

  let timer = null;
  const later = () => { clearTimeout(timer); timer = setTimeout(saveState, 500); };
  win.on('resize', later);
  win.on('move', later);
  win.on('close', saveState);
  win.on('closed', () => { win = null; });
  openApp();
}

// ──────────────────────────────────────────────── 页面接口（preload 只暴露 window.shell 的四项）

function fromOurPage(event) {
  const frame = event.senderFrame;
  return Boolean(frame && isOurs(frame.url));
}

ipcMain.handle('shell:open-external', (event, url) => {
  if (!fromOurPage(event) || !policy.isWebUrl(url)) return false;
  shell.openExternal(String(url));
  return true;
});

ipcMain.handle('shell:title-bar-overlay', (event, opts) => {
  const options = fromOurPage(event) ? policy.overlayOptions(opts) : null;
  const target = BrowserWindow.fromWebContents(event.sender);
  if (!options || !target || target.isDestroyed()) return false;
  target.setTitleBarOverlay(options);
  return true;
});

// ──────────────────────────────────────────────── 试装（后台验证）

function writeTestReport() {
  const report = { url: service.url, pid: process.pid, servicePid: service.child.pid, title: win.getTitle(), version: app.getVersion(),
    visible: win.isVisible(), gpuOff: PLAN.gpuOff, gpu: app.getGPUFeatureStatus().gpu_compositing, prior: PRIOR, launch: testLaunch };
  fs.writeFileSync(path.join(TEST_DIR, 'ready.json'), JSON.stringify(report), 'utf8');
}

function recordTestLaunch() {
  const file = path.join(TEST_DIR, 'launches.log');
  testLaunch = readText(file).split('\n').filter(Boolean).length;
  fs.appendFileSync(file, `${stamp()} pid=${process.pid} prior=${PRIOR} gpu=${PLAN.gpuOff ? 'off' : 'on'} clear=${PLAN.clearCache ? 'yes' : 'no'}\n`,
    'utf8');
}

// ZUZHIBU_SHELL_TEST_CRASH 的第 testLaunch 项：在“正在打开”页加载完时弄崩页面进程（renderer）或显卡进程（gpu），
// 用开发者协议的 Page.crash、Browser.crashGpuProcess，都是真崩溃。窗口不显示时显卡进程有时迟迟不起来，
// 这条命令就落空：3 秒内没崩成，就按显卡进程崩溃处理（只走外壳这一侧）
async function devtools(contents, method) {
  note(`试装：${method}`);
  try {
    if (!contents.debugger.isAttached()) contents.debugger.attach('1.3');
    await contents.debugger.sendCommand(method);
  } catch (err) {
    note(`试装：${method} 返回 ${err.message}`); // 页面进程崩了，这条命令自然收不到回复
  }
}

function injectTestCrash(contents) {
  const kind = TEST_CRASH[testLaunch];
  if (kind === 'renderer') contents.once('did-finish-load', () => devtools(contents, 'Page.crash'));
  if (kind !== 'gpu') return;
  contents.once('did-finish-load', async () => {
    for (let i = 0; i < 6 && !quitting && !recovering; i += 1) {
      if (app.getAppMetrics().some((m) => m.type === 'GPU')) await devtools(contents, 'Browser.crashGpuProcess');
      await new Promise((r) => setTimeout(r, 500));
    }
    if (quitting || recovering) return;
    note('试装：显卡进程没有崩成，按显卡进程崩溃处理');
    app.emit('child-process-gone', {}, { type: 'GPU', reason: 'crashed', exitCode: -1 });
  });
}

function watchTestQuit() {
  setInterval(() => {
    if (fs.existsSync(path.join(TEST_DIR, 'quit'))) app.quit();
  }, 500);
}

// ──────────────────────────────────────────────── 启动与退出

if (!app.requestSingleInstanceLock()) {
  if (process.argv.includes(RELAUNCHED)) note('自己重开的这一份没拿到“只开一个实例”：上一份还没退出');
  app.exit(0);
} else {
  prepareLaunch();
  app.on('child-process-gone', (_e, details) => {
    if (details.type === 'GPU') onCrash('显卡进程', details);
  });
  app.on('second-instance', () => {
    if (TEST_DIR) fs.writeFileSync(path.join(TEST_DIR, 'second-instance'), stamp(), 'utf8');
    if (!win) return;
    if (win.isMinimized()) win.restore();
    if (!TEST_DIR) {
      win.show();
      win.focus();
    }
  });

  app.on('web-contents-created', (_e, contents) => {
    contents.on('will-attach-webview', (event) => event.preventDefault());
  });

  app.whenReady().then(() => {
    Menu.setApplicationMenu(null); // 无菜单栏；刷新 F5，开发时 F12 开发者工具
    const ses = session.defaultSession;
    ses.setPermissionRequestHandler((_wc, permission, callback, details) => {
      callback(permission === 'clipboard-sanitized-write' && isOurs(details.requestingUrl || ''));
    });
    ses.setPermissionCheckHandler((_wc, permission, origin) => permission === 'clipboard-sanitized-write' && isOurs(origin || ''));
    createWindow();
    if (TEST_DIR) watchTestQuit();
  });

  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => {
    quitting = true;
    if (!recovering) writeFailures(0); // 使用者自己关掉的，不算没打开
    stopService();
  });
}
