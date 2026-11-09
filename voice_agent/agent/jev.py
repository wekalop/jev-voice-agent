"""Thin async client for Jev, written against POST /v1/systemone in TypeSafe's API reference."""

import asyncio
import random
import time
from dataclasses import dataclass, field

import httpx


class JevError(RuntimeError):
    pass


@dataclass
class JevResult:
    answers: dict
    latency_ms: float
    model: str
    usage: dict = field(default_factory=dict)
    retries: int = 0
    backoff_ms: float = 0.0


class JevClient:
    def __init__(self, api_key: str, base_url: str, model: str, max_retries: int = 3):
        self.model = model
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(15.0, connect=5.0),
            # Stops pauses between turns from forcing a new TLS connection on the critical path.
            limits=httpx.Limits(keepalive_expiry=120),
        )

    async def decide(self, state, questions: dict) -> JevResult:
        body = {"model": self.model, "state": state, "questions": questions}
        backoff_ms = 0.0
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            response = await self._client.post("/v1/systemone", json=body)
            latency_ms = (time.perf_counter() - started) * 1000
            # TypeSafe's docs say to back off on these two and not to retry anything else.
            if response.status_code in (429, 529) and attempt < self.max_retries:
                wait = (2**attempt) * 0.5 + random.random() * 0.25
                backoff_ms += wait * 1000
                await asyncio.sleep(wait)
                continue
            if response.status_code != 200:
                raise JevError(f"Jev returned {response.status_code}: {response.text[:500]}")
            data = response.json()
            # latency_ms covers the successful attempt only. backoff_ms is reported separately.
            return JevResult(
                answers=data["answers"],
                latency_ms=latency_ms,
                model=data.get("model", self.model),
                usage=data.get("usage", {}),
                retries=attempt,
                backoff_ms=backoff_ms,
            )
        raise JevError("Jev retries exhausted")

    async def warm_up(self) -> None:
        """Opens the connection so the first real turn does not pay for TLS setup."""
        await self.decide("warm up", {"ok": {"type": "noul", "instructions": "Is this text in English?"}})

    async def close(self) -> None:
        await self._client.aclose()
