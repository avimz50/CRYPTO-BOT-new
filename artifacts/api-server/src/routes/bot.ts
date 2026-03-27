import { Router } from "express";
import fs from "fs";
import path from "path";
import http from "http";
import https from "https";

const router = Router();

// Flask bot server (keep_alive.py) always runs on BOT_PORT (default 8091)
// Express api-server runs on PORT (8080 in prod) — no conflict
const BOT_FLASK_PORT = parseInt(process.env.BOT_PORT ?? "8091", 10);
const BOT_FLASK_BASE = `http://localhost:${BOT_FLASK_PORT}`;

// workspace root = two levels above artifacts/api-server
const ROOT   = path.resolve(process.cwd(), "../..");
const PUBLIC = path.join(ROOT, "artifacts", "bot-dashboard", "public");

function readJson(filePath: string): unknown {
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** Fetch JSON from internal Flask bot server, fallback to reading disk file */
function fetchFromFlask(endpoint: string, fallbackFile: string, fallback: unknown): Promise<unknown> {
  return new Promise((resolve) => {
    const req = http.get(`${BOT_FLASK_BASE}${endpoint}`, { timeout: 2000 }, (r) => {
      let body = "";
      r.on("data", (c) => (body += c));
      r.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve(readJson(fallbackFile) ?? fallback);
        }
      });
    });
    req.on("error", () => resolve(readJson(fallbackFile) ?? fallback));
    req.on("timeout", () => { req.destroy(); resolve(readJson(fallbackFile) ?? fallback); });
  });
}

router.get("/trades", async (_req, res) => {
  const data = await fetchFromFlask("/api/trades", path.join(PUBLIC, "active_trades.json"), { updated: null, count: 0, trades: [] });
  res.json(data);
});

router.get("/wallet", async (_req, res) => {
  const data = await fetchFromFlask("/api/wallet", path.join(PUBLIC, "wallet.json"), { balance: 200, starting: 200, total_pnl: 0, trades_opened: 0, equity_history: [] });
  res.json(data);
});

router.get("/hot", async (_req, res) => {
  const data = await fetchFromFlask("/api/hot", path.join(PUBLIC, "hot_candidates.json"), { updated: null, count: 0, candidates: [] });
  res.json(data);
});

router.get("/debug", async (_req, res) => {
  const data = await fetchFromFlask("/api/debug", "", null);
  res.json({
    flask_port: BOT_FLASK_PORT,
    flask_response: data,
    root: ROOT,
    public: PUBLIC,
    wallet_exists: fs.existsSync(path.join(PUBLIC, "wallet.json")),
    trades_exists: fs.existsSync(path.join(PUBLIC, "active_trades.json")),
    hot_exists:    fs.existsSync(path.join(PUBLIC, "hot_candidates.json")),
    cwd: process.cwd(),
  });
});

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

// FNG — cached 1h
let _fngCache: { value: number; label: string; ts: number } = { value: 50, label: "Neutral", ts: 0 };

router.get("/fng", (_req, res) => {
  const now = Date.now() / 1000;
  if (now - _fngCache.ts < 3600) {
    return res.json({ value: _fngCache.value, label: _fngCache.label });
  }
  const url = "https://api.alternative.me/fng/?limit=1";
  https.get(url, (r) => {
    let body = "";
    r.on("data", (c) => (body += c));
    r.on("end", () => {
      try {
        const d = JSON.parse(body).data[0];
        _fngCache = { value: parseInt(d.value), label: d.value_classification, ts: now };
      } catch {
        if (_fngCache.ts === 0) _fngCache = { value: 50, label: "Neutral", ts: now - 3500 };
      }
      res.json({ value: _fngCache.value, label: _fngCache.label });
    });
  }).on("error", () => {
    if (_fngCache.ts === 0) _fngCache = { value: 50, label: "Neutral", ts: now - 3500 };
    res.json({ value: _fngCache.value, label: _fngCache.label });
  });
});

export default router;
