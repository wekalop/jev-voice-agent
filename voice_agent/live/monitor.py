"""What the call page shows: the call state, the conversation, and each turn's timings."""

import time

from benchmark.common import latency_summary

STAGES = {"stt": "stt_done_ms", "jev": "jev_done_ms", "llm": "llm_first_token_ms", "total": "t_total"}


class CallMonitor:
    def __init__(self):
        self.active = False
        self.state = "idle"
        self.started_at = None
        self.greeting = None
        self.turns: list[dict] = []

    def start_call(self, greeting: str) -> None:
        self.active = True
        self.state = "connecting"
        self.started_at = time.time()
        self.greeting = greeting
        self.turns = []

    def end_call(self) -> None:
        self.active = False
        self.state = "idle"

    def set_state(self, state: str) -> None:
        if self.active:
            self.state = state

    def begin_turn(self, caller: str, replaces_last: bool, voice: str) -> None:
        if replaces_last and self.turns:
            self.turns.pop()
        self.turns.append({"caller": caller, "agent": "", "timings": None, "path": None, "fallback": False, "voice": voice})

    def add_reply_text(self, text: str) -> None:
        if self.turns:
            self.turns[-1]["agent"] += text

    def add_timings(self, row: dict) -> None:
        if self.turns:
            self.turns[-1]["timings"] = {name: row.get(key) for name, key in STAGES.items()}
            self.turns[-1]["cold"] = row.get("cold_start", False)
            self.turns[-1]["waited"] = bool(row.get("rate_limit_wait_ms"))

    def finish_turn(self, path: str | None, fell_back: bool, decision: dict | None) -> None:
        if self.turns:
            self.turns[-1]["path"] = path
            self.turns[-1]["fallback"] = fell_back
            if decision:
                self.turns[-1]["mood"] = decision.get("sentiment")
                self.turns[-1]["urgency"] = decision.get("urgency")

    def snapshot(self) -> dict:
        # The first turn is a cold start, and rate-limit waits say nothing about the model's speed.
        timed = [t["timings"] for t in self.turns if t["timings"] and not t.get("cold") and not t.get("waited")]
        return {
            "active": self.active,
            "state": self.state,
            "started_at": self.started_at,
            "greeting": self.greeting,
            "turns": self.turns,
            "summary": {name: latency_summary([t[name] for t in timed]) for name in STAGES},
        }


monitor = CallMonitor()
