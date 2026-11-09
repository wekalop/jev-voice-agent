"""Password gate for a hosted agent. With no ACCESS_PASSWORD set, as on your own machine, it stays open."""

import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = "nabda_access"
MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 600
OPEN_PATHS = {"/login"}

LOGIN_PAGE = """<!doctype html>
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
  ERROR_SLOT
  <label>Password<input type="password" name="password" autocomplete="current-password" required autofocus></label>
  <button type="submit">Continue</button>
</form></body></html>"""


def password() -> str:
    return os.environ.get("ACCESS_PASSWORD", "").strip()


def hosted() -> bool:
    return bool(os.environ.get("SPACE_ID"))


def check_startup() -> None:
    """A hosted agent spends real API allowances, so it refuses to run without a password."""
    if hosted() and not password():
        raise RuntimeError("ACCESS_PASSWORD must be set when hosting. Add it to the Space's secrets.")


def _session_value() -> str:
    return hmac.new(password().encode(), b"nabda-voice-session", hashlib.sha256).hexdigest()


def _signed_in(request: Request) -> bool:
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _session_value())


_failures: list[float] = []


def _locked_out() -> bool:
    cutoff = time.time() - FAILURE_WINDOW_SECONDS
    _failures[:] = [moment for moment in _failures if moment > cutoff]
    return len(_failures) >= MAX_FAILURES


def _page(message: str = "", status: int = 200) -> HTMLResponse:
    slot = f'<p class="error" role="alert">{message}</p>' if message else ""
    return HTMLResponse(LOGIN_PAGE.replace("ERROR_SLOT", slot), status_code=status)


async def gate(request: Request, call_next):
    if not password() or request.url.path in OPEN_PATHS or _signed_in(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


async def login_page(_request: Request):
    return _page()


async def login(request: Request):
    if _locked_out():
        return _page("Too many wrong tries. Please wait ten minutes, then try again.", 429)
    form = await request.form()
    if not hmac.compare_digest(str(form.get("password", "")).encode(), password().encode()):
        _failures.append(time.time())
        return _page("That password isn't right. Please try again.", 401)
    response = RedirectResponse("/", status_code=303)
    secure = hosted() or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, _session_value(), httponly=True, secure=secure, samesite="lax", max_age=7 * 24 * 3600)
    return response
