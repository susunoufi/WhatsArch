/**
 * Build-time script: Downloads and sets up embedded Python + ffmpeg.
 * Run with: npm run setup-vendor (from desktop/ directory)
 */

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const PYTHON_VERSION = '3.11.9';
const PYTHON_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`;
const FFMPEG_URL = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip';
const GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py';

const VENDOR_DIR = path.join(__dirname, '..', 'vendor');
const PYTHON_DIR = path.join(VENDOR_DIR, 'python');
const FFMPEG_DIR = path.join(VENDOR_DIR, 'ffmpeg');

function log(msg) { console.log(`[setup] ${msg}`); }

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    log(`Downloading: ${url}`);
    const file = fs.createWriteStream(dest);
    const getter = url.startsWith('https') ? https : http;
    getter.get(url, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        file.close(); fs.unlinkSync(dest);
        return downloadFile(res.headers.location, dest).then(resolve).catch(reject);
      }
      const total = parseInt(res.headers['content-length'] || '0', 10);
      let dl = 0;
      res.on('data', (c) => {
        dl += c.length;
        if (total > 0) process.stdout.write(`\r  ${Math.round(dl/total*100)}% (${(dl/1048576).toFixed(1)}MB)`);
      });
      res.pipe(file);
      file.on('finish', () => { file.close(); console.log(''); resolve(); });
    }).on('error', (e) => { fs.unlinkSync(dest); reject(e); });
  });
}

function extract(zip, dest) {
  log(`Extracting: ${zip}`);
  fs.mkdirSync(dest, { recursive: true });
  execSync(`powershell -Command "Expand-Archive -Force -Path '${zip}' -DestinationPath '${dest}'"`, { stdio: 'inherit' });
}

async function setupPython() {
  log('=== Setting up Python ===');
  if (fs.existsSync(path.join(PYTHON_DIR, 'python.exe'))) { log('Already exists'); return; }

  fs.mkdirSync(PYTHON_DIR, { recursive: true });
  const zip = path.join(VENDOR_DIR, 'python-embed.zip');
  await downloadFile(PYTHON_URL, zip);
  extract(zip, PYTHON_DIR);
  fs.unlinkSync(zip);

  // Enable site-packages
  for (const f of fs.readdirSync(PYTHON_DIR).filter(f => f.endsWith('._pth'))) {
    const p = path.join(PYTHON_DIR, f);
    let c = fs.readFileSync(p, 'utf-8');
    c = c.replace(/^#\s*import site/m, 'import site');
    if (!c.includes('Lib/site-packages')) c += '\nLib/site-packages\n';
    fs.writeFileSync(p, c);
  }
  fs.mkdirSync(path.join(PYTHON_DIR, 'Lib', 'site-packages'), { recursive: true });

  // Install pip
  const getPip = path.join(VENDOR_DIR, 'get-pip.py');
  await downloadFile(GET_PIP_URL, getPip);
  const py = path.join(PYTHON_DIR, 'python.exe');
  execSync(`"${py}" "${getPip}"`, { stdio: 'inherit', cwd: PYTHON_DIR });
  fs.unlinkSync(getPip);

  // Install dependencies (CPU-only torch first)
  log('Installing CPU-only PyTorch...');
  execSync(`"${py}" -m pip install torch --index-url https://download.pytorch.org/whl/cpu`, { stdio: 'inherit' });
  log('Installing remaining dependencies...');
  const reqs = path.join(__dirname, '..', '..', 'requirements.txt');
  execSync(`"${py}" -m pip install -r "${reqs}"`, { stdio: 'inherit' });
  log('Python setup complete!');
}

async function setupFfmpeg() {
  log('=== Setting up ffmpeg ===');
  if (fs.existsSync(path.join(FFMPEG_DIR, 'ffmpeg.exe'))) { log('Already exists'); return; }

  fs.mkdirSync(FFMPEG_DIR, { recursive: true });
  const zip = path.join(VENDOR_DIR, 'ffmpeg.zip');
  await downloadFile(FFMPEG_URL, zip);
  const tmp = path.join(VENDOR_DIR, 'ffmpeg-temp');
  extract(zip, tmp);
  fs.unlinkSync(zip);

  // Find ffmpeg.exe recursively
  function find(dir, name) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) { const r = find(full, name); if (r) return r; }
      else if (e.name === name) return full;
    }
    return null;
  }

  const exe = find(tmp, 'ffmpeg.exe');
  if (exe) fs.copyFileSync(exe, path.join(FFMPEG_DIR, 'ffmpeg.exe'));
  else log('WARNING: ffmpeg.exe not found!');
  fs.rmSync(tmp, { recursive: true, force: true });
  log('ffmpeg setup complete!');
}

async function main() {
  fs.mkdirSync(VENDOR_DIR, { recursive: true });
  try {
    await setupPython();
    await setupFfmpeg();
    log('=== All done! Run "npm run build" to create the installer. ===');
  } catch (err) {
    console.error('Setup failed:', err);
    process.exit(1);
  }
}

main();
