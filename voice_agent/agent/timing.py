"""Per-turn timing, written as one JSON line per turn."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TurnTiming:
    """Times come from one clock. `start` is the end of speech, or the text arriving in text replay."""

    start_perf: float = field(default_factory=time.perf_counter)
    start_epoch: float = field(default_factory=time.time)
    marks: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.perf_counter())

    def ms(self, name: str) -> float | None:
        if name not in self.marks:
            return None
        return round((self.marks[name] - self.start_perf) * 1000, 1)

    def _epoch(self, name: str) -> float | None:
        if name not in self.marks:
            return None
        return round(self.start_epoch + (self.marks[name] - self.start_perf), 3)

    def row(self) -> dict:
        wait = self.values.get("rate_limit_wait_ms", 0.0) + self.values.get("jev_backoff_ms", 0.0)
        first_audio = self.ms("t_tts_first_audio")
        return {
            "t_end_of_speech": round(self.start_epoch, 3),
            "t_stt_done": self._epoch("t_stt_done"),
            "t_jev_done": self._epoch("t_jev_done"),
            "t_llm_first_token": self._epoch("t_llm_first_token"),
            "t_tts_first_audio": self._epoch("t_tts_first_audio"),
            "stt_done_ms": self.ms("t_stt_done"),
            "jev_done_ms": self.ms("t_jev_done"),
            "llm_first_token_ms": self.ms("t_llm_first_token"),
            "first_sentence_ms": self.ms("t_first_sentence"),
            "t_total": first_audio,
            "t_total_excluding_waits": None if first_audio is None else round(first_audio - wait, 1),
            **self.values,
        }


class TimingLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def write(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
