/**
 * Lightweight Object Storage reader for the api-server fallback layer.
 *
 * Uses the same Replit GCS sidecar auth as the Python bot's state_store.py.
 * Keys map identically: key "wallet" → "<PRIVATE_OBJECT_DIR>/state/wallet.json"
 *
 * Call order in fetchFromFlask: Flask → Object Storage → disk → hardcoded default
 */
import { Storage } from "@google-cloud/storage";
import { IdentityPoolClient } from "google-auth-library";

const BUCKET_ID   = process.env.DEFAULT_OBJECT_STORAGE_BUCKET_ID ?? "";
const PRIVATE_DIR = process.env.PRIVATE_OBJECT_DIR ?? "";

let _storage: Storage | null = null;

function _getStorage(): Storage | null {
  if (!BUCKET_ID) return null;
  if (_storage) return _storage;
  try {
    const authClient = new IdentityPoolClient({
      audience: "replit",
      subjectTokenType: "urn:ietf:params:oauth:token-type:access_token",
      tokenUrl: "http://127.0.0.1:1106/token",
      credentialSource: {
        url: "http://127.0.0.1:1106/credential",
        format: {
          type: "json",
          subject_token_field_name: "access_token",
        },
      },
    });
    // Cast needed: @google-cloud/storage bundles google-auth-library@9 internally,
    // but we import from google-auth-library@10. The runtime interface is compatible.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    _storage = new Storage({ authClient: authClient as any });
    return _storage;
  } catch {
    return null;
  }
}

function _objectName(key: string): string {
  // Mirror Python logic in state_store._object_name(key)
  // PRIVATE_OBJECT_DIR: e.g. "/<bucket>/.private" → base = ".private"
  const base = PRIVATE_DIR.replace(/^\/[^/]+\/?/, "").replace(/\/$/, "");
  return `${base ? base + "/" : ""}state/${key}.json`;
}

/**
 * Read a state key from Object Storage. Returns parsed JSON or null on error.
 * Times out after `timeoutMs` ms (default 3000).
 */
export async function osGet(key: string, timeoutMs = 3000): Promise<unknown> {
  const storage = _getStorage();
  if (!storage) return null;
  try {
    const objectName = _objectName(key);
    const race = Promise.race([
      storage.bucket(BUCKET_ID).file(objectName).download(),
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
