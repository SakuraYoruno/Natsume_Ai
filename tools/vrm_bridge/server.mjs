import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer as createViteServer } from 'vite';
import { WebSocketServer } from 'ws';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const host = process.env.VRM_BRIDGE_HOST || '127.0.0.1';
const port = Number.parseInt(process.env.VRM_BRIDGE_PORT || '18760', 10);
const modelPath = process.env.VRM_MODEL_PATH || '';

const state = {
  startedAt: new Date().toISOString(),
  lastCommand: null,
  commandCount: 0,
  clientCount: 0,
};

const vite = await createViteServer({
  root: __dirname,
  appType: 'spa',
  server: {
    middlewareMode: true,
  },
});

const server = http.createServer(async (req, res) => {
  const reqUrl = new URL(req.url || '/', `http://${host}:${port}`);
  if (req.method === 'GET' && reqUrl.pathname === '/health') {
    return sendJson(res, {
      status: 'ok',
      bridge: 'vrm',
      started_at: state.startedAt,
      client_count: state.clientCount,
      command_count: state.commandCount,
      last_command: state.lastCommand,
      model_path: modelPath || null,
      model_available: Boolean(resolveModelPath()),
    });
  }
  if (req.method === 'GET' && reqUrl.pathname === '/model-info') {
    return sendJson(res, {
      model_url: '/model.vrm',
      model_path: modelPath || null,
      model_available: Boolean(resolveModelPath()),
    });
  }
  if (req.method === 'GET' && reqUrl.pathname === '/model.vrm') {
    return streamModel(res);
  }
  if (req.method === 'POST' && reqUrl.pathname === '/command') {
    const payload = await readJson(req);
    if (!payload || typeof payload !== 'object') {
      return sendJson(res, { success: false, detail: 'invalid json payload' }, 400);
    }
    const command = {
      ...payload,
      received_at: Date.now(),
    };
    state.lastCommand = command;
    state.commandCount += 1;
    broadcast(command);
    return sendJson(res, {
      success: true,
      detail: `broadcast to ${state.clientCount} client(s)`,
      client_count: state.clientCount,
      command_count: state.commandCount,
    });
  }
  vite.middlewares(req, res, (error) => {
    if (error) {
      vite.ssrFixStacktrace(error);
      res.statusCode = 500;
      res.end(error.stack || String(error));
    } else {
      res.statusCode = 404;
      res.end('Not found');
    }
  });
});

const wss = new WebSocketServer({ server, path: '/ws' });
wss.on('connection', (socket) => {
  state.clientCount = wss.clients.size;
  socket.send(JSON.stringify({ type: 'bridge_status', status: 'connected' }));
  socket.on('close', () => {
    state.clientCount = wss.clients.size;
  });
});

server.listen(port, host, () => {
  const model = resolveModelPath();
  console.log(`[vrm-bridge] http:// ${host}:${port}`.replace('http:// ', 'http://'));
  console.log(`[vrm-bridge] model=${model || '(missing: set VRM_MODEL_PATH)'}`);
});

function broadcast(command) {
  const raw = JSON.stringify(command);
  for (const client of wss.clients) {
    if (client.readyState === client.OPEN) {
      client.send(raw);
    }
  }
  state.clientCount = wss.clients.size;
}

function resolveModelPath() {
  if (modelPath && fs.existsSync(modelPath) && fs.statSync(modelPath).isFile()) {
    return modelPath;
  }
  const localModel = path.join(__dirname, 'public', 'models', 'aimis.vrm');
  if (fs.existsSync(localModel) && fs.statSync(localModel).isFile()) {
    return localModel;
  }
  return null;
}

function streamModel(res) {
  const resolved = resolveModelPath();
  if (!resolved) {
    return sendJson(res, { success: false, detail: 'VRM model not found' }, 404);
  }
  const stat = fs.statSync(resolved);
  res.writeHead(200, {
    'Content-Type': 'model/gltf-binary',
    'Content-Length': stat.size,
    'Cache-Control': 'no-store',
  });
  fs.createReadStream(resolved).pipe(res);
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  if (!chunks.length) {
    return {};
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    return null;
  }
}

function sendJson(res, payload, status = 200) {
  const body = Buffer.from(JSON.stringify(payload), 'utf8');
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
  });
  res.end(body);
}
