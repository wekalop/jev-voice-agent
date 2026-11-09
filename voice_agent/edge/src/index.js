// The Worker: password gate, the call page's files, and the speech and turn routes.

import { kb } from "./kb.js";
import { GREETING } from "./prompts.js";
import { makeJev, makeLlm, speak, transcribe } from "./services.js";
import { open, seal } from "./session.js";
import { runTurn } from "./turn.js";
import { describeAndLearn, validSample } from "./voice.js";

const COOKIE = "nabda_access";
const MAX_AUDIO_BYTES = 2 * 1024 * 1024;
const MAX_SPOKEN_CHARS = 500;
const encoder = new TextEncoder();

const LOGIN_PAGE = message => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nabda Voice Test</title>
<style>
  :root { color-scheme: light dark; --bg: #f1f5f9; --panel: #fff; --text: #0f172a; --muted: #475569; --line: #cbd5e1; --accent: #15803d; --on: #fff; --bad: #b91c1c; }
  @media (prefers-color-scheme: dark) { :root { --bg: #0f172a; --panel: #1e293b; --text: #f8fafc; --muted: #cbd5e1; --line: #475569; --accent: #22c55e; --on: #052e16; --bad: #fca5a5; } }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, sans-serif; }
  form { width: 100%; max-width: 380px; display: grid; gap: 12px; padding: 24px; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; }
  h1 { margin: 0; font-size: 22px; } p { margin: 0; color: var(--muted); }
  label { font-weight: 600; }
  input, button { font: inherit; min-height: 48px; width: 100%; border-radius: 8px; }
  input { padding: 0 12px; border: 1px solid var(--line); background: transparent; color: inherit; margin-top: 4px; }
  button { border: 0; background: var(--accent); color: var(--on); font-weight: 600; cursor: pointer; }
  .error { color: var(--bad); }
</style></head><body>
<form method="post" action="/login">
  <h1>Nabda voice test</h1>
  <p>Enter the password you were given. Nabda is a fictional company.</p>
  ${message ? `<p class="error" role="alert">${message}</p>` : ""}
  <label>Password<input type="password" name="password" autocomplete="current-password" required autofocus></label>
  <button type="submit">Continue</button>
</form></body></html>`;

const html = (body, status = 200) => new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

async function signature(env) {
  const key = await crypto.subtle.importKey("raw", encoder.encode(env.SESSION_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signed = new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(`access:${env.ACCESS_PASSWORD}`)));
  return Array.from(signed, byte => byte.toString(16).padStart(2, "0")).join("");
}

function sameText(a, b) {
  const [x, y] = [encoder.encode(a), encoder.encode(b)];
  if (x.length !== y.length) return false;
  let difference = 0;
  for (let i = 0; i < x.length; i++) difference |= x[i] ^ y[i];
  return difference === 0;
}

async function signedIn(request, env) {
  const cookies = Object.fromEntries((request.headers.get("cookie") || "").split(/;\s*/).map(part => part.split("=")));
  return sameText(cookies[COOKIE] || "", await signature(env));
}

async function login(request, env) {
  const form = await request.formData();
  if (!sameText(String(form.get("password") || ""), env.ACCESS_PASSWORD)) {
    return html(LOGIN_PAGE("That password isn't right. Please try again."), 401);
  }
  const secure = new URL(request.url).protocol === "https:" ? "; Secure" : "";
  return new Response(null, {
    status: 303,
    headers: { location: "/", "set-cookie": `${COOKIE}=${await signature(env)}; HttpOnly; Path=/; SameSite=Lax; Max-Age=604800${secure}` },
  });
}

async function turn(request, env, ctx) {
  const session = await open(request.headers.get("x-session"), env.SESSION_SECRET);
  const isAudio = (request.headers.get("content-type") || "").startsWith("audio/");
  let audio = null, typed = "", voice = null;
  if (isAudio) {
    audio = await request.arrayBuffer();
    if (audio.byteLength > MAX_AUDIO_BYTES) return json({ detail: "That was too long. Please say it in a shorter way." }, 413);
  } else {
    typed = String((await request.json()).text || "").slice(0, 1000);
  }
  try {
    voice = validSample(JSON.parse(request.headers.get("x-voice") || "null"));
  } catch {
    voice = null;
  }
  // What the caller said just before pausing, if the agent hadn't replied yet. It's their own words either way.
  const prefix = decodeURIComponent(request.headers.get("x-prefix") || "").slice(0, 1000);

  const stream = new TransformStream();
  const writer = stream.writable.getWriter();
  const send = event => writer.write(encoder.encode(`${JSON.stringify(event)}\n`));

  const work = (async () => {
    try {
      const heard = isAudio ? await transcribe(env, audio) : typed;
      const text = `${prefix} ${/\w/.test(heard) ? heard : ""}`.trim();
      if (!/\w/.test(text)) {
        await send({ type: "empty" });
        return;
      }
      const [voiceCues, voiceLabel] = describeAndLearn(session.baseline, voice, heard.split(/\s+/).length);
      await send({ type: "heard", text, voice: voiceLabel });
      const deps = { jev: makeJev(env), llm: makeLlm(env), kb, env };
      for await (const event of runTurn({ session, text, voiceCues, deps })) await send(event);
      await send({ type: "done", session: await seal(session, env.SESSION_SECRET) });
    } catch (error) {
      console.error("Turn failed", error?.message);
      const detail = error?.rateLimited
        ? "This test line has reached its free usage limit for the moment. Please try again in a minute."
        : "Something went wrong on my side. Please try again.";
      await send({ type: "error", detail });
    } finally {
      await writer.close();
    }
  })();
  ctx.waitUntil(work);
  return new Response(stream.readable, { headers: { "content-type": "application/x-ndjson", "cache-control": "no-store" } });
}

async function speech(request, env) {
  const text = String((await request.json()).text || "").trim();
  if (!text || text.length > MAX_SPOKEN_CHARS) return json({ detail: "Nothing to say." }, 400);
  const response = await speak(env, text);
  if (!response.ok) {
    console.error("Voice failed", response.status);
    return json({ detail: "The voice service didn't answer." }, 502);
  }
  return new Response(response.body, { headers: { "content-type": "application/octet-stream", "cache-control": "no-store" } });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (!env.ACCESS_PASSWORD || !env.SESSION_SECRET) return html(LOGIN_PAGE("This test line isn't set up yet."), 503);
    if (url.pathname === "/login") {
      return request.method === "POST" ? login(request, env) : html(LOGIN_PAGE(""));
    }
    if (!(await signedIn(request, env))) {
      if (url.pathname.startsWith("/api/")) return json({ detail: "Sign in first." }, 401);
      return new Response(null, { status: 303, headers: { location: "/login" } });
    }
    if (request.method === "POST" && url.pathname === "/api/turn") return turn(request, env, ctx);
    if (request.method === "POST" && url.pathname === "/api/speak") return speech(request, env);
    if (request.method === "GET" && url.pathname === "/api/config") return json({ greeting: GREETING });
    return env.ASSETS.fetch(request);
  },
};
