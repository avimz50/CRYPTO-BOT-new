import { Router } from "express";
import fs from "fs";
import path from "path";
import https from "https";

const router = Router();

const ROOT = path.resolve(process.cwd(), "../..");

function readJson(filename: string): unknown {
  try {
    const raw = fs.readFileSync(path.join(ROOT, filename), "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

router.get("/trades", (_req, res) => {
  const data = readJson("active_trades.json");
  if (!data) return res.json([]);
  res.json(data);
});

router.get("/wallet", (_req, res) => {
  const data = readJson("wallet.json");
  if (!data) return res.json({ balance: 200, total_pnl: 0 });
  res.json(data);
});

router.get("/hot", (_req, res) => {
  const data = readJson("hot_candidates.json");
  if (!data) return res.json({ gainers: [], losers: [], updated_at: null });
  res.json(data);
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
  if (_fngCache.value !== 50 || now - _fngCache.ts < 3600) {
    if (now - _fngCache.ts < 3600) {
      return res.json({ value: _fngCache.value, label: _fngCache.label });
    }
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
