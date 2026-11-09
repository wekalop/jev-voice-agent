// Gives the voice agent a stable workers.dev address. The agent still runs on your PC, and this forwards
// page and API requests to the current tunnel. Call audio goes through the relay and never passes through here.

const OFFLINE_PAGE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nabda Voice Test</title>
<style>
  :root { color-scheme: light dark; --bg: #f1f5f9; --panel: #fff; --text: #0f172a; --muted: #475569; --line: #cbd5e1; }
  @media (prefers-color-scheme: dark) { :root { --bg: #0f172a; --panel: #1e293b; --text: #f8fafc; --muted: #cbd5e1; --line: #475569; } }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px; box-sizing: border-box; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, sans-serif; }
  main { max-width: 420px; padding: 24px; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; }
  h1 { margin: 0 0 8px; font-size: 22px; } p { margin: 0; color: var(--muted); }
</style></head><body><main>
  <h1>The voice test is offline</h1>
  <p>This demo runs on a personal computer that isn't connected right now. Please ask the person who sent you this link to start it.</p>
</main></body></html>`;

function offline() {
  return new Response(OFFLINE_PAGE, { status: 503, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}

export default {
  async fetch(request, env) {
    if (!env.ORIGIN_URL) return offline();
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, env.ORIGIN_URL);
    const headers = new Headers(request.headers);
    headers.set("x-forwarded-proto", "https");
    try {
      const response = await fetch(target, {
        method: request.method,
        headers,
        body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
        redirect: "manual",
      });
      // A stopped tunnel answers with these, so the visitor gets a clear message instead of an error code.
      if ([502, 503, 504, 530].includes(response.status)) return offline();
      const output = new Response(response.body, response);
      const location = output.headers.get("location");
      if (location && location.startsWith(env.ORIGIN_URL)) {
        output.headers.set("location", location.slice(env.ORIGIN_URL.length) || "/");
      }
      output.headers.set("cache-control", "no-store");
      return output;
    } catch (error) {
      return offline();
    }
  },
};
