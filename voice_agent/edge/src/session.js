// A call's state travels with the browser as an encrypted token, because a free Worker keeps nothing between requests.
// AES-GCM means the caller can neither read the token nor change it, so they can't raise their own identity level.

import { newIdentity } from "./identity.js";
import { newBaseline } from "./voice.js";

const LIFETIME_MS = 2 * 3600 * 1000;
const encoder = new TextEncoder();
const decoder = new TextDecoder();

export const newSession = () => ({ identity: newIdentity(), history: [], pending_question: null, pending_level: 0, last_files: [], baseline: newBaseline() });

async function keyFrom(secret) {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(`session:${secret}`));
  return crypto.subtle.importKey("raw", digest, "AES-GCM", false, ["encrypt", "decrypt"]);
}

const toBase64Url = bytes => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromBase64Url = text => Uint8Array.from(atob(text.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));

export async function seal(session, secret) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = encoder.encode(JSON.stringify({ ...session, expires: Date.now() + LIFETIME_MS }));
  const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await keyFrom(secret), data));
  const joined = new Uint8Array(iv.length + sealed.length);
  joined.set(iv);
  joined.set(sealed, iv.length);
  return toBase64Url(joined);
}

// A missing, expired, or altered token simply starts a new call with no identity.
export async function open(token, secret) {
  if (!token) return newSession();
  try {
    const bytes = fromBase64Url(token);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes.slice(0, 12) }, await keyFrom(secret), bytes.slice(12));
    const session = JSON.parse(decoder.decode(plain));
    return session.expires > Date.now() ? session : newSession();
  } catch {
    return newSession();
  }
}
