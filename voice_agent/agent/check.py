"""Live check of keys and model IDs: python -m agent.check

Prints only status and non-secret details, never a key.
"""

import asyncio
import time

import httpx

from agent import decisions
from agent.config import load_settings
from agent.jev import JevClient, JevError
from agent.providers.tts import CartesiaTTS


async def check_jev(s) -> None:
    if not s.jev_api_key:
        print("Jev: JEV_API_KEY is not set")
        return
    jev = JevClient(s.jev_api_key, s.jev_base_url, s.jev_model, max_retries=0)
    try:
        for run in ("cold", "warm"):
            result = await jev.decide("I want to check my bill.", decisions.routing_questions(False))
            print(
                f"Jev {run}: {result.latency_ms:.0f} ms, model reported {result.model}, "
                f"intent {result.answers['intent']['choice']}"
            )
    except (JevError, httpx.HTTPError) as exc:
        print(f"Jev: FAILED {exc}")
    await jev.close()


async def check_groq(s) -> None:
    if not s.groq_api_key:
        print("Groq: GROQ_API_KEY is not set")
        return
    headers = {"Authorization": f"Bearer {s.groq_api_key}"}
    async with httpx.AsyncClient(base_url=s.groq_base_url, headers=headers, timeout=20) as client:
        response = await client.get("/models")
        if response.status_code != 200:
            print(f"Groq: FAILED {response.status_code}")
            return
        available = {m["id"] for m in response.json()["data"]}
        for model in (s.stt_model, s.llm_model, s.llm_fallback_model, s.classifier_model):
            print(f"Groq {model}: {'available' if model in available else 'NOT AVAILABLE'}")
        for model in sorted({s.llm_model, s.classifier_model}):
            response = await client.post(
                "/chat/completions",
                json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
            )
            limits = {k: v for k, v in response.headers.items() if k.startswith("x-ratelimit-limit")}
            print(f"Groq limits for {model}: {limits or response.status_code}")


async def check_cerebras(s) -> None:
    if not s.cerebras_api_key:
        print("Cerebras: no key, skipping the optional comparison")
        return
    headers = {"Authorization": f"Bearer {s.cerebras_api_key}"}
    async with httpx.AsyncClient(base_url=s.cerebras_base_url, headers=headers, timeout=20) as client:
        response = await client.get("/models")
        if response.status_code == 200:
            print("Cerebras models:", sorted(m["id"] for m in response.json()["data"]))
        else:
            print(f"Cerebras: FAILED {response.status_code}")


async def check_cartesia(s) -> None:
    if not s.cartesia_api_key:
        print("Cartesia: CARTESIA_API_KEY is not set")
        return
    tts = CartesiaTTS(s)
    try:
        await tts.connect()
        started = time.perf_counter()
        async for _ in tts.synthesize("Hello."):
            print(f"Cartesia: first audio in {(time.perf_counter() - started) * 1000:.0f} ms, model {s.tts_model}")
            break
    except Exception as exc:  # Any failure here should be reported, not raised.
        print(f"Cartesia: FAILED {exc}")
    await tts.close()


async def main() -> None:
    settings = load_settings()
    await check_jev(settings)
    await check_groq(settings)
    await check_cerebras(settings)
    await check_cartesia(settings)


if __name__ == "__main__":
    asyncio.run(main())
