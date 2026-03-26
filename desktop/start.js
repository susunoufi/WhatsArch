#!/usr/bin/env node
/**
 * Dev launcher for WhatsArch Electron app. Cross-platform (Windows + Mac).
 *
 * Two issues must be solved for require('electron') to work inside the Electron binary:
 * 1. ELECTRON_RUN_AS_NODE env var (set by npm/npx) makes the binary behave as
 *    plain Node.js, skipping all Electron initialization. Must be unset.
 * 2. node_modules/electron/index.js (npm helper) shadows the built-in 'electron'
 *    module. Must be moved out of the way before launching.
 */
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const isMac = process.platform === 'darwin';
const npmElectronDir = path.join(__dirname, 'node_modules', 'electron');
const hiddenDir = path.join(__dirname, 'node_modules', '_electron_pkg');

// Get the Electron binary path (platform-aware) before moving anything
function getElectronBin(fromDir) {
  if (isMac) {
    // On Mac, the binary is inside Electron.app
    const macBin = path.join(fromDir, 'dist', 'Electron.app', 'Contents', 'MacOS', 'Electron');
    if (fs.existsSync(macBin)) return macBin;
    // Fallback: some versions put it directly in dist/
    return path.join(fromDir, 'dist', 'electron');
  }
  return path.join(fromDir, 'dist', 'electron.exe');
}

// Step 1: Move the npm electron package out of the way
let moved = false;
try {
  if (fs.existsSync(npmElectronDir) && !fs.existsSync(hiddenDir)) {
    fs.renameSync(npmElectronDir, hiddenDir);
    moved = true;
  }
} catch (e) {
  console.error('Warning: cannot move electron package:', e.message);
}

// Step 2: Spawn Electron WITHOUT ELECTRON_RUN_AS_NODE
const bin = getElectronBin(moved ? hiddenDir : npmElectronDir);

const env = Object.assign({}, process.env);
delete env.ELECTRON_RUN_AS_NODE;

console.log('Starting WhatsArch...');

const child = spawn(bin, ['.'], {
  stdio: 'inherit',
  cwd: __dirname,
  windowsHide: false,
  env: env,
});

function restore() {
  if (moved && fs.existsSync(hiddenDir)) {
    for (let i = 0; i < 5; i++) {
      try {
        if (!fs.existsSync(npmElectronDir)) {
          fs.renameSync(hiddenDir, npmElectronDir);
        }
        return;
      } catch (e) {
        if (i < 4) {
          const end = Date.now() + 500;
          while (Date.now() < end) { /* busy wait */ }
        }
      }
    }
  }
}

child.on('close', (code) => { restore(); process.exit(code || 0); });
child.on('error', (err) => { console.error('Launch error:', err.message); restore(); process.exit(1); });
process.on('SIGINT', () => { if (!child.killed) child.kill('SIGINT'); });
process.on('SIGTERM', () => { if (!child.killed) child.kill('SIGTERM'); });
