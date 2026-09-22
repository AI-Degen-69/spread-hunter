/**
 * Spread Hunter — TS dashboard bridge (read-only reverse proxy)
 *
 * Serves the frontend from dashboard/static on PORT (default 8800) and
 * reverse-proxies all GET /api/* requests to the live Python FastAPI backend
 * (default 127.0.0.1:8799). The Python engine stays authoritative; this server
 * only *presents* it. The SSE endpoint (/api/cycle-stream) is streamed through
 * untouched so the dashboard gets the real ring-file event tail.
 *
 * Deliberately read-only: any non-GET/HEAD /api request is answered 501. Live
 * control actions (start/stop/sweep) are gated behind the control token and
 * wiring those happens separately on purpose.
 *
 * Zero external dependencies: Node.js 24 runs this file directly via type
 * stripping, so there is no `npm install` step:
 *
 *   node server.ts
 *
 * Env overrides:
 *   PORT          TS bridge listen port            (default 8800)
 *   PY_DASH_URL   upstream Python dashboard        (default http://127.0.0.1:8799)
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { request as httpRequest } from 'node:http';
import { readFile } from 'node:fs/promises';
import { existsSync, statSync } from 'node:fs';
import * as path from 'node:path';
import * as url from 'node:url';

const PORT = Number(process.env.PORT || 8800);
const UPSTREAM = process.env.PY_DASH_URL || 'http://127.0.0.1:8799';
const STATIC_DIR = path.join(process.cwd(), 'dashboard', 'static');

const up = new URL(UPSTREAM);

const TOKEN_PLACEHOLDER = '__LIVE_DASH_CONTROL_TOKEN__';
const TOKEN_RE = /CONTROL_TOKEN\s*=\s*"([^"]+)"/;

/** Extract the control token Python baked into its served HTML. */
export function scrapeControlToken(html: string): string {
  const m = html.match(TOKEN_RE);
  return m ? m[1] : '';
}

/** Replace the frontend placeholder with the real token. */
export function injectToken(html: string, token: string): string {
  if (!token) return html;
  return html.split(TOKEN_PLACEHOLDER).join(token);
}

/** Control forwarding is OFF unless BRIDGE_CONTROL=1 explicitly. */
export function allowControl(): boolean {
  return process.env.BRIDGE_CONTROL === '1';
}

let cachedToken = '';
let cachedAt = 0;
const TOKEN_TTL_MS = 60_000;

/** Fetch Python's own HTML and scrape the live token, cached 60 s. */
export async function getControlToken(): Promise<string> {
  const now = Date.now();
  if (cachedToken && now - cachedAt < TOKEN_TTL_MS) return cachedToken;
  try {
    const res = await fetch(`${up.origin}/`, { signal: AbortSignal.timeout(4000) });
    const html = await res.text();
    cachedToken = scrapeControlToken(html);
    cachedAt = now;
  } catch {
    cachedToken = '';
  }
  return cachedToken;
}

/* ── helpers ─────────────────────────────────────────────────────────────── */

function json(res: ServerResponse, code: number, obj: unknown): void {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(body);
}

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

async function serveStatic(req: IncomingMessage, res: ServerResponse, pathname: string): Promise<void> {
  const rel = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  const target = path.normalize(path.join(STATIC_DIR, rel));
  // No path traversal: the resolved file must stay inside STATIC_DIR.
  if (!target.startsWith(STATIC_DIR + path.sep)) {
    return json(res, 403, { ok: false, error: 'forbidden' });
  }
  try {
    if (!existsSync(target) || statSync(target).isDirectory()) {
      return json(res, 404, { ok: false, error: 'not found' });
    }
    const ext = path.extname(target).toLowerCase();
    const data = await readFile(target);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  } catch {
    json(res, 500, { ok: false, error: 'unreadable' });
  }
}

async function serveIndex(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const indexPath = path.join(STATIC_DIR, 'index.html');
  try {
    if (!existsSync(indexPath)) {
      return json(res, 404, { ok: false, error: 'index.html not found' });
    }
    const html = await readFile(indexPath, 'utf8');
    const token = await getControlToken();
    const out = injectToken(html, token);
    res.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-cache, no-store, must-revalidate',
    });
    res.end(out);
  } catch {
    json(res, 500, { ok: false, error: 'unreadable' });
  }
}

/* ── upstream proxy ──────────────────────────────────────────────────────── */

/** Proxy a GET/HEAD /api/* request (including SSE) to Python, streaming body. */
function proxyApi(clientReq: IncomingMessage, clientRes: ServerResponse, pathname: string): void {
  // Build the upstream URL preserving the original path + query string.
  const search = clientReq.url ? url.parse(clientReq.url).search || '' : '';
  const upUrl = `${up.origin}${pathname}${search}`;

  // Forward only the meaningful headers that are actually present. Passing
  // a literal `undefined` (e.g. when the client omitted accept-encoding) makes
  // Node throw ERR_HTTP_INVALID_HEADER_VALUE.
  const headerSrc = clientReq.headers;
  const headers: Record<string, string> = {
    accept: (headerSrc.accept as string) ?? '*/*',
  };
  for (const name of ['accept-encoding', 'x-control-token'] as const) {
    if (headerSrc[name]) headers[(name as string)] = headerSrc[name] as string;
  }

  const proxyReq = httpRequest(
    upUrl,
    { method: clientReq.method || 'GET', headers },
    (upRes) => {
      // Stream the response headers back (SSE content-type included).
      clientRes.writeHead(upRes.statusCode || 502, { ...upRes.headers });
      upRes.pipe(clientRes);

      // Track "slow consumers". If the client disconnects, tear down the
      // upstream socket so the SSE tail does not keep a dead stream open.
      clientReq.on('close', () => {
        upRes.destroy();
      });
    },
  );

  proxyReq.on('error', (err) => {
    // If we already started writing, the socket is gone; just destroy.
    if (!clientRes.writableEnded) {
      json(clientRes, 502, { ok: false, error: `upstream unreachable: ${(up as { host: string }).host} (${err.message})` });
    }
  });

  if (clientReq.method !== 'HEAD') {
    clientReq.pipe(proxyReq);
  } else {
    proxyReq.end();
  }
}

/** Forward a POST control action to Python, passing the frontend's token. */
function proxyControl(clientReq: IncomingMessage, clientRes: ServerResponse, pathname: string): void {
  const search = clientReq.url ? url.parse(clientReq.url).search || '' : '';
  const upUrl = `${up.origin}${pathname}${search}`;

  const headers: Record<string, string> = {
    accept: (clientReq.headers.accept as string) ?? '*/*',
    'content-type': (clientReq.headers['content-type'] as string) ?? 'application/json',
  };
  const token = clientReq.headers['x-control-token'] as string | undefined;
  if (token) headers['x-control-token'] = token;

  const proxyReq = httpRequest(
    upUrl,
    { method: 'POST', headers },
    (upRes) => {
      clientRes.writeHead(upRes.statusCode || 502, { ...upRes.headers });
      upRes.pipe(clientRes);
      clientReq.on('close', () => upRes.destroy());
    },
  );
  proxyReq.on('error', () => {
    if (!clientRes.writableEnded) {
      json(clientRes, 502, { ok: false, error: 'upstream unreachable' });
    }
  });
  clientReq.pipe(proxyReq);
}

/* ── routing ─────────────────────────────────────────────────────────────── */

const server = createServer((req, res) => {
  const parsed = url.parse(req.url || '/');
  const pathname = parsed.pathname || '/';

  if (pathname.startsWith('/api/')) {
    const method = (req.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
      const isControl = pathname.startsWith('/api/system/');
      if (!isControl || !allowControl()) {
        // Read-only bridge: protect live state unless control is explicitly on.
        return json(res, 501, { ok: false, error: 'read-only bridge; control actions are not proxied yet' });
      }
      return proxyControl(req, res, pathname);
    }
    return proxyApi(req, res, pathname);
  }

  if (pathname.startsWith('/static/')) {
    return serveStatic(req, res, pathname.replace(/^\/static\//, '/'));
  }
  if (pathname === '/' || pathname === '/index.html' || pathname === '/strategy_explainer.html') {
    if (pathname === '/strategy_explainer.html') {
      return serveStatic(req, res, '/strategy_explainer.html');
    }
    return serveIndex(req, res);
  }

  return json(res, 404, { ok: false, error: 'not found' });
});

// Only start the HTTP listener when this file is run directly (e.g. `node
// server.ts`). Importing it from tests must not bind a port — the module also
// exports pure functions (scrapeControlToken, injectToken, allowControl). With
// type-stripped ESM `require.main` is unavailable, so we compare the entry
// script against this file's own path.
import { fileURLToPath } from 'node:url';
if (process.argv[1] && typeof process.argv[1] === 'string' && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  server.listen(PORT, '0.0.0.0', () => {
    console.log(`TS dashboard bridge listening on http://0.0.0.0:${PORT}`);
    console.log(`  -> proxying GET /api/* to ${UPSTREAM}`);
    console.log(`  -> serving static from ${STATIC_DIR}`);
  });
}