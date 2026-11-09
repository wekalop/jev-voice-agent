"""Calls the running live server as a browser would, playing a WAV file as the microphone.

python -m tests.live_smoke speech.wav [http://127.0.0.1:7861] [seconds]
Prints whether audio came back. Timing rows are written by the server.
"""

import asyncio
import os
import sys

import httpx
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer


async def main(wav: str, url: str, seconds: float) -> None:
    # With a relay configured, this call goes through it, as a remote browser's would.
    from live import ice

    servers = await ice.browser_servers()
    configuration = [RTCIceServer(urls=s.urls, username=s.username, credential=s.credential) for s in ice.server_side(servers)]
    pc = RTCPeerConnection(RTCConfiguration(iceServers=configuration) if configuration else None)
    player = MediaPlayer(wav)
    pc.addTrack(player.audio)
    received = {"frames": 0}

    @pc.on("track")
    def on_track(track):
        async def drain():
            try:
                while True:
                    await track.recv()
                    received["frames"] += 1
            except Exception:  # The track ends when the call does.
                pass

        asyncio.ensure_future(drain())

    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=30) as client:
        if os.environ.get("ACCESS_PASSWORD"):
            await client.post(f"{url}/login", data={"password": os.environ["ACCESS_PASSWORD"]})
        response = await client.post(
            f"{url}/api/offer", json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        )
        response.raise_for_status()
        answer = response.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        await asyncio.sleep(seconds)
        turns = (await client.get(f"{url}/api/live", headers={"x-call-token": answer["call_token"]})).json()["turns"]
    await pc.close()
    print(f"connection reached: {pc.connectionState}, audio frames received: {received['frames']}")
    print(f"turns logged: {turns}")


if __name__ == "__main__":
    asyncio.run(main(
        sys.argv[1],
        sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:7861",
        float(sys.argv[3]) if len(sys.argv) > 3 else 15,
    ))
