import { Router } from "express";
import fs from "fs";
import path from "path";
import http from "http";
import https from "https";
import { fileURLToPath } from "url";
import { osGet } from "../lib/objectStorage.js";

const router = Router();

// Flask bot base URL — defaults to local keep_alive.py on BOT_PORT.
// Set BOT_URL env var (e.g. https://python-script-bymzrkhy.replit.app) to
// proxy the dashboard to the production bot instead of a local instance.
const BOT_FLASK_PORT = parseInt(process.env.BOT_PORT ?? "8091", 10);
const BOT_BASE_URL   = new URL(process.env.BOT_URL ?? `http://localhost:${BOT_FLASK_PORT}`);
const BOT_FLASK_BASE = BOT_BASE_URL.href.replace(/\/$/, "");
const BOT_FLASK_HOST = BOT_BASE_URL.hostname;
const BOT_FLASK_PNUM = BOT_BASE_URL.port
  ? parseInt(BOT_BASE_URL.port, 10)
  : BOT_BASE_URL.protocol === "https:" ? 443 : 80;
const _botHttp = BOT_BASE_URL.protocol === "https:" ? https : (http as unknown as typeof https);

// Use import.meta.url so ROOT is always correct regardless of process.cwd().
// Compiled bundle lives at: <workspace>/artifacts/api-server/dist/index.mjs
// Three levels up from dist/ → workspace root.
const __dirname_here = path.dirname(fileURLToPath(import.meta.url));
const ROOT   = path.resolve(__dirname_here, "../../..");
const PUBLIC = path.join(ROOT, "artifacts", "bot-dashboard", "public");

function readJson(filePath: string): unknown {
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Fetch JSON from internal Flask bot server.
 * Fallback chain: Flask → Object Storage (durable) → disk cache → hardcoded default.
 *
 * Object Storage is authoritative and survives Republish; disk cache is a
 * best-effort write-through maintained by the Python bot. The hardcoded
 * default is only used when all three sources fail (e.g. first-ever boot
 * before the bot has written anything).
 *
 * @param endpoint     Flask API path, e.g. "/api/wallet"
 * @param fallbackFile Disk-cache path (artifacts/bot-dashboard/public/*.json)
 * @param fallback     Hardcoded last-resort default value
 * @param dbKey        Object Storage key (e.g. "wallet", "active_trades")
 */
function fetchFromFlask(
  endpoint: string,
  fallbackFile: string,
  fallback: unknown,
  dbKey = "",
): Promise<unknown> {
  const diskFb = () => readJson(fallbackFile) ?? fallback;

  const osFallback = async (): Promise<unknown> => {
    if (dbKey) {
      const osData = await osGet(dbKey);
      if (osData != null) return osData;
    }
    return diskFb();
  };

  return new Promise((resolve) => {
    const req = _botHttp.get(`${BOT_FLASK_BASE}${endpoint}`, { timeout: 5000 }, (r) => {
      let body = "";
      r.on("data", (c) => (body += c));
      r.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          osFallback().then(resolve);
        }
      });
    });
    req.on("error", () => osFallback().then(resolve));
    req.on("timeout", () => { req.destroy(); osFallback().then(resolve); });
  });
}

/** In-memory close-price series accumulated from active_trades polling */
const _priceHistory = new Map<string, Array<{ ts: number; price: number }>>();
const MAX_HIST = 80;

function _recordPrices(tradesPayload: unknown): void {
  const payload = tradesPayload as { trades?: Array<{ symbol: string; current_price?: number }> };
  if (!Array.isArray(payload?.trades)) return;
  const now = Date.now();
  for (const t of payload.trades) {
    if (!t.symbol || t.current_price == null) continue;
    const arr = _priceHistory.get(t.symbol) ?? [];
    // Only append if price changed or >15 s since last point
    const last = arr[arr.length - 1];
    if (!last || last.price !== t.current_price || now - last.ts > 15_000) {
      arr.push({ ts: now, price: t.current_price });
      if (arr.length > MAX_HIST) arr.splice(0, arr.length - MAX_HIST);
    }
    _priceHistory.set(t.symbol, arr);
  }
}

router.get("/trades", async (_req, res) => {
  const data = await fetchFromFlask("/api/trades", path.join(PUBLIC, "active_trades.json"), { updated: null, count: 0, trades: [] }, "active_trades");
  _recordPrices(data);
  res.json(data);
});

/** Alias: /active_trades → same as /trades (required by dashboard spec) */
router.get("/active_trades", async (_req, res) => {
  const data = await fetchFromFlask("/api/trades", path.join(PUBLIC, "active_trades.json"), { updated: null, count: 0, trades: [] }, "active_trades");
  _recordPrices(data);
  res.json(data);
});

/** Close-price series for a single symbol — used by dashboard mini charts */
router.get("/price_history/:symbol", (req, res) => {
  const symbol = decodeURIComponent(req.params.symbol ?? "");
  const hist   = _priceHistory.get(symbol) ?? [];
  res.json({ symbol, count: hist.length, series: hist });
});

/** /status — combined connection/equity/FNG snapshot for the top status bar */
router.get("/status", async (_req, res) => {
  const [walletRaw, tradesRaw, btcPrice] = await Promise.all([
    fetchFromFlask("/api/wallet",  path.join(PUBLIC, "wallet.json"),       { balance: 200, starting: 200, total_pnl: 0, equity: 200, available_balance: 200, locked_balance: 0, unrealized_pnl: 0 }, "wallet"),
    fetchFromFlask("/api/trades",  path.join(PUBLIC, "active_trades.json"), { count: 0, trades: [] }, "active_trades"),
    fetchBtcPrice(),
  ]);
  const w = walletRaw as Record<string, number>;
  const t = tradesRaw as Record<string, number>;
  // FNG: use cache; if stale (>15 min) kick off a background refresh so next
  // /status call gets the updated value. Cache is also warmed up on server start.
  if (Date.now() / 1000 - _fngCache.ts >= 900) _refreshFngCache();
  res.json({
    connected:    true,
    exchange:     "Bitget",
    mode:         "VIRTUAL",
    equity:       w.equity       ?? w.balance ?? 200,
    available:    w.available_balance ?? w.balance ?? 200,
    starting:     w.starting     ?? 200,
    unrealized:   w.unrealized_pnl   ?? 0,
    realized:     w.total_pnl    ?? 0,
    locked:       w.locked_balance   ?? 0,
    fng_value:    _fngCache.value,
    fng_label:    _fngCache.label,
    active_trades: Number(t.count ?? 0),
    btc_price:    btcPrice,
    ts: Date.now(),
  });
});

router.get("/wallet", async (_req, res) => {
  const data = await fetchFromFlask("/api/wallet", path.join(PUBLIC, "wallet.json"), { balance: 200, starting: 200, total_pnl: 0, trades_opened: 0, equity_history: [] }, "wallet");
  res.json(data);
});

router.get("/hot", async (_req, res) => {
  const data = await fetchFromFlask("/api/hot", path.join(PUBLIC, "hot_candidates.json"), { updated: null, count: 0, candidates: [] });
  res.json(data);
});

router.get("/trade_audit", async (_req, res) => {
  const data = await fetchFromFlask("/api/trade_audit", path.join(PUBLIC, "trade_audit.json"), { updated: null, count: 0, trades: [] }, "trade_audit");
  res.json(data);
});

// /api/debug removed — exposed internal paths, filesystem state, and crash logs
// to unauthenticated public requests (security hardening).

router.get("/last_scan", async (_req, res) => {
  const data = await fetchFromFlask("/api/last_scan", path.join(PUBLIC, "last_scan_results.json"), null);
  if (!data) { res.status(404).json({ error: "No scan report yet." }); return; }
  res.json(data);
});

/** Proxy POST to Flask bot — used by Make.com incoming webhook */
router.post("/make", (req, res) => {
  const body = JSON.stringify(req.body ?? {});
  const options = {
    hostname: BOT_FLASK_HOST,
    port: BOT_FLASK_PNUM,
    path: "/api/make",
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body),
    },
    timeout: 8000,
  };
  const flaskReq = _botHttp.request(options, (flaskRes) => {
    let data = "";
    flaskRes.on("data", (chunk) => (data += chunk));
    flaskRes.on("end", () => {
      res.status(flaskRes.statusCode ?? 200);
      try { res.json(JSON.parse(data)); } catch { res.send(data); }
    });
  });
  flaskReq.on("error", (err) => res.status(502).json({ ok: false, error: String(err) }));
  flaskReq.on("timeout", () => { flaskReq.destroy(); res.status(504).json({ ok: false, error: "timeout" }); });
  flaskReq.write(body);
  flaskReq.end();
});

// /api/bot_log removed — exposed stdout/stderr/crash logs and restart history
// to unauthenticated public requests (security hardening).

router.get("/audit", (_req, res) => {
  const filePath = path.join(ROOT, "audit_report.json");
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    const data = JSON.parse(raw);
    res.setHeader("Content-Disposition", 'attachment; filename="trading_audit_report.json"');
    res.json(data);
  } catch {
    res.status(404).json({ error: "Audit report not yet generated. Runs at 08:00 and 20:00." });
  }
});

// Sync — dashboard SYNC button → Flask /api/sync → sends /status to Telegram
router.post("/sync", (req, res) => {
  const body = JSON.stringify(req.body ?? {});
  const options = {
    hostname: BOT_FLASK_HOST,
    port: BOT_FLASK_PNUM,
    path: "/api/sync",
    method: "POST",
    headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
    timeout: 8000,
  };
  const proxyReq = _botHttp.request(options, (r) => {
    let data = "";
    r.on("data", (c) => (data += c));
    r.on("end", () => {
      try { res.status(r.statusCode ?? 200).json(JSON.parse(data)); }
      catch { res.status(500).json({ error: "invalid response from bot" }); }
    });
  });
  proxyReq.on("error", () => res.status(503).json({ ok: false, error: "הבוט לא מחובר" }));
  proxyReq.on("timeout", () => { proxyReq.destroy(); res.status(504).json({ ok: false, error: "timeout" }); });
  proxyReq.write(body);
  proxyReq.end();
});

// Slots — proxy GET/POST to Flask bot
router.get("/slots", async (_req, res) => {
  const data = await fetchFromFlask("/api/slots", "", { max_trades: 5, active_trades: 0, open_slots: 5, min: 1, max: 5 });
  res.json(data);
});

router.post("/slots", (req, res) => {
  const body = JSON.stringify(req.body ?? {});
  const options = {
    hostname: BOT_FLASK_HOST,
    port: BOT_FLASK_PNUM,
    path: "/api/slots",
    method: "POST",
    headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
  };
  const proxyReq = _botHttp.request(options, (r) => {
    let data = "";
    r.on("data", (c) => (data += c));
    r.on("end", () => {
      try { res.status(r.statusCode ?? 200).json(JSON.parse(data)); }
      catch { res.status(500).json({ error: "invalid response from bot" }); }
    });
  });
  proxyReq.on("error", () => res.status(503).json({ ok: false, error: "הבוט לא מחובר — נסה שוב בעוד רגע" }));
  proxyReq.write(body);
  proxyReq.end();
});

// FNG Settings — proxy to Flask bot
router.get("/fng_settings", async (_req, res) => {
  const data = await fetchFromFlask("/api/fng_settings", "", {
    extreme_fear: 13, fear: 30, greed: 70,
    ranges: {
      extreme_fear: { min: 5,  max: 25, desc: 'Kill-Switch — אין עסקאות חדשות' },
      fear:         { min: 15, max: 45, desc: 'Fear — RSI<30 + SL+1%' },
      greed:        { min: 55, max: 85, desc: 'Greed — פוזיציה 60% + BE מוקדם' },
    }
  });
  res.json(data);
});

router.post("/fng_settings", (req, res) => {
  const body = JSON.stringify(req.body);
  const options = {
    hostname: BOT_FLASK_HOST,
    port: BOT_FLASK_PNUM,
    path: "/api/fng_settings",
    method: "POST",
    headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
  };
  const proxyReq = _botHttp.request(options, (r) => {
    let data = "";
    r.on("data", (c) => (data += c));
    r.on("end", () => {
      try { res.status(r.statusCode ?? 200).json(JSON.parse(data)); }
      catch { res.status(500).json({ error: "invalid response from bot" }); }
    });
  });
  proxyReq.on("error", (e) => res.status(503).json({ error: String(e) }));
  proxyReq.write(body);
  proxyReq.end();
});

// BTC spot price — cached 15 s (CoinGecko primary, Kraken fallback)
let _btcCache: { price: number; ts: number } = { price: 0, ts: 0 };

function _httpsGet(url: string, timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { timeout: timeoutMs }, (r) => {
      let body = "";
      r.on("data", (c: string) => (body += c));
      r.on("end", () => resolve(body));
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("timeout")); });
  });
}

async function fetchBtcPrice(): Promise<number> {
  const now = Date.now();
  if (_btcCache.price > 0 && now - _btcCache.ts < 15_000) return _btcCache.price;
  // Try CoinGecko first, fall back to Kraken
  const sources: Array<{ url: string; parse: (body: string) => number }> = [
    {
      url: "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
      parse: (b) => JSON.parse(b).bitcoin.usd,
    },
    {
      url: "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
      parse: (b) => parseFloat(JSON.parse(b).result.XXBTZUSD.c[0]),
    },
  ];
  for (const src of sources) {
    try {
      const body = await _httpsGet(src.url, 4000);
      const p = src.parse(body);
      if (p > 0) { _btcCache = { price: p, ts: now }; return p; }
    } catch { /* try next source */ }
  }
  return _btcCache.price; // return stale cache or 0
}

// FNG — cached 15 min
let _fngCache: { value: number; label: string; ts: number; updated_at: number; time_until_update: number } = {
  value: 50, label: "Neutral", ts: 0, updated_at: 0, time_until_update: 0
};

/** Fetches FNG from alternative.me and updates _fngCache. Resolves when done. */
function _refreshFngCache(): Promise<void> {
  return new Promise((resolve) => {
    const now = Date.now() / 1000;
    const url = "https://api.alternative.me/fng/?limit=1";
    https.get(url, (r) => {
      let body = "";
      r.on("data", (c) => (body += c));
      r.on("end", () => {
        try {
          const d = JSON.parse(body).data[0];
          _fngCache = {
            value: parseInt(d.value),
            label: d.value_classification,
            ts: now,
            updated_at: parseInt(d.timestamp),
            time_until_update: parseInt(d.time_until_update),
          };
        } catch {
          if (_fngCache.ts === 0) _fngCache = { value: 50, label: "Neutral", ts: now - 800, updated_at: now, time_until_update: 0 };
        }
        resolve();
      });
    }).on("error", () => {
      if (_fngCache.ts === 0) _fngCache = { value: 50, label: "Neutral", ts: now - 800, updated_at: now, time_until_update: 0 };
      resolve();
    });
  });
}

// Warm up FNG cache immediately on server start (so /status never shows 50 on first load)
_refreshFngCache();

router.get("/fng", async (_req, res) => {
  const now = Date.now() / 1000;
  if (now - _fngCache.ts < 900) {
    res.json({
      value: _fngCache.value,
      label: _fngCache.label,
      updated_at: _fngCache.updated_at,
      time_until_update: _fngCache.time_until_update,
    });
    return;
  }
  await _refreshFngCache();
  res.json({
    value: _fngCache.value,
    label: _fngCache.label,
    updated_at: _fngCache.updated_at,
    time_until_update: _fngCache.time_until_update,
  });
});

// Telegram webhook — receives Telegram updates in production (webhook mode)
// Proxies the POST body straight to Flask /api/tg_hook for bot.process_new_updates()
router.post("/tg_hook", (req, res) => {
  const body = JSON.stringify(req.body ?? {});
  const options = {
    hostname: BOT_FLASK_HOST,
    port: BOT_FLASK_PNUM,
    path: "/api/tg_hook",
    method: "POST",
    headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
    timeout: 5000, // always return to Telegram within 5s — prevents retry storms
  };
  const proxyReq = _botHttp.request(options, (r) => {
    let data = "";
    r.on("data", (c) => (data += c));
    r.on("end", () => res.status(r.statusCode ?? 200).json({ ok: true }));
  });
  proxyReq.on("error", () => { if (!res.headersSent) res.status(200).json({ ok: false }); }); // always 200 so Telegram doesn't retry
  proxyReq.on("timeout", () => { proxyReq.destroy(); if (!res.headersSent) res.status(200).json({ ok: false }); });
  proxyReq.write(body);
  proxyReq.end();
});

export default router;
