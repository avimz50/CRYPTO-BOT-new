/**
 * Shared session auth — single source of truth for the session cookie check.
 * Both bot.ts and push.ts import from here so they validate against the same token.
 */
import crypto from "crypto";
import type { Request, Response, NextFunction } from "express";

export const SESSION_COOKIE = "dash_sid";

// One random token per process lifetime — issued via POST /api/_login only.
export const SESSION_TOKEN = crypto.randomBytes(32).toString("hex");

export function requireSession(req: Request, res: Response, next: NextFunction): void {
  const cookies = req.cookies as Record<string, string> | undefined;
  const token   = cookies?.[SESSION_COOKIE];
  if (!token || token !== SESSION_TOKEN || !SESSION_TOKEN) {
    res.status(401).json({ error: "Unauthorized" });
    return;
  }
  next();
}
