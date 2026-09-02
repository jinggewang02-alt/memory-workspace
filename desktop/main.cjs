"use strict";

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const STARTUP_TIMEOUT_MS = 20_000;
const HEALTH_RETRY_MS = 120;

let backend = null;
let backendUrl = null;
let mainWindow = null;
let quitting = false;

function applicationRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "memory-home");
  }
  return path.resolve(__dirname, "..");
}

function pythonCandidates() {
  const configured = process.env.MEMORY_HOME_PYTHON;
  const bundled = app.isPackaged
    ? path.join(process.resourcesPath, "python", "bin", "python3")
    : null;
  const commonMacRuntimes = [
    "/opt/homebrew/bin/python3.13",
    "/opt/homebrew/bin/python3.12",
    "/opt/homebrew/bin/python3.11",
    "/opt/homebrew/bin/python3.10",
    "/usr/local/bin/python3.13",
    "/usr/local/bin/python3.12",
    "/usr/local/bin/python3.11",
    "/usr/local/bin/python3.10",
  ];
  return [
    bundled,
    configured,
    ...commonMacRuntimes,
    "python3.13",
    "python3.12",
    "python3.11",
    "python3.10",
    "python3",
    "python",
  ].filter(
    (candidate, index, values) =>
      candidate &&
      values.indexOf(candidate) === index &&
      (!candidate.includes(path.sep) || fs.existsSync(candidate)),
  );
}

function requestHealth(url) {
  return new Promise((resolve, reject) => {
    const request = http.get(new URL("/api/health", url), { timeout: 1_500 }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => {
        try {
          const payload = JSON.parse(body);
          if (response.statusCode === 200 && payload.status === "ready") {
            resolve(payload);
            return;
          }
        } catch {
          // The retry loop reports one concise startup error after the deadline.
        }
        reject(new Error("本地服务尚未就绪"));
      });
    });
    request.on("timeout", () => request.destroy(new Error("健康检查超时")));
    request.on("error", reject);
  });
}

async function waitForHealth(url, deadline) {
  let latestError = null;
  while (Date.now() < deadline) {
    try {
      await requestHealth(url);
      return;
    } catch (error) {
      latestError = error;
      await new Promise((resolve) => setTimeout(resolve, HEALTH_RETRY_MS));
    }
  }
  throw latestError || new Error("本地服务启动超时");
}

function spawnBackend(python) {
  const root = applicationRoot();
  const script = path.join(root, "scripts", "quickstart.py");
  const child = spawn(
    python,
    [script, "--host", "127.0.0.1", "--port", "0", "--no-open"],
    {
      cwd: root,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  return child;
}

function readBackendUrl(child, python) {
  return new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timeout = setTimeout(() => {
      finish(new Error("等待本地服务地址超时"));
    }, STARTUP_TIMEOUT_MS);

    const finish = (error, url = null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      child.stdout.off("data", onStdout);
      child.stderr.off("data", onStderr);
      child.off("error", onError);
      child.off("exit", onExit);
      if (error) reject(error);
      else resolve(url);
    };

    const onStdout = (chunk) => {
      stdout += chunk.toString("utf8");
      const match = stdout.match(/https?:\/\/127\.0\.0\.1:\d+\//);
      if (match) finish(null, match[0]);
    };
    const onStderr = (chunk) => {
      stderr += chunk.toString("utf8");
      if (stderr.length > 8_000) stderr = stderr.slice(-8_000);
    };
    const onError = (error) => finish(error);
    const onExit = (code) => {
      finish(
        new Error(
          `${python} 未能启动 Memory Home 服务（退出码 ${code ?? "unknown"}）` +
            (stderr.trim() ? `：${stderr.trim()}` : ""),
        ),
      );
    };

    child.stdout.on("data", onStdout);
    child.stderr.on("data", onStderr);
    child.once("error", onError);
    child.once("exit", onExit);
  });
}

async function startBackend() {
  const failures = [];
  for (const python of pythonCandidates()) {
    const child = spawnBackend(python);
    try {
      const url = await readBackendUrl(child, python);
      await waitForHealth(url, Date.now() + STARTUP_TIMEOUT_MS);
      backend = child;
      backendUrl = url;
      child.once("exit", (code, signal) => {
        backend = null;
        backendUrl = null;
        if (!quitting) {
          dialog.showErrorBox(
            "Memory Home 已停止",
            `本地记忆服务意外退出（${signal || code || "unknown"}）。请重新打开应用。`,
          );
          app.quit();
        }
      });
      return url;
    } catch (error) {
      failures.push(error.message);
      if (!child.killed) child.kill("SIGTERM");
    }
  }
  throw new Error(failures.join("\n"));
}

function stopBackend() {
  if (backend === null || backend.exitCode !== null) return;
  const child = backend;
  backend = null;
  backendUrl = null;
  child.kill("SIGTERM");
  const force = setTimeout(() => {
    if (child.exitCode === null) child.kill("SIGKILL");
  }, 2_000);
  force.unref();
}

function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 680,
    backgroundColor: "#f7f7f5",
    title: "Memory Home",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    trafficLightPosition: { x: 18, y: 18 },
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const allowedOrigin = new URL(url).origin;
  mainWindow.webContents.on("will-navigate", (event, targetUrl) => {
    if (new URL(targetUrl).origin !== allowedOrigin) event.preventDefault();
  });
  mainWindow.webContents.setWindowOpenHandler(({ url: targetUrl }) => {
    if (targetUrl.startsWith("https://")) void shell.openExternal(targetUrl);
    return { action: "deny" };
  });
  mainWindow.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => {
    callback(false);
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  void mainWindow.loadURL(url);

  if (process.env.MEMORY_HOME_DEVTOOLS === "1") {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }
}

ipcMain.handle("memory-home:runtime", () => ({
  desktop: true,
  packaged: app.isPackaged,
  serviceReady: backend !== null && backendUrl !== null,
}));

app.setName("Memory Home");
if (!app.isPackaged) {
  app.setPath("userData", path.join(app.getPath("appData"), "Memory Home"));
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow === null) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    try {
      const url = await startBackend();
      createWindow(url);
    } catch (error) {
      dialog.showErrorBox(
        "Memory Home 无法启动",
        `${error.message}\n\n可以设置 MEMORY_HOME_PYTHON 指向可用的 Python 3。`,
      );
      app.quit();
    }
  });

  app.on("activate", () => {
    if (mainWindow === null && backendUrl !== null) createWindow(backendUrl);
  });

  app.on("before-quit", () => {
    quitting = true;
    stopBackend();
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
