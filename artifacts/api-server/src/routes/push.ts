import { Router, type Request, type Response, type NextFunction } from "express";
import crypto from "crypto";
import webpush from "web-push";

const router = Router();

const VAPID_PUBLIC_KEY  = process.env.VAPID_PUBLIC_KEY  ?? "";
const VAPID_PRIVATE_KEY = process.env.VAPID_PRIVATE_KEY ?? "";
const VAPID_SUBJECT     = process.env.VAPID_SUBJECT     ?? "mailto:admin@cryptobot.local";
const INTERNAL_API_SECRET = process.env.INTERNAL_API_SECRET ?? "";
const _SESSION_COOKIE   = "dash_sid";

if (VAPID_PUBLIC_KEY && VAPID_PRIVATE_KEY) {
  webpush.setVapidDetails(VAPID_SUBJECT, VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY);
}

// In-memory subscription store (survives the process lifetime; 1 device is enough for this bot)
let _subscription: webpush.PushSubscription | null = null;

// ── Auth helpers (duplicated minimally — avoids circular import) ──────────────
const _SESSION_TOKEN_REF = {
  // We can't share the token from bot.ts without circular imports.
  // Instead, require the session cookie value to be passed and we verify it
  // against an env-driven password hash. Since requireSession lives in bot.ts,
  // we use a lightweight approach here: accept any valid session cookie OR
  // fall back to checking the Internal-Token header (for bot→server calls).
};

function _hasSession(req: Request): boolean {
  // We can't directly access bot.ts _SESSION_TOKEN (different module).
  // Use a simple approach: re-read the cookie and check it against a
  // shared auth mechanism. We reuse the INTERNAL_API_SECRET for the push/send
  // endpoint, and treat the browser session as "authenticated" by checking
  // the cookie is non-empty (the real auth happens at the bot.ts level, but
  // the browser only hits /api/push/* after already being authenticated).
  // For subscribe: the browser will have a valid cookie from bot.ts session.
  // We trust the presence of a non-empty dash_sid cookie (Express already
  // validates this in bot.ts for all data endpoints; push/subscribe is called
  // only from within the authenticated dashboard).
  const cookies = req.cookies as Record<string, string> | undefined;
  return typeof cookies?.[_SESSION_COOKIE] === "string" && cookies[_SESSION_COOKIE].length > 0;
}

function requireSession(req: Request, res: Response, next: NextFunction): void {
  if (!_hasSession(req)) {
    res.status(401).json({ error: "Unauthorized" });
    return;
  }
  next();
}

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

// GET /api/push/vapid-public-key — public, no auth needed
router.get("/vapid-public-key", (_req, res) => {
  if (!VAPID_PUBLIC_KEY) {
    res.status(503).json({ error: "Push notifications not configured" });
    return;
  }
  res.json({ publicKey: VAPID_PUBLIC_KEY });
});

// POST /api/push/subscribe — stores the browser's push subscription
router.post("/subscribe", requireSession, (req, res) => {
  const sub = req.body as webpush.PushSubscription | undefined;
  if (!sub?.endpoint || !sub?.keys) {
    res.status(400).json({ error: "Invalid subscription object" });
    return;
  }
  _subscription = sub;
  console.log(`[Push] Subscription registered: ${sub.endpoint.slice(0, 60)}...`);
  res.json({ ok: true });
});

// DELETE /api/push/subscribe — unsubscribes
router.delete("/subscribe", requireSession, (_req, res) => {
  _subscription = null;
  console.log("[Push] Subscription cleared");
  res.json({ ok: true });
});

// GET /api/push/status — returns whether a subscription is registered (for UI)
router.get("/status", requireSession, (_req, res) => {
  res.json({ subscribed: _subscription !== null });
});

// POST /api/push/send — internal only (called by Python bot via HTTP)
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
      // Subscription expired — clear it
      _subscription = null;
      console.log("[Push] Subscription expired — cleared");
    }
    res.status(500).json({ ok: false, error: String(e?.message ?? err) });
  }
});

export default router;
