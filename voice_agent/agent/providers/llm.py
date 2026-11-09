"""Swappable LLM providers. All use the OpenAI-compatible streaming API."""

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, RateLimitError

from agent.config import Settings

MAX_ATTEMPTS = 8


@dataclass
class LLMStats:
    model_used: str = ""
    rate_limit_wait_ms: float = 0.0
    fell_back: bool = False


def model_options(model: str) -> tuple[dict, int]:
    """Request options that keep thinking off the critical path, plus tokens to allow for it.

    Qwen can turn reasoning off. GPT-OSS cannot, so it gets the lowest setting and extra tokens,
    because its hidden reasoning counts against the output limit.
    """
    if model.startswith("qwen/"):
        return {"reasoning_effort": "none"}, 0
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "include_reasoning": False}, 300
    return {}, 0


class OpenAICompatibleLLM:
    def __init__(self, name: str, api_key: str, base_url: str, model: str, fallback_model: str = ""):
        self.name = name
        self.model = model
        self.fallback_model = fallback_model
        # Retries are handled here so that waits can be measured and left out of model timing.
        # The long keep-alive stops pauses between turns from forcing a new TLS connection.
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=60.0,
            http_client=DefaultAsyncHttpxClient(limits=httpx.Limits(keepalive_expiry=120)),
        )

    async def stream(self, messages: list[dict], stats: LLMStats, max_tokens: int = 140) -> AsyncIterator[str]:
        model = self.model
        for attempt in range(MAX_ATTEMPTS):
            options, extra_tokens = model_options(model)
            try:
                stream = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                    temperature=0.4,
                    max_completion_tokens=max_tokens + extra_tokens,
                    extra_body=options,
                )
                stats.model_used = model
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except RateLimitError as exc:
                wait = _retry_after(exc)
                # On a call, a different model now beats silence. Benchmarks turn the fallback off.
                if wait > 1 and self.fallback_model and model != self.fallback_model:
                    model = self.fallback_model
                    stats.fell_back = True
                    continue
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                stats.rate_limit_wait_ms += wait * 1000
                await asyncio.sleep(wait)

    async def complete_json(self, messages: list[dict], model: str, stats: LLMStats, max_tokens: int = 200) -> str:
        options, extra_tokens = model_options(model)
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    max_completion_tokens=max_tokens + extra_tokens,
                    response_format={"type": "json_object"},
                    extra_body=options,
                )
                stats.model_used = response.model or model
                return response.choices[0].message.content or "{}"
            except RateLimitError as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                wait = _retry_after(exc)
                stats.rate_limit_wait_ms += wait * 1000
                await asyncio.sleep(wait)
        return "{}"


def _retry_after(exc: RateLimitError) -> float:
    headers = getattr(exc.response, "headers", {}) or {}
    value = headers.get("retry-after")
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(exc))
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2))
    return 5.0


def make_llm(settings: Settings, provider: str | None = None, model: str | None = None) -> OpenAICompatibleLLM:
    provider = provider or settings.llm_provider
    if provider == "groq":
        return OpenAICompatibleLLM(
            "groq", settings.groq_api_key, settings.groq_base_url, model or settings.llm_model, settings.llm_fallback_model
        )
    if provider == "cerebras":
        chosen = model or settings.cerebras_model
        if not chosen:
            raise ValueError("Set CEREBRAS_MODEL. Run `python -m agent.check` to list what your key can use.")
        return OpenAICompatibleLLM("cerebras", settings.cerebras_api_key, settings.cerebras_base_url, chosen)
    raise ValueError(f"Unknown LLM provider: {provider}")
