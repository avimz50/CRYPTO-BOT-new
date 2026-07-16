import { Router } from "express";
import type { Request, Response, NextFunction } from "express";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import webpush from "web-push";
import { requireSession } from "../lib/auth.js";

const router = Router();

const VAPID_PUBLIC_KEY   = process.env.VAPID_PUBLIC_KEY  ?? "";
const VAPID_PRIVATE_KEY  = process.env.VAPID_PRIVATE_KEY ?? "";
const VAPID_SUBJECT      = process.env.VAPID_SUBJECT     ?? "mailto:admin@cryptobot.local";
const INTERNAL_API_SECRET = process.env.INTERNAL_API_SECRET ?? "";

if (VAPID_PUBLIC_KEY && VAPID_PRIVATE_KEY) {
  webpush.setVapidDetails(VAPID_SUBJECT, VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY);
}

// ── Durable subscription persistence ────────────────────────────────────────
// Stored on disk so it survives process restarts.
const __dirname_here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname_here, "../../..");
const SUB_FILE = path.join(ROOT, "push_subscription.json");

function _loadSub(): webpush.PushSubscription | null {
  try {
    const raw = fs.readFileSync(SUB_FILE, "utf-8");
    return JSON.parse(raw) as webpush.PushSubscription;
  } catch {
    return null;
  }
}

function _saveSub(sub: webpush.PushSubscription | null): void {
  try {
    if (sub) {
      fs.writeFileSync(SUB_FILE, JSON.stringify(sub, null, 2), "utf-8");
    } else {
      fs.rmSync(SUB_FILE, { force: true });
    }
  } catch (e) {
    console.error("[Push] Failed to persist subscription:", e);
  }
}

let _subscription: webpush.PushSubscription | null = _loadSub();
if (_subscription) {
  console.log("[Push] Loaded persisted subscription from disk.");
}

// ── Internal-token auth (bot → server) ───────────────────────────────────────
function requireInternalToken(req: Request, res: Response, next: NextFunction): void {
  if (!INTERNAL_API_SECRET) {
    res.status(503).json({ error: "INTERNAL_API_SECRET not configured" });
    return;
  }
  const incoming = req.headers["x-internal-token"];
  if (!incoming || incoming !== INTERNAL_API_SECRET) {
    res.status(403).json({ error: "Forbidden" });
    return;
  }
  next();
}

// GET /api/push/vapid-public-key — public, no auth needed (only public key)
router.get("/vapid-public-key", (_req, res) => {
  if (!VAPID_PUBLIC_KEY) {
    res.status(503).json({ error: "Push notifications not configured" });
    return;
  }
  res.json({ publicKey: VAPID_PUBLIC_KEY });
});

// POST /api/push/subscribe — stores the browser's push subscription (session-auth)
router.post("/subscribe", requireSession, (req, res) => {
  const sub = req.body as webpush.PushSubscription | undefined;
  if (!sub?.endpoint || !sub?.keys) {
    res.status(400).json({ error: "Invalid subscription object" });
    return;
  }
  _subscription = sub;
  _saveSub(sub);
  console.log(`[Push] Subscription registered and persisted: ${sub.endpoint.slice(0, 60)}...`);
  res.json({ ok: true });
});

// DELETE /api/push/subscribe — unsubscribes (session-auth)
router.delete("/subscribe", requireSession, (_req, res) => {
  _subscription = null;
  _saveSub(null);
  console.log("[Push] Subscription cleared");
  res.json({ ok: true });
});

// GET /api/push/status — returns whether a subscription is registered (session-auth)
router.get("/status", requireSession, (_req, res) => {
  res.json({ subscribed: _subscription !== null });
});

// POST /api/push/send — internal only (Python bot → API Server)
// Body: { title: string, body: string, icon?: string, tag?: string }
router.post("/send", requireInternalToken, async (req, res) => {
  if (!_subscription) {
    res.json({ ok: false, reason: "no_subscription" });
    return;
  }
  if (!VAPID_PUBLIC_KEY || !VAPID_PRIVATE_KEY) {
    res.status(503).json({ ok: false, reason: "vapid_not_configured" });
    return;
  }

  const { title = "Trading Bot", body = "", icon, tag } = req.body as {
    title?: string; body?: string; icon?: string; tag?: string;
  };

  const payload = JSON.stringify({
    title,
    body,
    icon:  icon  ?? "/icon-192.png",
    badge: "/icon-192.png",
    tag:   tag   ?? `trade-${Date.now()}`,
    data:  { url: "/" },
  });

  try {
    await webpush.sendNotification(_subscription, payload);
    console.log(`[Push] Sent: ${title} — ${body}`);
    res.json({ ok: true });
  } catch (err: unknown) {
    const e = err as { statusCode?: number; message?: string };
    console.error(`[Push] Send failed: ${e?.statusCode} ${e?.message}`);
    if (e?.statusCode === 410 || e?.statusCode === 404) {
      _subscription = null;
      _saveSub(null);
      console.log("[Push] Subscription expired — cleared and removed from disk");
    }
    res.status(500).json({ ok: false, error: String(e?.message ?? err) });
  }
});

export default router;
