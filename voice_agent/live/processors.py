"""Pipecat processors that connect the shared turn logic to the audio pipeline."""

import re
import time

from loguru import logger
from pipecat.frames.frames import (
    InputAudioRawFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent.timing import TimingLog, TurnTiming
from agent.turn import Agent, Session, stream_turn
from agent.voice_cues import VoiceBaseline, VoiceSample, analyze
from live.monitor import monitor


class TurnTracker:
    """Holds the timing of the current turn, and writes it once first audio arrives."""

    def __init__(self, log: TimingLog):
        self.log = log
        self.timing: TurnTiming | None = None
        self.turns = 0
        self.written = True
        # True once the caller has heard audio from the current turn.
        self.heard = False

    def end_of_speech(self) -> None:
        self.timing = TurnTiming()
        self.written = False

    def write(self, **extra) -> None:
        if self.timing is None or self.written:
            return
        self.written = True
        row = self.timing.row()
        row.update(cold_start=self.turns == 0, source="live", **extra)
        self.turns += 1
        self.log.write(row)
        monitor.add_timings(row)


class VoiceCueProcessor(FrameProcessor):
    """Sits after voice detection. Measures how the caller sounded in each stretch of speech."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.latest: VoiceSample | None = None
        self.latest_ms = 0.0
        self._audio: list[bytes] = []
        self._sample_rate = 16000
        self._recording = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._audio = []
            self._recording = True
        elif isinstance(frame, InputAudioRawFrame) and self._recording:
            self._audio.append(frame.audio)
            self._sample_rate = frame.sample_rate
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._recording = False
            started = time.perf_counter()
            try:
                self.latest = analyze(b"".join(self._audio), self._sample_rate)
            except ValueError:
                self.latest = None
            self.latest_ms = (time.perf_counter() - started) * 1000
        await self.push_frame(frame, direction)


class BrainProcessor(FrameProcessor):
    """Sits between STT and TTS. Runs one turn for each final transcript."""

    def __init__(self, agent: Agent, tracker: TurnTracker, voice: VoiceCueProcessor, **kwargs):
        super().__init__(**kwargs)
        self.agent = agent
        self.tracker = tracker
        self.voice = voice
        self.baseline = VoiceBaseline()
        self.session = Session()
        self._task = None
        # What the caller said in the current turn, and the session as it was before that turn.
        self._current_text: str | None = None
        self._saved: tuple | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            monitor.set_state("speaking")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            monitor.set_state("listening")
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            monitor.set_state("hearing")
            await self._cancel_turn()
            await self.broadcast_interruption()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            monitor.set_state("thinking")
            self.tracker.end_of_speech()
        elif isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if re.search(r"\w", text):
                await self._cancel_turn()
                cues, label = self.baseline.describe_and_learn(self.voice.latest, len(text.split()))
                self._task = self.create_task(self._run_turn(self._merge_with_unheard(text, label), cues))
            return

        await self.push_frame(frame, direction)

    def _merge_with_unheard(self, text: str, voice_label: str) -> str:
        """If the caller paused mid-sentence and heard no reply yet, both parts are one thought."""
        merged = self._current_text is not None and not self.tracker.heard
        if merged:
            history, pending, level = self._saved
            self.session.history = history
            self.session.pending_question = pending
            self.session.pending_level = level
            text = f"{self._current_text} {text}"
        self._current_text = text
        self._saved = (list(self.session.history), self.session.pending_question, self.session.pending_level)
        self.tracker.heard = False
        monitor.begin_turn(text, replaces_last=merged, voice=voice_label)
        return text

    async def _run_turn(self, text: str, voice_cues: str | None) -> None:
        timing = self.tracker.timing or TurnTiming()
        self.tracker.timing = timing
        timing.mark("t_stt_done")
        timing.values["voice_measure_ms"] = round(self.voice.latest_ms, 1)
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            async for delta in stream_turn(self.agent, self.session, text, timing, voice_cues):
                monitor.add_reply_text(delta)
                await self.push_frame(LLMTextFrame(delta))
        except Exception as exc:  # Keep the call alive and tell the caller.
            logger.exception("Turn failed")
            timing.values["error"] = f"{type(exc).__name__}: {exc}"
            apology = "I'm sorry, something went wrong on my side. Please try again."
            monitor.add_reply_text(apology)
            await self.push_frame(LLMTextFrame(apology))
        monitor.finish_turn(
            timing.values.get("reply_path"), bool(timing.values.get("llm_fell_back")), timing.values.get("decision")
        )
        await self.push_frame(LLMFullResponseEndFrame())

    async def _cancel_turn(self) -> None:
        if self._task is not None and not self._task.done():
            await self.cancel_task(self._task)
        self._task = None


class FirstAudioTap(FrameProcessor):
    """Sits after TTS. Marks the first audio of each turn and writes the timing row."""

    def __init__(self, tracker: TurnTracker, **kwargs):
        super().__init__(**kwargs)
        self.tracker = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.tracker.heard = True
            if self.tracker.timing is not None and not self.tracker.written:
                self.tracker.timing.mark("t_tts_first_audio")
                self.tracker.write()
        await self.push_frame(frame, direction)
