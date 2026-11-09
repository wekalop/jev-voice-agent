"""Settings from the environment. Keys are never hardcoded."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    jev_api_key: str
    jev_base_url: str
    jev_model: str
    jev_mode: str
    groq_api_key: str
    groq_base_url: str
    cerebras_api_key: str
    cerebras_base_url: str
    cerebras_model: str
    cartesia_api_key: str
    cartesia_ws_url: str
    cartesia_version: str
    stt_provider: str
    stt_model: str
    llm_provider: str
    llm_model: str
    llm_fallback_model: str
    classifier_model: str
    tts_provider: str
    tts_model: str
    tts_voice: str
    threshold_needs_human: float
    threshold_answerable: float
    kb_dir: Path
    test_questions: Path
    results_dir: Path
    # The knowledge base is frozen at this moment. See company/README.md.
    now_text: str = "Tuesday, the third of November 2026, nine a.m. Cairo time"
    # Helps speech recognition with names it would otherwise mishear. Kept short, because Whisper can echo it.
    stt_hint: str = (
        "Nabda telecom company. Go Smart, Go Mini, Go Plus, Go Youth, Go Week, Flex Silver, Flex Gold, Flex Platinum, "
        "Boost, Home Fiber, Home Air, eSIM, roaming. Maadi, Smouha, Sidi Gaber."
    )


def load_settings() -> Settings:
    return Settings(
        jev_api_key=_env("JEV_API_KEY"),
        jev_base_url=_env("JEV_BASE_URL", "https://api.typesafe.ai").rstrip("/"),
        jev_model=_env("JEV_MODEL", "jev-1.14.0"),
        jev_mode=_env("JEV_MODE", "two_call"),
        groq_api_key=_env("GROQ_API_KEY"),
        groq_base_url=_env("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"),
        cerebras_api_key=_env("CEREBRAS_API_KEY"),
        cerebras_base_url=_env("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1").rstrip("/"),
        cerebras_model=_env("CEREBRAS_MODEL"),
        cartesia_api_key=_env("CARTESIA_API_KEY"),
        cartesia_ws_url=_env("CARTESIA_WS_URL", "wss://api.cartesia.ai/tts/websocket"),
        cartesia_version=_env("CARTESIA_VERSION", "2026-08-14"),
        stt_provider=_env("STT_PROVIDER", "groq"),
        stt_model=_env("STT_MODEL", "whisper-large-v3-turbo"),
        llm_provider=_env("LLM_PROVIDER", "groq"),
        llm_model=_env("LLM_MODEL", "qwen/qwen3.8-27b"),
        llm_fallback_model=_env("LLM_FALLBACK_MODEL", "openai/gpt-oss-120b"),
        classifier_model=_env("CLASSIFIER_MODEL", "openai/gpt-oss-20b"),
        tts_provider=_env("TTS_PROVIDER", "cartesia"),
        tts_model=_env("TTS_MODEL", "sonic-3.6-2026-08-27"),
        tts_voice=_env("TTS_VOICE", "f786b574-daa5-4673-aa0c-cbe3e8534c02"),
        threshold_needs_human=float(_env("THRESHOLD_NEEDS_HUMAN", "0.7")),
        threshold_answerable=float(_env("THRESHOLD_ANSWERABLE", "0.3")),
        kb_dir=Path(_env("KB_DIR", str(PROJECT / "company"))),
        test_questions=Path(_env("TEST_QUESTIONS", str(PROJECT / "tests" / "test_questions.jsonl"))),
        results_dir=Path(_env("RESULTS_DIR", str(ROOT / "results"))),
    )
