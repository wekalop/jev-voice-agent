"""Swappable TTS for the replay benchmark. Live mode uses Pipecat's service for the same provider."""

import base64
import json
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import websockets

from agent.config import Settings

SAMPLE_RATE = 24000


class CartesiaTTS:
    """Streams text to Cartesia's WebSocket API and yields raw 16-bit PCM."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._ws = None

    async def connect(self) -> None:
        query = urlencode({"cartesia_version": self.settings.cartesia_version})
        self._ws = await websockets.connect(
            f"{self.settings.cartesia_ws_url}?{query}",
            additional_headers={"X-API-Key": self.settings.cartesia_api_key},
        )

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if self._ws is None:
            await self.connect()
        context_id = uuid.uuid4().hex
        try:
            await self._ws.send(self._message(text, context_id))
        except websockets.ConnectionClosed:
            await self.connect()
            await self._ws.send(self._message(text, context_id))
        while True:
            message = json.loads(await self._ws.recv())
            if message.get("context_id") not in (None, context_id):
                continue
            kind = message.get("type")
            if kind == "chunk":
                yield base64.b64decode(message["data"])
            elif kind == "error":
                raise RuntimeError(f"Cartesia error: {message.get('title')}: {message.get('message')}")
            if message.get("done"):
                return

    def _message(self, text: str, context_id: str) -> str:
        return json.dumps({
            "model_id": self.settings.tts_model,
            "transcript": text,
            "voice": self.settings.tts_voice,
            "language": "en",
            "context_id": context_id,
            "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": SAMPLE_RATE},
            "continue": False,
        })

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


def make_tts(settings: Settings):
    if settings.tts_provider == "cartesia":
        return CartesiaTTS(settings)
    raise ValueError(f"Unknown TTS provider: {settings.tts_provider}")
