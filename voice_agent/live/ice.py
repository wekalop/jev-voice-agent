"""Relay servers for WebRTC. Hosted apps need them, because most free hosts don't accept direct audio traffic."""

import os

import httpx
from pipecat.transports.smallwebrtc.connection import IceServer

CREDENTIAL_SECONDS = 4 * 3600
TURNIX_URL = "https://turnix.io/api/v1/credentials/ice"
CLOUDFLARE_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key}/credentials/generate-ice-servers"


def configured() -> bool:
    return bool(os.environ.get("TURNIX_API_TOKEN") or (os.environ.get("TURN_KEY_ID") and os.environ.get("TURN_KEY_API_TOKEN")))


async def browser_servers() -> list[dict]:
    """Short-lived relay details for one call, or an empty list when none are configured."""
    if not configured():
        return []
    async with httpx.AsyncClient(timeout=8) as client:
        if os.environ.get("TURNIX_API_TOKEN"):
            response = await client.post(
                TURNIX_URL,
                headers={"Authorization": f"Bearer {os.environ['TURNIX_API_TOKEN']}"},
                data={"ttl": CREDENTIAL_SECONDS},
            )
            response.raise_for_status()
            # Turnix's relay certificate had expired when tested (November 2026), so its encrypted URLs fail.
            # Plain TURN is still safe here: call audio has its own encryption, and these credentials are short-lived.
            servers = without_blocked_ports(response.json()["iceServers"])
            return [{**s, "urls": [u for u in s["urls"] if not u.startswith("turns:")]} for s in servers]
        else:
            response = await client.post(
                CLOUDFLARE_URL.format(key=os.environ["TURN_KEY_ID"]),
                headers={"Authorization": f"Bearer {os.environ['TURN_KEY_API_TOKEN']}"},
                json={"ttl": CREDENTIAL_SECONDS},
            )
    response.raise_for_status()
    return without_blocked_ports(response.json()["iceServers"])


def without_blocked_ports(servers) -> list[dict]:
    """Browsers block port 53, which can stall connecting."""
    entries = servers if isinstance(servers, list) else [servers]
    cleaned = []
    for server in entries:
        urls = server["urls"] if isinstance(server["urls"], list) else [server["urls"]]
        kept = [url for url in urls if ":53?" not in url and not url.endswith(":53")]
        if kept:
            cleaned.append({**server, "urls": kept})
    return cleaned


def _rank(url: str) -> int:
    """Encrypted TCP on port 443 is the route a hosting firewall is least likely to block."""
    tcp = "transport=tcp" in url
    if url.startswith("turns:") and ":443" in url and tcp:
        return 0
    if url.startswith("turns:") and tcp:
        return 1
    if url.startswith("turn:") and tcp:
        return 2
    # The Python library rejects encrypted relays that aren't TCP, so those can't be used here.
    return 3 if url.startswith("turn:") else 4


def server_side(servers: list[dict]) -> list[IceServer]:
    """The Python WebRTC library uses one relay address only, so this picks the most reliable."""
    result = []
    for server in servers:
        if "username" in server:
            result.append(IceServer(urls=min(server["urls"], key=_rank), username=server["username"], credential=server["credential"]))
        else:
            result.append(IceServer(urls=server["urls"]))
    return result
