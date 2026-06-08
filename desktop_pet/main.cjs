const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require("electron");
const http = require("http");
const path = require("path");

const PORT = Number(process.env.NATSUME_DESKTOP_PET_PORT || 18788);
const HOST = "127.0.0.1";

let mainWindow = null;
let httpServer = null;
let clickThrough = false;
let rendererState = {
  loaded: false,
  model: null,
  lastAction: null,
};

function petBounds(name = "right_bottom", width = 360, height = 560, margin = 28) {
  const area = screen.getPrimaryDisplay().workArea;
  const preset = String(name || "right_bottom").replace(/-/g, "_").toLowerCase();
  const w = Math.max(180, Number(width) || 360);
  const h = Math.max(240, Number(height) || 560);
  const m = Math.max(0, Number(margin) || 28);
  if (preset === "left_bottom") {
    return { x: area.x + m, y: area.y + area.height - h - m, width: w, height: h };
  }
  if (preset === "right_center") {
    return { x: area.x + area.width - w - m, y: area.y + Math.round((area.height - h) / 2), width: w, height: h };
  }
  if (preset === "left_center") {
    return { x: area.x + m, y: area.y + Math.round((area.height - h) / 2), width: w, height: h };
  }
  return { x: area.x + area.width - w - m, y: area.y + area.height - h - m, width: w, height: h };
}

function createWindow() {
  const bounds = petBounds();
  mainWindow = new BrowserWindow({
    ...bounds,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    resizable: true,
    movable: true,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false,
    },
  });
  mainWindow.setAlwaysOnTop(true, "screen-saver");
  mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
  mainWindow.once("ready-to-show", () => {
    if (!mainWindow) return;
    mainWindow.show();
    mainWindow.focus();
    mainWindow.moveTop();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function windowStatus() {
  if (!mainWindow) {
    return null;
  }
  return {
    bounds: mainWindow.getBounds(),
    visible: mainWindow.isVisible(),
    alwaysOnTop: mainWindow.isAlwaysOnTop(),
    clickThrough,
  };
}

function sendAction(payload) {
  if (!mainWindow) {
    return { success: false, reason_code: "DESKTOP_PET_WINDOW_MISSING", detail: "desktop pet window is not available" };
  }
  const action = String(payload.action || "touch").toLowerCase();
  if (action === "show") {
    mainWindow.show();
    mainWindow.focus();
    mainWindow.moveTop();
  } else if (action === "hide") {
    mainWindow.hide();
  } else if (action === "preset") {
    mainWindow.setBounds(petBounds(payload.name, payload.width, payload.height, payload.margin), true);
    mainWindow.show();
    mainWindow.focus();
    mainWindow.moveTop();
  } else if (action === "click_through") {
    clickThrough = Boolean(payload.enabled);
    mainWindow.setIgnoreMouseEvents(clickThrough, { forward: true });
  }
  mainWindow.webContents.send("pet:action", payload);
  rendererState.lastAction = payload;
  return {
    success: true,
    detail: "desktop pet action accepted",
    action,
    window: windowStatus(),
    renderer: rendererState,
  };
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 1024 * 1024) {
        reject(new Error("request too large"));
        req.destroy();
      }
    });
    req.on("end", () => {
      try {
        resolve(body.trim() ? JSON.parse(body) : {});
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

function writeJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function startHttpServer() {
  httpServer = http.createServer(async (req, res) => {
    try {
      if (req.method === "GET" && req.url === "/health") {
        writeJson(res, 200, {
          success: true,
          detail: "desktop pet runtime is available",
          window: windowStatus(),
          renderer: rendererState,
          port: PORT,
        });
        return;
      }
      if (req.method === "POST" && req.url === "/action") {
        const payload = await readJson(req);
        writeJson(res, 200, sendAction(payload));
        return;
      }
      writeJson(res, 404, { success: false, reason_code: "NOT_FOUND", detail: "desktop pet endpoint not found" });
    } catch (error) {
      writeJson(res, 500, { success: false, reason_code: "DESKTOP_PET_ERROR", detail: String(error && error.message || error) });
    }
  });
  httpServer.listen(PORT, HOST);
}

app.whenReady().then(() => {
  createWindow();
  startHttpServer();
  globalShortcut.register("CommandOrControl+Alt+P", () => {
    if (!mainWindow) return;
    clickThrough = !clickThrough;
    mainWindow.setIgnoreMouseEvents(clickThrough, { forward: true });
    mainWindow.webContents.send("pet:action", { action: "click_through", enabled: clickThrough });
  });
});

ipcMain.handle("pet:report-state", (_event, state) => {
  rendererState = { ...rendererState, ...state };
  return rendererState;
});

ipcMain.handle("pet:set-click-through", (_event, enabled) => {
  if (!mainWindow) return false;
  clickThrough = Boolean(enabled);
  mainWindow.setIgnoreMouseEvents(clickThrough, { forward: true });
  return clickThrough;
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (httpServer) {
    httpServer.close();
  }
});
