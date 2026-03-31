import { Router } from "express";
import fs from "fs";
import path from "path";
import http from "http";
import https from "https";
import { fileURLToPath } from "url";

const router = Router();

// Flask bot server (keep_alive.py) always runs on BOT_PORT (default 8091)
// Express api-server runs on PORT (8080 in prod) — no conflict
const BOT_FLASK_PORT = parseInt(process.env.BOT_PORT ?? "8091", 10);
const BOT_FLASK_BASE = `http://localhost:${BOT_FLASK_PORT}`;

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

/** Fetch JSON from internal Flask bot server, fallback to reading disk file */
function fetchFromFlask(endpoint: string, fallbackFile: string, fallback: unknown): Promise<unknown> {
  return new Promise((resolve) => {
    const req = http.get(`${BOT_FLASK_BASE}${endpoint}`, { timeout: 5000 }, (r) => {
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

router.get("/last_scan", async (_req, res) => {
  const data = await fetchFromFlask("/api/last_scan", path.join(PUBLIC, "last_scan_results.json"), null);
  if (!data) return res.status(404).json({ error: "No scan report yet." });
  res.json(data);
});

router.get("/bot_log", (_req, res) => {
  try {
    const stdout = fs.existsSync("/tmp/bot_stdout.log")
      ? fs.readFileSync("/tmp/bot_stdout.log", "utf-8").slice(-8000)
      : "(no stdout log yet)";
    const stderr = fs.existsSync("/tmp/bot_stderr.log")
      ? fs.readFileSync("/tmp/bot_stderr.log", "utf-8").slice(-8000)
      : "(no stderr log yet)";
    const crash = fs.existsSync("/tmp/bot_crash.log")
      ? fs.readFileSync("/tmp/bot_crash.log", "utf-8")
      : null;
    res.json({ stdout, stderr, crash });
  } catch (e) {
    res.json({ stdout: "", stderr: String(e), crash: null });
  }
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

// FNG — cached 15 min
let _fngCache: { value: number; label: string; ts: number; updated_at: number; time_until_update: number } = {
  value: 50, label: "Neutral", ts: 0, updated_at: 0, time_until_update: 0
};

router.get("/fng", (_req, res) => {
  const now = Date.now() / 1000;
  if (now - _fngCache.ts < 900) {
    return res.json({
      value: _fngCache.value,
      label: _fngCache.label,
      updated_at: _fngCache.updated_at,
      time_until_update: _fngCache.time_until_update,
    });
  }
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
      res.json({
        value: _fngCache.value,
        label: _fngCache.label,
        updated_at: _fngCache.updated_at,
        time_until_update: _fngCache.time_until_update,
      });
    });
  }).on("error", () => {
    if (_fngCache.ts === 0) _fngCache = { value: 50, label: "Neutral", ts: now - 800, updated_at: now, time_until_update: 0 };
    res.json({
      value: _fngCache.value,
      label: _fngCache.label,
      updated_at: _fngCache.updated_at,
      time_until_update: _fngCache.time_until_update,
    });
  });
});

export default router;
