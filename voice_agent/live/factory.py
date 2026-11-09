"""Swappable Pipecat services for live mode. To add a provider, add a branch here."""

from agent.config import Settings


def make_live_stt(settings: Settings):
    if settings.stt_provider == "groq":
        from pipecat.services.groq.stt import GroqSTTService
        from pipecat.transcriptions.language import Language

        return GroqSTTService(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            settings=GroqSTTService.Settings(model=settings.stt_model, language=Language.EN, prompt=settings.stt_hint),
        )
    raise ValueError(f"Unknown STT provider: {settings.stt_provider}")


def make_live_tts(settings: Settings):
    if settings.tts_provider == "cartesia":
        from pipecat.services.cartesia.tts import CartesiaTTSService

        return CartesiaTTSService(
            api_key=settings.cartesia_api_key,
            url=settings.cartesia_ws_url,
            settings=CartesiaTTSService.Settings(model=settings.tts_model, voice=settings.tts_voice),
        )
    raise ValueError(f"Unknown TTS provider: {settings.tts_provider}")
