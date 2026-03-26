const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('setupAPI', {
  onProgress: (callback) => ipcRenderer.on('setup-progress', (_, data) => callback(data)),
  onComplete: (callback) => ipcRenderer.on('setup-complete', (_, data) => callback(data)),
  onError: (callback) => ipcRenderer.on('setup-error', (_, data) => callback(data)),
});
