const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require("electron");
const http = require("http");
const path = require("path");
const {
  ACTION_SCOPE,
  BridgeSecurityError,
  EXPOSURE_MODES,
  READ_SCOPE,
  createBridgeSecurity,
  publicBridgeConfig,
  resolveBridgeConfig,
} = require("./bridge_security.cjs");

const bridgeConfig = resolveBridgeConfig(process.env);
const PORT = bridgeConfig.port;
const HOST = bridgeConfig.host;
const primaryInstance = app.requestSingleInstanceLock();
if (!primaryInstance) {
  app.quit();
}

let mainWindow = null;
let httpServer = null;
let bridgeSecurity = null;
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

function readRawBody(req, maxBodyBytes) {
  return new Promise((resolve, reject) => {
    const contentLengths = [];
    const transferEncodings = [];
    for (let index = 0; index + 1 < req.rawHeaders.length; index += 2) {
      const name = String(req.rawHeaders[index] || "").trim().toLowerCase();
      if (name === "content-length") contentLengths.push(String(req.rawHeaders[index + 1] || "").trim());
      if (name === "transfer-encoding") transferEncodings.push(String(req.rawHeaders[index + 1] || "").trim());
    }
    if (transferEncodings.length || contentLengths.length > 1) {
      reject(new BridgeSecurityError(
        "BRIDGE_BODY_FRAMING_INVALID",
        "desktop pet request body framing is ambiguous",
        400,
      ));
      return;
    }
    if (req.method === "POST" && contentLengths.length !== 1) {
      reject(new BridgeSecurityError(
        "BRIDGE_CONTENT_LENGTH_REQUIRED",
        "desktop pet action requires one Content-Length header",
        411,
      ));
      return;
    }
    if (contentLengths.length === 1) {
      const declaredLength = Number(contentLengths[0]);
      if (!Number.isSafeInteger(declaredLength) || declaredLength < 0) {
        reject(new BridgeSecurityError("BRIDGE_CONTENT_LENGTH_INVALID", "invalid Content-Length", 400));
        return;
      }
      if (declaredLength > maxBodyBytes) {
        reject(new BridgeSecurityError("BRIDGE_BODY_TOO_LARGE", "desktop pet request exceeds body limit", 413));
        return;
      }
    }
    const chunks = [];
    let bodyBytes = 0;
    let rejected = false;
    req.on("data", (chunk) => {
      if (rejected) return;
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      bodyBytes += bytes.length;
      if (bodyBytes > maxBodyBytes) {
        rejected = true;
        const error = new BridgeSecurityError(
          "BRIDGE_BODY_TOO_LARGE",
          "desktop pet request exceeds the configured body limit",
          413,
        );
        error.auditRawBody = Buffer.concat([...chunks, bytes], maxBodyBytes + 1);
        chunks.length = 0;
        reject(error);
        return;
      }
      chunks.push(bytes);
    });
    req.on("end", () => {
      if (!rejected) resolve(Buffer.concat(chunks, bodyBytes));
    });
    req.on("error", (error) => {
      if (!rejected) reject(error);
    });
  });
}

async function readAuthorizedBody(req, requiredScope) {
  let rawBody;
  try {
    rawBody = await readRawBody(req, bridgeConfig.maxBodyBytes);
  } catch (error) {
    if (
      error instanceof BridgeSecurityError
      && error.reasonCode === "BRIDGE_BODY_TOO_LARGE"
      && Buffer.isBuffer(error.auditRawBody)
    ) {
      bridgeSecurity.authorize({
        method: req.method,
        requestPath: req.url,
        headers: req.headers,
        rawHeaders: req.rawHeaders,
        rawBody: error.auditRawBody,
        peer: req.socket.remoteAddress,
        requiredScope,
      });
    }
    throw error;
  }
  bridgeSecurity.authorize({
    method: req.method,
    requestPath: req.url,
    headers: req.headers,
    rawHeaders: req.rawHeaders,
    rawBody,
    peer: req.socket.remoteAddress,
    requiredScope,
  });
  return rawBody;
}

function parseJsonObject(rawBody) {
  const text = rawBody.toString("utf8").trim();
  if (!text) return {};
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new BridgeSecurityError(
      "DESKTOP_PET_JSON_INVALID",
      "desktop pet action body must be valid JSON",
      400,
    );
  }
  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    throw new BridgeSecurityError(
      "DESKTOP_PET_JSON_OBJECT_REQUIRED",
      "desktop pet action body must be a JSON object",
      400,
    );
  }
  return payload;
}

function writeJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  res.end(body);
}

function startHttpServer() {
  if (!bridgeConfig.listenerEnabled) return;
  try {
    bridgeSecurity = createBridgeSecurity(bridgeConfig);
  } catch (error) {
    const reasonCode = error instanceof BridgeSecurityError
      ? error.reasonCode
      : "BRIDGE_SECURITY_INIT_FAILED";
    console.error("[desktop-pet] bridge listener not started", reasonCode);
    return;
  }
  httpServer = http.createServer(async (req, res) => {
    try {
      bridgeSecurity.admitPeer(req.socket.remoteAddress);
      if (req.method === "GET" && req.url === "/health") {
        if (bridgeConfig.mode === EXPOSURE_MODES.REMOTE_APPROVED) {
          await readAuthorizedBody(req, READ_SCOPE);
        }
        writeJson(res, 200, { status: "ok" });
        return;
      }
      if (req.method === "POST" && req.url === "/action") {
        const rawBody = await readAuthorizedBody(req, ACTION_SCOPE);
        const payload = parseJsonObject(rawBody);
        writeJson(res, 200, sendAction(payload));
        return;
      }
      writeJson(res, 404, { success: false, reason_code: "NOT_FOUND", detail: "desktop pet endpoint not found" });
    } catch (error) {
      const expected = error instanceof BridgeSecurityError;
      const status = expected ? error.statusCode : 500;
      const reasonCode = expected ? error.reasonCode : "DESKTOP_PET_ERROR";
      if (bridgeSecurity && !error.auditRecorded) {
        try {
          bridgeSecurity.recordPreauthRejection({
            peer: req.socket.remoteAddress,
            method: req.method,
            requestPath: req.url,
            reasonCode,
          });
        } catch {
          // The request is already fail-closed; an unavailable audit remains a 5xx condition.
        }
      }
      writeJson(res, status, {
        success: false,
        reason_code: reasonCode,
        detail: status >= 500 ? "desktop pet request failed" : "desktop pet request rejected",
      });
    }
  });
  httpServer.requestTimeout = 15_000;
  httpServer.headersTimeout = 10_000;
  httpServer.keepAliveTimeout = 5_000;
  httpServer.maxConnections = Math.min(256, Math.max(32, bridgeConfig.rateBurst * 4));
  httpServer.on("error", (error) => {
    console.error("[desktop-pet] bridge listener error", String(error && error.code || "UNKNOWN"));
  });
  httpServer.listen(PORT, HOST, () => {
    console.info("[desktop-pet] bridge listener ready", JSON.stringify(publicBridgeConfig(bridgeConfig)));
  });
}

app.whenReady().then(() => {
  if (!primaryInstance) return;
  createWindow();
  if (bridgeConfig.listenerEnabled) {
    startHttpServer();
  } else {
    console.info("[desktop-pet] bridge listener disabled", JSON.stringify(publicBridgeConfig(bridgeConfig)));
  }
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
