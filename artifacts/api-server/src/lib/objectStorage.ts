/**
 * Lightweight Object Storage reader/writer for the api-server.
 *
 * Uses the Replit GCS sidecar auth — credentials passed directly to
 * @google-cloud/storage (same pattern as the official Replit template).
 * Keys map to: "<bucket>/.private/state/<key>.json"
 */
import { Storage } from "@google-cloud/storage";

const REPLIT_SIDECAR = "http://127.0.0.1:1106";
const BUCKET_ID      = process.env.DEFAULT_OBJECT_STORAGE_BUCKET_ID ?? "";
const PRIVATE_DIR    = process.env.PRIVATE_OBJECT_DIR ?? "";

// Use the same credential shape as the official Replit objectStorage template.
// Do NOT use IdentityPoolClient — it has google-auth-library version conflicts.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _storage = BUCKET_ID ? new Storage({
  credentials: {
    type: "external_account",
    audience: "replit",
    subject_token_type: "access_token",
    token_url: `${REPLIT_SIDECAR}/token`,
    credential_source: {
      url: `${REPLIT_SIDECAR}/credential`,
      format: { type: "json", subject_token_field_name: "access_token" },
    },
    universe_domain: "googleapis.com",
  } as any,
  projectId: "",
} as any) : null;

function _objectName(key: string): string {
  // Mirror Python state_store._object_name():
  // PRIVATE_OBJECT_DIR is like "/<bucket>/.private" — strip leading "/<bucket>/"
  const base = PRIVATE_DIR.replace(/^\/[^/]+\/?/, "").replace(/\/$/, "");
  return `${base ? base + "/" : ""}state/${key}.json`;
}

/**
 * Read a state key from Object Storage. Returns parsed JSON or null on error.
 * Times out after `timeoutMs` ms (default 3000).
 */
export async function osGet(key: string, timeoutMs = 3000): Promise<unknown> {
  if (!_storage) return null;
  try {
    const race = Promise.race([
      _storage.bucket(BUCKET_ID).file(_objectName(key)).download(),
      new Promise<never>((_, rej) =>
        setTimeout(() => rej(new Error("os_timeout")), timeoutMs),
      ),
    ]);
    const [contents] = (await race) as [Buffer];
    return JSON.parse(contents.toString("utf-8"));
  } catch {
    return null;
  }
}

/**
 * Write a value to Object Storage under the given key.
 * Pass null to delete the object.
 * Times out after `timeoutMs` ms (default 5000).
 */
export async function osSet(key: string, value: unknown, timeoutMs = 5000): Promise<boolean> {
  if (!_storage) return false;
  try {
    const file = _storage.bucket(BUCKET_ID).file(_objectName(key));
    if (value === null) {
      await Promise.race([
        file.delete({ ignoreNotFound: true }),
        new Promise<never>((_, rej) => setTimeout(() => rej(new Error("os_timeout")), timeoutMs)),
      ]);
    } else {
      const buf = Buffer.from(JSON.stringify(value), "utf-8");
      await Promise.race([
        file.save(buf, { contentType: "application/json" }),
        new Promise<never>((_, rej) => setTimeout(() => rej(new Error("os_timeout")), timeoutMs)),
      ]);
    }
    return true;
  } catch (err) {
    console.error("[ObjectStorage] osSet error:", (err as Error)?.message ?? err);
    return false;
  }
}
