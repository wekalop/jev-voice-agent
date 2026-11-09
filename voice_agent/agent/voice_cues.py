"""Measures how a caller sounds, using plain signal maths and no AI model.

Jev cannot take audio, so these measurements reach it as a sentence of text. Each turn is compared
with how the same caller sounded earlier in the call, because voices differ too much for fixed
values to mean anything.
"""

import statistics
from dataclasses import dataclass, field

import numpy as np

FRAME_SECONDS = 0.03
HOP_SECONDS = 0.02
MIN_PITCH_HZ = 75
MAX_PITCH_HZ = 400
# How strong the repeating pattern must be for a frame to count as voiced speech.
VOICED_STRENGTH = 0.5
MIN_VOICED_SECONDS = 0.4
MIN_WORDS = 3
MAX_SECONDS = 15

LOUDER_DB = 3.0
PITCH_SEMITONES = 2.0
RATE_CHANGE = 0.2


@dataclass
class VoiceSample:
    loudness_db: float
    pitch_hz: float
    speech_seconds: float


def analyze(pcm: bytes, sample_rate: int) -> VoiceSample | None:
    """Reads 16-bit mono audio. Returns None if there is too little voiced speech to judge."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    samples = samples[-MAX_SECONDS * sample_rate :]
    frame_length = int(FRAME_SECONDS * sample_rate)
    hop = int(HOP_SECONDS * sample_rate)
    if len(samples) < frame_length:
        return None

    frames = np.lib.stride_tricks.sliding_window_view(samples, frame_length)[::hop]
    levels = np.sqrt(np.mean(frames**2, axis=1))
    # Ignores silence so that pauses do not count as a quiet voice.
    loud_enough = levels > max(0.003, float(np.percentile(levels, 90)) * 0.1)

    min_lag = int(sample_rate / MAX_PITCH_HZ)
    max_lag = min(int(sample_rate / MIN_PITCH_HZ), frame_length - 1)
    voiced_levels, pitches, voiced_positions = [], [], []
    for position in np.flatnonzero(loud_enough):
        frame = frames[position] - np.mean(frames[position])
        spectrum = np.fft.rfft(frame, 2 * frame_length)
        correlation = np.fft.irfft(spectrum * np.conj(spectrum))[: max_lag + 1]
        if correlation[0] <= 0:
            continue
        lag = min_lag + int(np.argmax(correlation[min_lag:]))
        if correlation[lag] / correlation[0] >= VOICED_STRENGTH:
            pitches.append(sample_rate / lag)
            voiced_levels.append(levels[position])
            voiced_positions.append(position)

    if len(pitches) * HOP_SECONDS < MIN_VOICED_SECONDS:
        return None
    return VoiceSample(
        loudness_db=20 * float(np.log10(np.mean(voiced_levels))),
        pitch_hz=float(np.median(pitches)),
        speech_seconds=(voiced_positions[-1] - voiced_positions[0] + 1) * HOP_SECONDS,
    )


@dataclass
class VoiceBaseline:
    """How this caller has sounded so far in the call."""

    loudness: list = field(default_factory=list)
    pitch: list = field(default_factory=list)
    rate: list = field(default_factory=list)

    def describe_and_learn(self, sample: VoiceSample | None, words: int) -> tuple[str | None, str]:
        """Returns a sentence for Jev (or None), and a short label for the call page."""
        if sample is None or words < MIN_WORDS:
            return None, "too short to judge"
        rate = words / sample.speech_seconds
        description = None
        label = "learning your voice"
        if self.loudness:
            loudness_change = sample.loudness_db - statistics.median(self.loudness)
            semitones = 12 * float(np.log2(sample.pitch_hz / statistics.median(self.pitch)))
            rate_change = rate / statistics.median(self.rate) - 1
            cues = []
            if loudness_change >= LOUDER_DB:
                cues.append("louder")
            elif loudness_change <= -LOUDER_DB:
                cues.append("quieter")
            if semitones >= PITCH_SEMITONES:
                cues.append("higher pitched")
            elif semitones <= -PITCH_SEMITONES:
                cues.append("lower pitched")
            if rate_change >= RATE_CHANGE:
                cues.append("faster")
            elif rate_change <= -RATE_CHANGE:
                cues.append("slower")
            label = ", ".join(cues) if cues else "steady"
            summary = " and ".join(cues) if cues else "about the same"
            description = (
                f"Compared with earlier in this call, the caller's voice sounds {summary}. "
                "This comes from audio measurements, and it can have other causes, such as background noise."
            )
        self.loudness.append(sample.loudness_db)
        self.pitch.append(sample.pitch_hz)
        self.rate.append(rate)
        return description, label
