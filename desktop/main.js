const { app, BrowserWindow, dialog, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, execSync } = require('child_process');
const net = require('net');
const treeKill = require('tree-kill');

// ============================================================
// Path resolution
// ============================================================

function isDev() {
  return !app.isPackaged;
}

function getPythonPath() {
  if (isDev()) {
    const vendorPython = path.join(__dirname, 'vendor', 'python', 'python.exe');
    if (fs.existsSync(vendorPython)) return vendorPython;
    return 'python';
  }
  return path.join(process.resourcesPath, 'vendor', 'python', 'python.exe');
}

function getFfmpegDir() {
  if (isDev()) {
    const vendorFfmpeg = path.join(__dirname, 'vendor', 'ffmpeg');
    if (fs.existsSync(vendorFfmpeg)) return vendorFfmpeg;
    return null;
  }
  return path.join(process.resourcesPath, 'vendor', 'ffmpeg');
}

function getAppPath() {
  if (isDev()) {
    return path.join(__dirname, '..');
  }
  return path.join(process.resourcesPath, 'app');
}

function getUserDataDir() {
  return path.join(app.getPath('documents'), 'WhatsArch');
}

function getModelsDir() {
  return path.join(app.getPath('appData'), 'WhatsArch', 'models');
}

function getLogsDir() {
  return path.join(app.getPath('appData'), 'WhatsArch', 'logs');
}

// ============================================================
// Globals
// ============================================================

let mainWindow = null;
let setupWindow = null;
let tray = null;
let flaskProcess = null;
let flaskPort = 5000;
let isQuitting = false;

// ============================================================
// Port finder
// ============================================================

function findFreePort(startPort) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(startPort, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on('error', () => {
      resolve(findFreePort(startPort + 1));
    });
  });
}

// ============================================================
// Directory setup
// ============================================================

function ensureDirectories() {
  [getUserDataDir(), path.join(getUserDataDir(), 'chats'), getModelsDir(), getLogsDir()]
    .forEach(dir => fs.mkdirSync(dir, { recursive: true }));
}

function setupJunctions() {
  const appDir = getAppPath();
  const userChatsDir = path.join(getUserDataDir(), 'chats');
  const chatsLink = path.join(appDir, 'chats');

  try {
    if (!fs.existsSync(chatsLink)) {
      fs.symlinkSync(userChatsDir, chatsLink, 'junction');
    }
  } catch (err) {
    console.error('Junction creation failed:', err.message);
  }

  // .env symlink
  const userEnvPath = path.join(getUserDataDir(), '.env');
  const appEnvPath = path.join(appDir, '.env');

  if (!fs.existsSync(userEnvPath)) {
    fs.writeFileSync(userEnvPath, '# WhatsArch API Keys\n', 'utf-8');
  }

  try {
    if (!fs.existsSync(appEnvPath)) {
      fs.symlinkSync(userEnvPath, appEnvPath, 'file');
    }
  } catch (err) {
    try { fs.copyFileSync(userEnvPath, appEnvPath); } catch (e) { /* ignore */ }
  }
}

// ============================================================
// Model checking & download
// ============================================================

function checkModelsExist() {
  const hfDir = path.join(getModelsDir(), 'hub');
  if (!fs.existsSync(hfDir)) return { whisper: false, e5: false };

  let whisper = false, e5 = false;
  try {
    for (const entry of fs.readdirSync(hfDir)) {
      if (entry.toLowerCase().includes('whisper')) whisper = true;
      if (entry.includes('multilingual-e5') || entry.includes('e5-large')) e5 = true;
    }
  } catch (err) { /* empty */ }
  return { whisper, e5 };
}

function downloadModels(win) {
  return new Promise((resolve) => {
    const pythonPath = getPythonPath();
    const modelsDir = getModelsDir();
    const env = { ...process.env, HF_HOME: modelsDir, XDG_CACHE_HOME: modelsDir, PYTHONIOENCODING: 'utf-8' };

    const sendProgress = (step, status, message, percent) => {
      if (win && !win.isDestroyed()) {
        win.webContents.send('setup-progress', { step, status, message, percent });
      }
    };

    sendProgress('whisper', 'downloading', 'מוריד מודל זיהוי דיבור (Whisper)...', 0);

    const whisperScript = [
      `import sys, os`,
      `os.environ['HF_HOME'] = r'${modelsDir.replace(/\\/g, '\\\\')}'`,
      `print("STEP:whisper:start", flush=True)`,
      `from faster_whisper import WhisperModel`,
      `print("STEP:whisper:loading", flush=True)`,
      `model = WhisperModel("small", device="cpu", compute_type="int8")`,
      `print("STEP:whisper:done", flush=True)`,
    ].join('\n');

    const whisperProc = spawn(pythonPath, ['-c', whisperScript], { env, windowsHide: true });

    whisperProc.stdout.on('data', (data) => {
      const text = data.toString();
      if (text.includes('STEP:whisper:loading')) sendProgress('whisper', 'downloading', 'טוען מודל Whisper...', 50);
      if (text.includes('STEP:whisper:done')) sendProgress('whisper', 'done', 'מודל Whisper הותקן', 100);
    });

    whisperProc.stderr.on('data', (data) => {
      const m = data.toString().match(/(\d+)%/);
      if (m) sendProgress('whisper', 'downloading', `מוריד Whisper... ${m[1]}%`, parseInt(m[1]));
    });

    whisperProc.on('close', () => {
      sendProgress('e5', 'downloading', 'מוריד מודל חיפוש חכם (E5-large)...', 0);

      const e5Script = [
        `import sys, os`,
        `os.environ['HF_HOME'] = r'${modelsDir.replace(/\\/g, '\\\\')}'`,
        `print("STEP:e5:start", flush=True)`,
        `from sentence_transformers import SentenceTransformer`,
        `print("STEP:e5:loading", flush=True)`,
        `model = SentenceTransformer("intfloat/multilingual-e5-large")`,
        `print("STEP:e5:done", flush=True)`,
      ].join('\n');

      const e5Proc = spawn(pythonPath, ['-c', e5Script], { env, windowsHide: true });

      e5Proc.stdout.on('data', (data) => {
        const text = data.toString();
        if (text.includes('STEP:e5:loading')) sendProgress('e5', 'downloading', 'טוען מודל E5-large...', 50);
        if (text.includes('STEP:e5:done')) sendProgress('e5', 'done', 'מודל E5-large הותקן', 100);
      });

      e5Proc.stderr.on('data', (data) => {
        const m = data.toString().match(/(\d+)%/);
        if (m) sendProgress('e5', 'downloading', `מוריד E5-large... ${m[1]}%`, parseInt(m[1]));
      });

      e5Proc.on('close', () => {
        if (win && !win.isDestroyed()) win.webContents.send('setup-complete', {});
        setTimeout(resolve, 1500);
      });
    });
  });
}

// ============================================================
// Flask process management
// ============================================================

function startFlask(port) {
  return new Promise((resolve, reject) => {
    const pythonPath = getPythonPath();
    const appDir = getAppPath();
    const runScript = path.join(appDir, 'run.py');
    const ffmpegDir = getFfmpegDir();
    const modelsDir = getModelsDir();
    const logsDir = getLogsDir();

    const env = { ...process.env };
    if (ffmpegDir && fs.existsSync(ffmpegDir)) {
      env.PATH = ffmpegDir + ';' + (env.PATH || '');
    }
    env.HF_HOME = modelsDir;
    env.XDG_CACHE_HOME = modelsDir;
    env.PYTHONIOENCODING = 'utf-8';

    const args = [runScript, '--port', String(port), '--no-browser',
      '--skip-transcribe', '--skip-vision', '--skip-embeddings', '--skip-chunking'];

    console.log(`Starting Flask: ${pythonPath} ${args.join(' ')}`);

    flaskProcess = spawn(pythonPath, args, { env, cwd: appDir, windowsHide: true });

    const logFile = path.join(logsDir, `flask-${Date.now()}.log`);
    const logStream = fs.createWriteStream(logFile, { flags: 'a' });

    let started = false;
    const timeout = setTimeout(() => {
      if (!started) reject(new Error('Flask did not start within 30 seconds'));
    }, 30000);

    function checkStarted(text) {
      if (!started && (text.includes('Server running at') || text.includes('Running on'))) {
        started = true;
        clearTimeout(timeout);
        setTimeout(() => resolve(port), 500);
      }
    }

    flaskProcess.stdout.on('data', (data) => {
      const text = data.toString();
      logStream.write(text);
      checkStarted(text);
    });

    flaskProcess.stderr.on('data', (data) => {
      const text = data.toString();
      logStream.write('[STDERR] ' + text);
      checkStarted(text);
    });

    flaskProcess.on('error', (err) => {
      clearTimeout(timeout);
      logStream.write('[ERROR] ' + err.message + '\n');
      reject(err);
    });

    flaskProcess.on('close', (code) => {
      logStream.write(`[EXIT] code ${code}\n`);
      logStream.end();
      flaskProcess = null;
      if (!isQuitting && mainWindow) {
        dialog.showErrorBox('WhatsArch', 'השרת נכבה באופן לא צפוי. האפליקציה תיסגר.');
        app.quit();
      }
    });
  });
}

function stopFlask() {
  return new Promise((resolve) => {
    if (!flaskProcess) return resolve();
    const pid = flaskProcess.pid;
    treeKill(pid, 'SIGTERM', (err) => {
      if (err) {
        try { execSync(`taskkill /pid ${pid} /T /F`, { windowsHide: true }); } catch (e) { /* dead */ }
      }
      flaskProcess = null;
      resolve();
    });
  });
}

// ============================================================
// Window creation
// ============================================================

function createSetupWindow() {
  setupWindow = new BrowserWindow({
    width: 500, height: 420, resizable: false, frame: false,
    backgroundColor: '#0D9488',
    icon: path.join(__dirname, 'icons', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'setup-preload.js'),
      contextIsolation: true, nodeIntegration: false,
    },
  });
  setupWindow.loadFile(path.join(__dirname, 'setup.html'));
  setupWindow.setMenuBarVisibility(false);
  setupWindow.on('closed', () => { setupWindow = null; });
  return setupWindow;
}

function createMainWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1200, height: 800, minWidth: 800, minHeight: 600,
    backgroundColor: '#FAFAF8',
    icon: path.join(__dirname, 'icons', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false,
    },
  });
  mainWindow.loadURL(`http://localhost:${port}`);
  mainWindow.setMenuBarVisibility(false);
  mainWindow.on('close', (e) => {
    if (!isQuitting) { e.preventDefault(); mainWindow.hide(); }
  });
  mainWindow.on('closed', () => { mainWindow = null; });
  return mainWindow;
}

// ============================================================
// Tray
// ============================================================

function createTray() {
  const iconPath = path.join(__dirname, 'icons', 'icon.png');
  let trayIcon;
  if (fs.existsSync(iconPath)) {
    trayIcon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 });
  } else {
    trayIcon = nativeImage.createEmpty();
  }

  tray = new Tray(trayIcon);
  tray.setToolTip('WhatsArch');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'פתח WhatsArch', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } } },
    { type: 'separator' },
    { label: 'יציאה', click: () => { isQuitting = true; app.quit(); } },
  ]));
  tray.on('double-click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
}

// ============================================================
// App lifecycle
// ============================================================

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.on('ready', async () => {
    try {
      ensureDirectories();

      // Check if models need downloading
      const models = checkModelsExist();
      if (!models.whisper || !models.e5) {
        const win = createSetupWindow();
        try { await downloadModels(win); } catch (err) { console.error('Model download:', err); }
        if (setupWindow) setupWindow.close();
      }

      // Setup junctions in production
      if (!isDev()) setupJunctions();

      // Find free port and start Flask
      flaskPort = await findFreePort(5000);
      console.log(`Using port: ${flaskPort}`);
      await startFlask(flaskPort);
      console.log('Flask started');

      createMainWindow(flaskPort);
      createTray();
    } catch (err) {
      console.error('Startup error:', err);
      dialog.showErrorBox('WhatsArch - שגיאה',
        `לא ניתן להפעיל את האפליקציה:\n${err.message}\n\nודא ש-Python מותקן כראוי.`);
      app.quit();
    }
  });

  app.on('before-quit', async (e) => {
    isQuitting = true;
    if (flaskProcess) {
      e.preventDefault();
      await stopFlask();
      app.quit();
    }
  });

  app.on('window-all-closed', () => { /* keep running in tray */ });
}
