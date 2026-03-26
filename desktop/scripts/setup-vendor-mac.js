/**
 * Build-time script: Downloads Python + ffmpeg + Ollama for macOS.
 * Run with: npm run setup-vendor:mac-arm64  (Apple Silicon)
 *       or: npm run setup-vendor:mac-x64    (Intel Mac)
 *
 * Downloads python-build-standalone, static ffmpeg, and Ollama.
 */

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Parse --arch flag
const archArg = process.argv.find(a => a.startsWith('--arch='));
const ARCH = archArg ? archArg.split('=')[1] : 'arm64'; // default to Apple Silicon

const PYTHON_VERSION = '3.11';
const PYTHON_BUILD_TAG = '20250317'; // Use a known stable release
const PYTHON_ARCH = ARCH === 'arm64' ? 'aarch64' : 'x86_64';
const PYTHON_URL = `https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/cpython-${PYTHON_VERSION}.11+${PYTHON_BUILD_TAG}-${PYTHON_ARCH}-apple-darwin-install_only.tar.gz`;

// ffmpeg - evermeet.cx provides x86_64 static builds (works on arm64 via Rosetta 2)
const FFMPEG_URL = 'https://evermeet.cx/ffmpeg/ffmpeg-7.1.1.zip';

// Ollama - universal macOS app
const OLLAMA_URL = 'https://ollama.com/download/Ollama-darwin.zip';

const VENDOR_DIR = path.join(__dirname, '..', 'vendor');
const PYTHON_DIR = path.join(VENDOR_DIR, 'python');
const FFMPEG_DIR = path.join(VENDOR_DIR, 'ffmpeg');
const OLLAMA_DIR = path.join(VENDOR_DIR, 'ollama');

function log(msg) { console.log(`[setup-mac-${ARCH}] ${msg}`); }

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    log(`Downloading: ${url}`);
    const file = fs.createWriteStream(dest);
    const getter = url.startsWith('https') ? https : http;
    getter.get(url, { headers: { 'User-Agent': 'WhatsArch-Builder/1.0' } }, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        file.close(); fs.unlinkSync(dest);
        return downloadFile(res.headers.location, dest).then(resolve).catch(reject);
      }
      if (res.statusCode !== 200) {
        file.close(); fs.unlinkSync(dest);
        return reject(new Error(`HTTP ${res.statusCode} for ${url}`));
      }
      const total = parseInt(res.headers['content-length'] || '0', 10);
      let dl = 0;
      res.on('data', (c) => {
        dl += c.length;
        if (total > 0) process.stdout.write(`\r  ${Math.round(dl/total*100)}% (${(dl/1048576).toFixed(1)}MB)`);
      });
      res.pipe(file);
      file.on('finish', () => { file.close(); console.log(''); resolve(); });
    }).on('error', (e) => { file.close(); try { fs.unlinkSync(dest); } catch(_) {} reject(e); });
  });
}

async function setupPython() {
  log('=== Setting up Python for macOS ===');
  if (fs.existsSync(path.join(PYTHON_DIR, 'bin', 'python3'))) { log('Already exists'); return; }

  // Clean and create
  if (fs.existsSync(PYTHON_DIR)) fs.rmSync(PYTHON_DIR, { recursive: true, force: true });
  fs.mkdirSync(PYTHON_DIR, { recursive: true });

  const tarPath = path.join(VENDOR_DIR, 'python-mac.tar.gz');
  try {
    await downloadFile(PYTHON_URL, tarPath);
  } catch (e) {
    // Try alternative URL pattern (version number might differ)
    log(`First URL failed (${e.message}), trying alternative...`);
    const altUrl = PYTHON_URL.replace('.11+', '.12+');
    await downloadFile(altUrl, tarPath);
  }

  // Extract tar.gz - use tar command (available on Windows via Git Bash)
  log('Extracting Python...');
  execSync(`tar -xzf "${tarPath}" -C "${VENDOR_DIR}"`, { stdio: 'inherit' });
  fs.unlinkSync(tarPath);

  // python-build-standalone extracts to python/ directory
  const py = path.join(PYTHON_DIR, 'bin', 'python3');
  if (!fs.existsSync(py)) {
    log('ERROR: python3 binary not found after extraction!');
    log('Contents of vendor/python:', fs.readdirSync(PYTHON_DIR).join(', '));
    if (fs.existsSync(path.join(PYTHON_DIR, 'bin'))) {
      log('Contents of vendor/python/bin:', fs.readdirSync(path.join(PYTHON_DIR, 'bin')).join(', '));
    }
    throw new Error('Python extraction failed - python3 binary not found');
  }

  // Make python3 executable (in case permissions are lost)
  try { execSync(`chmod +x "${py}"`, { stdio: 'pipe' }); } catch (e) { /* Windows - ignore */ }

  // Install pip if not present
  try {
    execSync(`"${py}" -m pip --version`, { stdio: 'pipe' });
    log('pip already available');
  } catch (e) {
    log('Installing pip...');
    execSync(`"${py}" -m ensurepip`, { stdio: 'inherit' });
  }

  // Install dependencies
  log('Installing PyTorch (default, includes MPS for Apple Silicon)...');
  execSync(`"${py}" -m pip install --no-cache-dir torch`, { stdio: 'inherit' });

  log('Installing remaining dependencies...');
  const reqs = path.join(__dirname, '..', '..', 'requirements.txt');
  execSync(`"${py}" -m pip install --no-cache-dir -r "${reqs}"`, { stdio: 'inherit' });

  log('Python setup complete!');
}

async function setupFfmpeg() {
  log('=== Setting up ffmpeg for macOS ===');
  if (fs.existsSync(path.join(FFMPEG_DIR, 'ffmpeg'))) { log('Already exists'); return; }

  fs.mkdirSync(FFMPEG_DIR, { recursive: true });
  const zipPath = path.join(VENDOR_DIR, 'ffmpeg-mac.zip');

  await downloadFile(FFMPEG_URL, zipPath);

  log('Extracting ffmpeg...');
  // Use unzip or tar depending on availability
  try {
    execSync(`unzip -o "${zipPath}" -d "${FFMPEG_DIR}"`, { stdio: 'inherit' });
  } catch (e) {
    // Fallback: use powershell on Windows or tar
    try {
      execSync(`tar -xf "${zipPath}" -C "${FFMPEG_DIR}"`, { stdio: 'inherit' });
    } catch (e2) {
      execSync(`powershell -Command "Expand-Archive -Force -Path '${zipPath}' -DestinationPath '${FFMPEG_DIR}'"`, { stdio: 'inherit' });
    }
  }
  fs.unlinkSync(zipPath);

  // Make executable
  const ffmpegBin = path.join(FFMPEG_DIR, 'ffmpeg');
  if (fs.existsSync(ffmpegBin)) {
    try { execSync(`chmod +x "${ffmpegBin}"`, { stdio: 'pipe' }); } catch (e) { /* Windows */ }
  } else {
    log('WARNING: ffmpeg binary not found after extraction!');
  }

  log('ffmpeg setup complete!');
}

async function setupOllama() {
  log('=== Setting up Ollama for macOS ===');
  if (fs.existsSync(path.join(OLLAMA_DIR, 'Ollama-darwin.zip'))) { log('Already exists'); return; }

  fs.mkdirSync(OLLAMA_DIR, { recursive: true });
  const dest = path.join(OLLAMA_DIR, 'Ollama-darwin.zip');
  await downloadFile(OLLAMA_URL, dest);
  log('Ollama download complete!');
}

async function main() {
  log(`Building for macOS ${ARCH}`);
  fs.mkdirSync(VENDOR_DIR, { recursive: true });
  try {
    await setupPython();
    await setupFfmpeg();
    await setupOllama();
    log('');
    log('=== All done! ===');
    log(`Run "npm run build:mac-${ARCH}" to create the Mac app.`);
  } catch (err) {
    console.error('Setup failed:', err);
    process.exit(1);
  }
}

main();
