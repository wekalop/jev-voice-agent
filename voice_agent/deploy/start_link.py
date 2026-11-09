"""Connects the stable workers.dev address to this PC: python -m deploy.start_link

Start the agent first with python -m live.server. This starts a tunnel, points the Worker at it, and
keeps running. Press Ctrl+C to stop sharing, and the address then shows an offline page.
"""

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx

WORKER_FOLDER = Path(__file__).parent / "worker"
DEFAULT_CLOUDFLARED = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
LOCAL_AGENT = "http://localhost:7860"
TUNNEL_ADDRESS = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
WRANGLER = ["npx", "--yes", "wrangler@4"]


def wrangler(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    command = (shutil.which("npx") or "npx", *WRANGLER[1:], *args)
    return subprocess.run(command, cwd=WORKER_FOLDER, capture_output=capture, text=True, encoding="utf-8", errors="replace")


def start_tunnel() -> tuple[subprocess.Popen, str]:
    executable = shutil.which("cloudflared") or DEFAULT_CLOUDFLARED
    process = subprocess.Popen(
        [executable, "tunnel", "--no-autoupdate", "--url", LOCAL_AGENT],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    found: list[str] = []

    def read_output() -> None:
        for line in process.stdout:
            match = TUNNEL_ADDRESS.search(line)
            if match and not found:
                found.append(match.group())

    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.time() + 60
    while not found and time.time() < deadline:
        time.sleep(0.5)
    if not found:
        process.terminate()
        raise SystemExit("The tunnel didn't start within a minute. Check your connection and try again.")
    return process, found[0]


def wait_until_reachable(url: str) -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/login", timeout=8).status_code in (200, 404):
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit("The tunnel started, but the agent can't be reached through it.")


def main() -> None:
    try:
        httpx.get(f"{LOCAL_AGENT}/login", timeout=4)
    except httpx.HTTPError:
        raise SystemExit("The agent isn't running. Start it first with: python -m live.server")

    tunnel, address = start_tunnel()
    try:
        wait_until_reachable(address)
        print("Pointing the Worker at this PC...", flush=True)
        result = wrangler("deploy", "--var", f"ORIGIN_URL:{address}", capture=True)
        if result.returncode != 0:
            print(result.stdout[-1500:], result.stderr[-1500:])
            raise SystemExit("Publishing failed. If it says you aren't signed in, run: npx wrangler@4 login")
        link = re.search(r"https://[\w.-]+\.workers\.dev", result.stdout)
        print(f"\nLive at: {link.group() if link else 'your workers.dev address'}")
        print("Keep this window open during the demo. Press Ctrl+C to stop sharing.\n", flush=True)
        tunnel.wait()
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
        print("Stopped sharing.")


if __name__ == "__main__":
    main()
