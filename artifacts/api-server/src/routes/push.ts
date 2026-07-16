import { Router } from "express";
import type { Request, Response, NextFunction } from "express";
import webpush from "web-push";
import { requireSession } from "../lib/auth.js";
import { osGet, osSet } from "../lib/objectStorage.js";

const router = Router();

const VAPID_PUBLIC_KEY    = process.env.VAPID_PUBLIC_KEY  ?? "";
const VAPID_PRIVATE_KEY   = process.env.VAPID_PRIVATE_KEY ?? "";
const VAPID_SUBJECT       = process.env.VAPID_SUBJECT     ?? "mailto:admin@cryptobot.local";
const INTERNAL_API_SECRET = process.env.INTERNAL_API_SECRET ?? "";

if (VAPID_PUBLIC_KEY && VAPID_PRIVATE_KEY) {
  webpush.setVapidDetails(VAPID_SUBJECT, VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY);
}

// ── Durable subscription persistence via Object Storage ─────────────────────
// Stored in Object Storage under key "push_subscription" so it survives
// process restarts and redeployments. Falls back gracefully if OS unavailable.

const OS_KEY = "push_subscription";

// In-memory cache to avoid round-trips on every /send call
let _subscription: webpush.PushSubscription | null = null;
let _loaded = false;

async function _getSub(): Promise<webpush.PushSubscription | null> {
  if (_loaded) return _subscription;
  const raw = await osGet(OS_KEY);
  _loaded = true;
  if (raw && typeof raw === "object" && "endpoint" in (raw as object)) {
    _subscription = raw as webpush.PushSubscription;
    console.log("[Push] Loaded subscription from Object Storage.");
  }
  return _subscription;
}

async function _setSub(sub: webpush.PushSubscription | null): Promise<void> {
  _subscription = sub;
  _loaded = true;
  const ok = await osSet(OS_KEY, sub);
  if (!ok) {
    console.warn("[Push] Object Storage unavailable — subscription stored in-memory only (non-durable).");
  }
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
router.post("/subscribe", requireSession, async (req, res) => {
  const sub = req.body as webpush.PushSubscription | undefined;
  if (!sub?.endpoint || !sub?.keys) {
    res.status(400).json({ error: "Invalid subscription object" });
    return;
  }
  await _setSub(sub);
  console.log(`[Push] Subscription registered: ${sub.endpoint.slice(0, 60)}...`);
  res.json({ ok: true });
});

// DELETE /api/push/subscribe — unsubscribes (session-auth)
router.delete("/subscribe", requireSession, async (_req, res) => {
  await _setSub(null);
  console.log("[Push] Subscription cleared");
  res.json({ ok: true });
});

// GET /api/push/status — returns whether a subscription is registered (session-auth)
router.get("/status", requireSession, async (_req, res) => {
  const sub = await _getSub();
  res.json({ subscribed: sub !== null });
});

// POST /api/push/send — internal only (Python bot → API Server)
// Body: { title: string, body: string, icon?: string, tag?: string }
router.post("/send", requireInternalToken, async (req, res) => {
  const sub = await _getSub();
  if (!sub) {
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
    await webpush.sendNotification(sub, payload);
    console.log(`[Push] Sent: ${title} — ${body}`);
    res.json({ ok: true });
  } catch (err: unknown) {
    const e = err as { statusCode?: number; message?: string };
    console.error(`[Push] Send failed: ${e?.statusCode} ${e?.message}`);
    if (e?.statusCode === 410 || e?.statusCode === 404) {
      await _setSub(null);
      console.log("[Push] Subscription expired — cleared from Object Storage");
    }
    res.status(500).json({ ok: false, error: String(e?.message ?? err) });
  }
});

export default router;
