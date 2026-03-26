const { app, BrowserWindow, dialog, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, execSync, execFileSync } = require('child_process');
const net = require('net');
const https = require('https');
const http = require('http');
const treeKill = require('tree-kill');

// ============================================================
// Platform
// ============================================================

const isMac = process.platform === 'darwin';
const isWin = process.platform === 'win32';
const PATH_SEP = isMac ? ':' : ';';

// ============================================================
// Paths
// ============================================================

function isDev() { return !app.isPackaged; }

function getAppPath() {
  return isDev() ? path.join(__dirname, '..') : path.join(process.resourcesPath, 'app');
}

function getUserDataDir() {
  return path.join(app.getPath('documents'), 'WhatsArch');
}

function getLocalDir() {
  // Where we store downloaded tools (Python, ffmpeg)
  return path.join(app.getPath('appData'), 'WhatsArch');
}

function getModelsDir() {
  return path.join(getLocalDir(), 'models');
}

function getLogsDir() {
  return path.join(getLocalDir(), 'logs');
}

function getPythonDir() {
  return path.join(getLocalDir(), 'python');
}

function getFfmpegDir() {
  return path.join(getLocalDir(), 'ffmpeg');
}

// ============================================================
// Tool detection: check what's already installed
// ============================================================

function detectPython() {
  // Check our local install first
  const localBin = isMac
    ? path.join(getPythonDir(), 'bin', 'python3')
    : path.join(getPythonDir(), 'python.exe');
  if (fs.existsSync(localBin)) return { found: true, path: localBin, source: 'local' };

  // Check system Python
  const cmds = isMac ? ['python3', 'python'] : ['python', 'python3'];
  for (const cmd of cmds) {
    try {
      const ver = execSync(`${cmd} --version`, { stdio: 'pipe', windowsHide: true }).toString().trim();
      const match = ver.match(/Python (\d+)\.(\d+)/);
      if (match && parseInt(match[1]) >= 3 && parseInt(match[2]) >= 10) {
        return { found: true, path: cmd, source: 'system', version: ver };
      }
    } catch (e) { /* not found */ }
  }
  return { found: false };
}

function detectFfmpeg() {
  // Check local
  const localBin = isMac
    ? path.join(getFfmpegDir(), 'ffmpeg')
    : path.join(getFfmpegDir(), 'ffmpeg.exe');
  if (fs.existsSync(localBin)) return { found: true, path: path.dirname(localBin), source: 'local' };

  // Check system
  try {
    execSync('ffmpeg -version', { stdio: 'pipe', windowsHide: true });
    return { found: true, path: null, source: 'system' };
  } catch (e) {
    return { found: false };
  }
}

function detectOllama() {
  if (isMac && fs.existsSync('/Applications/Ollama.app')) {
    return { found: true, source: 'system' };
  }
  try {
    execSync('ollama --version', { stdio: 'pipe', windowsHide: true });
    return { found: true, source: 'system' };
  } catch (e) {
    return { found: false };
  }
}

function detectPipPackages(pythonPath) {
  try {
    const result = execSync(`"${pythonPath}" -c "import flask; import faster_whisper; import sentence_transformers; print('ok')"`,
      { stdio: 'pipe', windowsHide: true, timeout: 30000 }).toString().trim();
    return result === 'ok';
  } catch (e) {
    return false;
  }
}

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

// ============================================================
// Download helper
// ============================================================

function downloadFile(url, dest, onProgress) {
  return new Promise((resolve, reject) => {
    const getter = url.startsWith('https') ? https : http;
    getter.get(url, { headers: { 'User-Agent': 'WhatsArch/1.0' } }, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        return downloadFile(res.headers.location, dest, onProgress).then(resolve).catch(reject);
      }
      if (res.statusCode !== 200) {
        return reject(new Error(`HTTP ${res.statusCode}`));
      }
      const total = parseInt(res.headers['content-length'] || '0', 10);
      let downloaded = 0;
      const file = fs.createWriteStream(dest);
      res.on('data', (chunk) => {
        downloaded += chunk.length;
        if (total > 0 && onProgress) {
          onProgress(Math.round(downloaded / total * 100), downloaded, total);
        }
      });
      res.pipe(file);
      file.on('finish', () => { file.close(resolve); });
      file.on('error', (err) => { fs.unlinkSync(dest); reject(err); });
    }).on('error', reject);
  });
}

// ============================================================
// Install missing tools
// ============================================================

async function installPython(sendProgress) {
  const pythonDir = getPythonDir();
  fs.mkdirSync(pythonDir, { recursive: true });
  const tmpDir = path.join(getLocalDir(), 'tmp');
  fs.mkdirSync(tmpDir, { recursive: true });

  if (isWin) {
    // Download Python embeddable for Windows
    const url = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip';
    const zipPath = path.join(tmpDir, 'python.zip');
    sendProgress('מוריד Python...', 0);
    await downloadFile(url, zipPath, (pct) => sendProgress(`מוריד Python... ${pct}%`, pct));

    sendProgress('מחלץ Python...', 90);
    execSync(`powershell -Command "Expand-Archive -Force -Path '${zipPath}' -DestinationPath '${pythonDir}'"`, { windowsHide: true });
    fs.unlinkSync(zipPath);

    // Enable site-packages
    const pthFiles = fs.readdirSync(pythonDir).filter(f => f.endsWith('._pth'));
    for (const f of pthFiles) {
      const p = path.join(pythonDir, f);
      let content = fs.readFileSync(p, 'utf-8');
      content = content.replace(/^#\s*import site/m, 'import site');
      if (!content.includes('Lib/site-packages')) content += '\nLib/site-packages\n';
      fs.writeFileSync(p, content);
    }
    fs.mkdirSync(path.join(pythonDir, 'Lib', 'site-packages'), { recursive: true });

    // Install pip
    sendProgress('מתקין pip...', 95);
    const getPipPath = path.join(tmpDir, 'get-pip.py');
    await downloadFile('https://bootstrap.pypa.io/get-pip.py', getPipPath);
    execSync(`"${path.join(pythonDir, 'python.exe')}" "${getPipPath}"`, { stdio: 'pipe', windowsHide: true });
    fs.unlinkSync(getPipPath);
  } else {
    // Download python-build-standalone for Mac
    const arch = process.arch === 'arm64' ? 'aarch64' : 'x86_64';
    const url = `https://github.com/astral-sh/python-build-standalone/releases/download/20250317/cpython-3.11.12+20250317-${arch}-apple-darwin-install_only.tar.gz`;
    const tarPath = path.join(tmpDir, 'python.tar.gz');
    sendProgress('מוריד Python...', 0);
    await downloadFile(url, tarPath, (pct) => sendProgress(`מוריד Python... ${pct}%`, pct));

    sendProgress('מחלץ Python...', 90);
    // Remove existing and extract fresh
    if (fs.existsSync(pythonDir)) fs.rmSync(pythonDir, { recursive: true, force: true });
    fs.mkdirSync(pythonDir, { recursive: true });
    execSync(`tar -xzf "${tarPath}" -C "${getLocalDir()}"`, { stdio: 'pipe' });
    fs.unlinkSync(tarPath);

    // Make executable
    const pyBin = path.join(pythonDir, 'bin', 'python3');
    if (fs.existsSync(pyBin)) {
      try { execSync(`chmod +x "${pyBin}"`, { stdio: 'pipe' }); } catch (e) { /* ok */ }
    }
  }

  // Cleanup tmp
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) { /* ok */ }
  sendProgress('Python מותקן', 100);
}

async function installFfmpeg(sendProgress) {
  const ffmpegDir = getFfmpegDir();
  fs.mkdirSync(ffmpegDir, { recursive: true });
  const tmpDir = path.join(getLocalDir(), 'tmp');
  fs.mkdirSync(tmpDir, { recursive: true });

  if (isWin) {
    const url = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip';
    const zipPath = path.join(tmpDir, 'ffmpeg.zip');
    sendProgress('מוריד ffmpeg...', 0);
    await downloadFile(url, zipPath, (pct) => sendProgress(`מוריד ffmpeg... ${pct}%`, pct));

    sendProgress('מחלץ ffmpeg...', 90);
    const extractDir = path.join(tmpDir, 'ffmpeg-extract');
    execSync(`powershell -Command "Expand-Archive -Force -Path '${zipPath}' -DestinationPath '${extractDir}'"`, { windowsHide: true });

    // Find ffmpeg.exe recursively
    function findFile(dir, name) {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) { const r = findFile(full, name); if (r) return r; }
        else if (e.name === name) return full;
      }
      return null;
    }

    const exe = findFile(extractDir, 'ffmpeg.exe');
    if (exe) fs.copyFileSync(exe, path.join(ffmpegDir, 'ffmpeg.exe'));
    fs.rmSync(extractDir, { recursive: true, force: true });
    fs.unlinkSync(zipPath);
  } else {
    // Mac: evermeet.cx static build (x86_64, works on arm64 via Rosetta)
    const url = 'https://evermeet.cx/ffmpeg/ffmpeg-7.1.1.zip';
    const zipPath = path.join(tmpDir, 'ffmpeg.zip');
    sendProgress('מוריד ffmpeg...', 0);
    await downloadFile(url, zipPath, (pct) => sendProgress(`מוריד ffmpeg... ${pct}%`, pct));

    sendProgress('מחלץ ffmpeg...', 90);
    try {
      execSync(`unzip -o "${zipPath}" -d "${ffmpegDir}"`, { stdio: 'pipe' });
    } catch (e) {
      execSync(`tar -xf "${zipPath}" -C "${ffmpegDir}"`, { stdio: 'pipe' });
    }
    // Make executable
    const bin = path.join(ffmpegDir, 'ffmpeg');
    if (fs.existsSync(bin)) {
      try { execSync(`chmod +x "${bin}"`, { stdio: 'pipe' }); } catch (e) { /* ok */ }
    }
    fs.unlinkSync(zipPath);
  }

  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) { /* ok */ }
  sendProgress('ffmpeg מותקן', 100);
}

async function installOllama(sendProgress) {
  if (isWin) {
    const url = 'https://ollama.com/download/OllamaSetup.exe';
    const tmpDir = path.join(getLocalDir(), 'tmp');
    fs.mkdirSync(tmpDir, { recursive: true });
    const exePath = path.join(tmpDir, 'OllamaSetup.exe');

    sendProgress('מוריד Ollama...', 0);
    await downloadFile(url, exePath, (pct) => sendProgress(`מוריד Ollama... ${pct}%`, pct));

    sendProgress('מתקין Ollama...', 90);
    try {
      execSync(`"${exePath}" /SILENT /NORESTART`, { windowsHide: true, timeout: 180000 });
      sendProgress('Ollama מותקן', 100);
    } catch (e) {
      sendProgress('Ollama - התקנה נכשלה (אופציונלי)', 100);
    }
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) { /* ok */ }
  } else {
    // Mac
    const url = 'https://ollama.com/download/Ollama-darwin.zip';
    const tmpDir = path.join(getLocalDir(), 'tmp');
    fs.mkdirSync(tmpDir, { recursive: true });
    const zipPath = path.join(tmpDir, 'Ollama.zip');

    sendProgress('מוריד Ollama...', 0);
    await downloadFile(url, zipPath, (pct) => sendProgress(`מוריד Ollama... ${pct}%`, pct));

    sendProgress('מתקין Ollama...', 90);
    try {
      execSync(`unzip -o "${zipPath}" -d /Applications/`, { stdio: 'pipe', timeout: 120000 });
      sendProgress('Ollama מותקן', 100);
    } catch (e) {
      sendProgress('Ollama - התקנה נכשלה (אופציונלי)', 100);
    }
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) { /* ok */ }
  }
}

async function installPipPackages(pythonPath, sendProgress) {
  sendProgress('מתקין חבילות Python...', 0);
  const reqs = path.join(getAppPath(), 'requirements.txt');

  // Install CPU-only torch on Windows, default on Mac
  if (isWin) {
    sendProgress('מתקין PyTorch (CPU)...', 10);
    try {
      execSync(`"${pythonPath}" -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu`,
        { stdio: 'pipe', windowsHide: true, timeout: 600000 });
    } catch (e) { /* try regular install */ }
  }

  sendProgress('מתקין חבילות...', 40);
  try {
    execSync(`"${pythonPath}" -m pip install --no-cache-dir -r "${reqs}"`,
      { stdio: 'pipe', windowsHide: true, timeout: 600000 });
    sendProgress('חבילות Python מותקנות', 100);
  } catch (e) {
    sendProgress('שגיאה בהתקנת חבילות', 100);
    throw e;
  }
}

async function downloadModel(pythonPath, step, scriptCode, sendProgress) {
  return new Promise((resolve) => {
    const modelsDir = getModelsDir();
    const modelsDirPy = modelsDir.replace(/\\/g, '/');
    const env = { ...process.env, HF_HOME: modelsDir, XDG_CACHE_HOME: modelsDir, PYTHONIOENCODING: 'utf-8' };

    const proc = spawn(pythonPath, ['-c', scriptCode.replace('{{MODELS_DIR}}', modelsDirPy)],
      { env, windowsHide: true });

    proc.stdout.on('data', (data) => {
      const text = data.toString();
      if (text.includes('STEP:loading')) sendProgress('טוען...', 50);
      if (text.includes('STEP:done')) sendProgress('הותקן', 100);
    });

    proc.stderr.on('data', (data) => {
      const m = data.toString().match(/(\d+)%/);
      if (m) sendProgress(`מוריד... ${m[1]}%`, parseInt(m[1]));
    });

    proc.on('close', () => resolve());
    proc.on('error', () => resolve());
  });
}

// ============================================================
// Main setup flow
// ============================================================

async function runSetup(win) {
  const send = (step, status, message, percent) => {
    if (win && !win.isDestroyed()) {
      win.webContents.send('setup-progress', { step, status, message, percent });
    }
  };

  // Step 1: Python
  const python = detectPython();
  if (python.found) {
    send('python', 'done', `Python נמצא (${python.source})`, 100);
  } else {
    send('python', 'downloading', 'מוריד Python...', 0);
    await installPython((msg, pct) => send('python', pct >= 100 ? 'done' : 'downloading', msg, pct));
  }

  const pythonPath = detectPython().path;

  // Step 2: ffmpeg
  const ffmpeg = detectFfmpeg();
  if (ffmpeg.found) {
    send('ffmpeg', 'done', `ffmpeg נמצא (${ffmpeg.source})`, 100);
  } else {
    send('ffmpeg', 'downloading', 'מוריד ffmpeg...', 0);
    await installFfmpeg((msg, pct) => send('ffmpeg', pct >= 100 ? 'done' : 'downloading', msg, pct));
  }

  // Step 3: Ollama (optional)
  const ollama = detectOllama();
  if (ollama.found) {
    send('ollama', 'done', 'Ollama נמצא', 100);
  } else {
    send('ollama', 'downloading', 'מוריד Ollama...', 0);
    await installOllama((msg, pct) => send('ollama', pct >= 100 ? 'done' : 'downloading', msg, pct));
  }

  // Step 4: pip packages
  const hasPkgs = detectPipPackages(pythonPath);
  if (hasPkgs) {
    send('packages', 'done', 'חבילות Python מותקנות', 100);
  } else {
    send('packages', 'downloading', 'מתקין חבילות...', 0);
    await installPipPackages(pythonPath, (msg, pct) => send('packages', pct >= 100 ? 'done' : 'downloading', msg, pct));
  }

  // Step 5: Whisper model
  const models = checkModelsExist();
  if (models.whisper) {
    send('whisper', 'done', 'מודל Whisper נמצא', 100);
  } else {
    send('whisper', 'downloading', 'מוריד מודל Whisper...', 0);
    await downloadModel(pythonPath, 'whisper', `
import os
os.environ['HF_HOME'] = '{{MODELS_DIR}}'
print("STEP:loading", flush=True)
from faster_whisper import WhisperModel
model = WhisperModel("small", device="cpu", compute_type="int8")
print("STEP:done", flush=True)
`, (msg, pct) => send('whisper', pct >= 100 ? 'done' : 'downloading', `Whisper: ${msg}`, pct));
  }

  // Step 6: E5 model
  if (models.e5) {
    send('e5', 'done', 'מודל E5 נמצא', 100);
  } else {
    send('e5', 'downloading', 'מוריד מודל E5-large...', 0);
    await downloadModel(pythonPath, 'e5', `
import os
os.environ['HF_HOME'] = '{{MODELS_DIR}}'
print("STEP:loading", flush=True)
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("intfloat/multilingual-e5-large")
print("STEP:done", flush=True)
`, (msg, pct) => send('e5', pct >= 100 ? 'done' : 'downloading', `E5: ${msg}`, pct));
  }

  if (win && !win.isDestroyed()) win.webContents.send('setup-complete', {});
  await new Promise(r => setTimeout(r, 1500));
}

function needsSetup() {
  const python = detectPython();
  if (!python.found) return true;
  const models = checkModelsExist();
  if (!models.whisper || !models.e5) return true;
  if (!detectPipPackages(python.path)) return true;
  return false;
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
    server.on('error', () => resolve(findFreePort(startPort + 1)));
  });
}

// ============================================================
// Directory & config setup
// ============================================================

function ensureDirectories() {
  [getUserDataDir(), path.join(getUserDataDir(), 'chats'), getModelsDir(), getLogsDir(), getLocalDir()]
    .forEach(dir => fs.mkdirSync(dir, { recursive: true }));
}

function setupJunctions() {
  const appDir = getAppPath();
  const symlinkType = isMac ? 'dir' : 'junction';

  // Chats - remove empty dir if it exists (packaged by electron-builder), replace with symlink
  const chatsLink = path.join(appDir, 'chats');
  const userChatsDir = path.join(getUserDataDir(), 'chats');
  try {
    if (fs.existsSync(chatsLink)) {
      const stat = fs.lstatSync(chatsLink);
      if (!stat.isSymbolicLink()) {
        // It's a real directory (from packaging) - remove it if empty
        const contents = fs.readdirSync(chatsLink);
        if (contents.length === 0) {
          fs.rmdirSync(chatsLink);
        }
      }
    }
    if (!fs.existsSync(chatsLink)) {
      fs.symlinkSync(userChatsDir, chatsLink, symlinkType);
    }
  } catch (e) { console.error('Symlink error:', e.message); }

  // .env
  const userEnv = path.join(getUserDataDir(), '.env');
  const appEnv = path.join(appDir, '.env');
  if (!fs.existsSync(userEnv)) fs.writeFileSync(userEnv, '# WhatsArch API Keys\n', 'utf-8');
  try { if (!fs.existsSync(appEnv)) fs.symlinkSync(userEnv, appEnv, 'file'); }
  catch (e) { try { fs.copyFileSync(userEnv, appEnv); } catch (_) {} }

  // settings.json
  const userSettings = path.join(getUserDataDir(), 'settings.json');
  const appSettings = path.join(appDir, 'settings.json');
  if (!fs.existsSync(userSettings)) fs.writeFileSync(userSettings, '{}', 'utf-8');
  try { if (!fs.existsSync(appSettings)) fs.symlinkSync(userSettings, appSettings, 'file'); }
  catch (e) { try { fs.copyFileSync(userSettings, appSettings); } catch (_) {} }
}

// ============================================================
// Flask
// ============================================================

function startFlask(port) {
  return new Promise((resolve, reject) => {
    const python = detectPython();
    if (!python.found) return reject(new Error('Python not found'));

    const pythonPath = python.path;
    const appDir = getAppPath();
    const runScript = path.join(appDir, 'run.py');
    const ffmpeg = detectFfmpeg();

    const env = { ...process.env };
    if (ffmpeg.found && ffmpeg.path) env.PATH = ffmpeg.path + PATH_SEP + (env.PATH || '');
    // Also add our local ffmpeg
    const localFfmpeg = getFfmpegDir();
    if (fs.existsSync(localFfmpeg)) env.PATH = localFfmpeg + PATH_SEP + (env.PATH || '');

    env.HF_HOME = getModelsDir();
    env.XDG_CACHE_HOME = getModelsDir();
    env.PYTHONIOENCODING = 'utf-8';

    const args = [runScript, '--port', String(port), '--no-browser',
      '--skip-transcribe', '--skip-vision', '--skip-embeddings', '--skip-chunking'];

    console.log(`Starting Flask: ${pythonPath} ${args.join(' ')}`);
    flaskProcess = spawn(pythonPath, args, { env, cwd: appDir, windowsHide: true });

    const logFile = path.join(getLogsDir(), `flask-${Date.now()}.log`);
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

    flaskProcess.stdout.on('data', (d) => { const t = d.toString(); logStream.write(t); checkStarted(t); });
    flaskProcess.stderr.on('data', (d) => { const t = d.toString(); logStream.write('[STDERR] ' + t); checkStarted(t); });
    flaskProcess.on('error', (err) => { clearTimeout(timeout); logStream.write('[ERROR] ' + err.message + '\n'); reject(err); });
    flaskProcess.on('close', (code) => {
      logStream.write(`[EXIT] code ${code}\n`); logStream.end(); flaskProcess = null;
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
        try {
          if (isWin) execSync(`taskkill /pid ${pid} /T /F`, { windowsHide: true });
          else execSync(`kill -9 ${pid}`, { stdio: 'pipe' });
        } catch (e) {}
      }
      flaskProcess = null;
      resolve();
    });
  });
}

// ============================================================
// Windows
// ============================================================

function createSetupWindow() {
  setupWindow = new BrowserWindow({
    width: 520, height: 560, resizable: false, frame: false,
    backgroundColor: '#0D9488',
    icon: path.join(__dirname, 'icons', 'icon.png'),
    webPreferences: { preload: path.join(__dirname, 'setup-preload.js'), contextIsolation: true, nodeIntegration: false },
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
    titleBarStyle: isMac ? 'hiddenInset' : 'default',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false },
  });
  mainWindow.loadURL(`http://localhost:${port}`);
  if (!isMac) mainWindow.setMenuBarVisibility(false);
  mainWindow.on('close', (e) => { if (!isQuitting) { e.preventDefault(); mainWindow.hide(); } });
  mainWindow.on('closed', () => { mainWindow = null; });
  return mainWindow;
}

function createTray() {
  const iconPath = path.join(__dirname, 'icons', 'icon.png');
  let trayIcon;
  if (fs.existsSync(iconPath)) {
    const size = isMac ? { width: 18, height: 18 } : { width: 16, height: 16 };
    trayIcon = nativeImage.createFromPath(iconPath).resize(size);
    if (isMac) trayIcon.setTemplateImage(true);
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
  if (!isMac) tray.on('double-click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
}

// ============================================================
// Mac dock
// ============================================================

if (isMac) {
  app.on('activate', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
}

// ============================================================
// App lifecycle
// ============================================================

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus(); }
  });

  app.on('ready', async () => {
    try {
      ensureDirectories();

      if (needsSetup()) {
        const win = createSetupWindow();
        try { await runSetup(win); } catch (err) { console.error('Setup error:', err); }
        if (setupWindow) setupWindow.close();
      }

      if (!isDev()) setupJunctions();

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
    if (flaskProcess) { e.preventDefault(); await stopFlask(); app.quit(); }
  });

  app.on('window-all-closed', () => {
    if (!isMac) { /* tray */ }
  });
}
