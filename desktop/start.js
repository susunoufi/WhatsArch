#!/usr/bin/env node
/**
 * Dev launcher for WhatsArch Electron app.
 *
 * Two issues must be solved for require('electron') to work inside electron.exe:
 * 1. ELECTRON_RUN_AS_NODE env var (set by npm/npx) makes electron.exe behave as
 *    plain Node.js, skipping all Electron initialization. Must be unset.
 * 2. node_modules/electron/index.js (npm helper) shadows the built-in 'electron'
 *    module. Must be moved out of the way before launching.
 *
 * In production (packaged app), neither issue exists because:
 * - electron.exe is launched directly (no npm)
 * - node_modules/electron is not bundled
 */
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const npmElectronDir = path.join(__dirname, 'node_modules', 'electron');
const hiddenDir = path.join(__dirname, 'node_modules', '_electron_pkg');

// Compute the binary path before moving
const electronBin = path.join(npmElectronDir, 'dist', 'electron.exe');

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

// Step 2: Spawn electron.exe WITHOUT ELECTRON_RUN_AS_NODE
const bin = moved
  ? path.join(hiddenDir, 'dist', 'electron.exe')
  : electronBin;

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
    // Retry a few times (electron.exe may still hold a lock briefly)
    for (let i = 0; i < 5; i++) {
      try {
        if (!fs.existsSync(npmElectronDir)) {
          fs.renameSync(hiddenDir, npmElectronDir);
        }
        return;
      } catch (e) {
        if (i < 4) {
          // Sync sleep 500ms
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
