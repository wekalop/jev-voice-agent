"""Swappable STT for the replay benchmark. Live mode uses Pipecat's service for the same provider."""

import httpx

from agent.config import Settings


class GroqWhisperSTT:
    def __init__(self, settings: Settings):
        self.model = settings.stt_model
        self.hint = settings.stt_hint
        self._client = httpx.AsyncClient(
            base_url=settings.groq_base_url,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            timeout=30.0,
        )

    async def transcribe(self, wav_bytes: bytes) -> str:
        response = await self._client.post(
            "/audio/transcriptions",
            files={"file": ("speech.wav", wav_bytes, "audio/wav")},
            data={"model": self.model, "language": "en", "response_format": "json", "temperature": "0", "prompt": self.hint},
        )
        response.raise_for_status()
        return response.json()["text"].strip()

    async def close(self) -> None:
        await self._client.aclose()


def make_stt(settings: Settings):
    if settings.stt_provider == "groq":
        return GroqWhisperSTT(settings)
    raise ValueError(f"Unknown STT provider: {settings.stt_provider}")
