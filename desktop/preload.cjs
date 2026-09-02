"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("memoryHomeDesktop", {
  isElectron: true,
  platform: process.platform,
  runtime: () => ipcRenderer.invoke("memory-home:runtime"),
});
