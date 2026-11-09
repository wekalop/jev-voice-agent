"""Shared helpers for the replay benchmarks."""

import asyncio
import json
import math
import random
import re
import ssl
import statistics
import time
from urllib.parse import urlparse

from agent.config import Settings
from agent.jev import JevClient
from agent.kb import KnowledgeBase
from agent.providers.llm import make_llm
from agent.turn import Agent

CUSTOMER_ID = re.compile(r"CUST-\d+")


def load_tests(settings: Settings) -> list[dict]:
    with settings.test_questions.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def make_agent(settings: Settings, mode: str, jev_model: str | None = None, provider: str | None = None, model: str | None = None) -> Agent:
    return Agent(
        settings=settings,
        kb=KnowledgeBase(settings.kb_dir),
        jev=JevClient(settings.jev_api_key, settings.jev_base_url, jev_model or settings.jev_model),
        llm=make_llm(settings, provider, model),
        extractor=make_llm(settings, "groq"),
        mode=mode,
    )


def stratified_sample(tests: list[dict], size: int, seed: int = 7) -> list[dict]:
    """Keeps each category's share of the test set. The seed makes runs comparable."""
    rng = random.Random(seed)
    by_category: dict[str, list[dict]] = {}
    for test in tests:
        by_category.setdefault(test["category"], []).append(test)
    chosen = []
    for items in by_category.values():
        count = max(1, round(size * len(items) / len(tests)))
        chosen += rng.sample(items, min(count, len(items)))
    rng.shuffle(chosen)
    return chosen[:size]


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def latency_summary(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    return {"n": len(values), "median": statistics.median(values), "p95": percentile(values, 0.95)}


async def probe_network(url: str, attempts: int = 5) -> float | None:
    """Median TCP and TLS setup time to a host, which gives the network share of latency."""
    parsed = urlparse(url.replace("wss://", "https://").replace("ws://", "http://"))
    secure = parsed.scheme == "https"
    port = parsed.port or (443 if secure else 80)
    times = []
    for _ in range(attempts):
        started = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, port, ssl=ssl.create_default_context() if secure else None), 5
            )
        except (OSError, asyncio.TimeoutError):
            continue
        times.append((time.perf_counter() - started) * 1000)
        writer.close()
    return round(statistics.median(times), 1) if times else None
