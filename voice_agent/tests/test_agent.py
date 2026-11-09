"""Tests of the agent's logic against local mock services. No keys or network needed."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from tests.mock_services import app as mock_app


@pytest.fixture(scope="session")
def mock_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def env(mock_url, tmp_path, monkeypatch):
    values = {
        "JEV_API_KEY": "test", "GROQ_API_KEY": "test", "CARTESIA_API_KEY": "test",
        "JEV_BASE_URL": mock_url,
        "GROQ_BASE_URL": f"{mock_url}/openai/v1",
        "CARTESIA_WS_URL": mock_url.replace("http://", "ws://") + "/tts/websocket",
        "RESULTS_DIR": str(tmp_path),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    mock_app.state.last_prompts.clear()
    return values


def make(mode="two_call"):
    from agent.config import load_settings
    from benchmark.common import make_agent

    return make_agent(load_settings(), mode)


def say(agent, session, text):
    from agent.timing import TurnTiming
    from agent.turn import stream_turn

    async def collect():
        timing = TurnTiming()
        reply = "".join([d async for d in stream_turn(agent, session, text, timing)])
        return reply, timing

    return asyncio.run(collect())


def fresh_jev(agent):
    """Each asyncio.run has its own event loop, so the HTTP clients are rebuilt per call."""
    from agent.jev import JevClient
    from agent.providers.llm import make_llm

    s = agent.settings
    agent.jev = JevClient(s.jev_api_key, s.jev_base_url, s.jev_model)
    agent.llm = make_llm(s)
    agent.extractor = make_llm(s, "groq")


def turn(agent, session, text):
    fresh_jev(agent)
    return say(agent, session, text)


@pytest.mark.parametrize("mode", ["one_call", "two_call", "two_call_parallel"])
def test_general_question_reaches_llm_with_routed_file(env, mode):
    from agent.turn import Session

    agent = make(mode)
    reply, timing = turn(agent, Session(), "How much is the Go Smart plan?")
    assert "one hundred thirty pounds" in reply
    assert timing.values["reply_path"] == "llm"
    system = mock_app.state.last_prompts[-1][0]["content"]
    assert "Knowledge: plans.md" in system
    assert "Knowledge: roaming.md" not in system
    assert "What the assistant must never do" in system


@pytest.mark.parametrize("mode", ["one_call", "two_call", "two_call_parallel"])
def test_unanswerable_question_gets_decline(env, mode):
    from agent import prompts
    from agent.turn import Session

    agent = make(mode)
    reply, timing = turn(agent, Session(), "What's the weather in Hurghada?")
    # The decline is written by the LLM, with no knowledge in its prompt, so it has nothing to invent from.
    assert reply and prompts.DECLINE not in reply
    assert timing.values["reply_path"].startswith("decline")
    system = mock_app.state.last_prompts[-1][0]["content"]
    assert "does not cover this" in system
    assert "# Knowledge:" not in system


def test_account_question_asks_for_identity_and_sends_no_record(env):
    from agent.turn import Session

    agent = make()
    session = Session()
    reply, timing = turn(agent, session, "What's my balance?")
    assert "last four digits" in reply
    assert timing.values["reply_path"] == "fixed_identity"
    # Only the extractor was called, and no customer data was in its prompt.
    for messages in mock_app.state.last_prompts:
        assert "CUST-" not in json.dumps(messages)


def test_verification_answers_the_original_question(env):
    from agent.turn import Session

    agent = make()
    session = Session()
    turn(agent, session, "What's my balance?")
    assert session.pending_question == "What's my balance?"
    reply, timing = turn(agent, session, "my number is oh one nine nine, Mona Adel, four four seven one")
    assert session.identity.level == 1
    assert timing.values["reply_path"] == "llm"
    system = mock_app.state.last_prompts[-1][0]["content"]
    assert "CUST-1001" in system
    # The identity details themselves never reach the reply model.
    assert "national_id_last4" not in system
    assert "1990-05-12" not in system
    assert "four four seven one" not in json.dumps(mock_app.state.last_prompts[-1])


def test_wrong_details_share_nothing_and_lock_after_three(env):
    from agent import prompts
    from agent.turn import Session

    agent = make()
    session = Session()
    turn(agent, session, "What's my balance?")
    for _ in range(2):
        reply, _timing = turn(agent, session, "wrong details")
        assert reply.startswith(prompts.NOT_MATCHED)
        assert session.identity.level == 0
    reply, _timing = turn(agent, session, "wrong details")
    assert reply == prompts.LOCKED
    for messages in mock_app.state.last_prompts:
        assert "CUST-" not in json.dumps(messages)


def test_mixing_two_customers_details_fails(env):
    from agent.turn import Session

    agent = make()
    session = Session()
    turn(agent, session, "What's my balance?")
    turn(agent, session, "someone else")
    assert session.identity.level == 0
    assert session.identity.customer_id is None


def test_account_change_needs_birth_date(env):
    from agent import prompts
    from agent.turn import Session

    agent = make()
    session = Session()
    turn(agent, session, "I want to change my plan")
    reply, _ = turn(agent, session, "my number is oh one nine nine, Mona Adel, four four seven one")
    assert session.identity.level == 1
    assert reply == prompts.ASK_FOR_BIRTH_DATE
    reply, timing = turn(agent, session, "I was born on the twelfth of May 1990")
    assert session.identity.level == 2
    assert timing.values["reply_path"] == "llm"


def test_human_request_hands_off_without_identity_check(env):
    from agent.turn import Session

    agent = make()
    reply, timing = turn(agent, Session(), "Get me a manager")
    assert timing.values["hand_off"] is True
    assert timing.values["reply_path"] == "fixed_human"
    assert "eight a.m. to midnight" in reply


def test_caller_moving_on_drops_the_check(env):
    from agent.turn import Session

    agent = make()
    session = Session()
    turn(agent, session, "What's my balance?")
    reply, timing = turn(agent, session, "Actually how much is Go Smart?")
    assert session.pending_question is None
    assert timing.values["reply_path"] == "llm"
    assert "one hundred thirty pounds" in reply


def test_partly_heard_phone_is_not_a_failed_check(env):
    from agent import prompts
    from agent.turn import Session

    agent = make()
    session = Session()
    turn(agent, session, "What's my balance?")
    reply, _ = turn(agent, session, "partial number")
    assert session.identity.failures == 0
    assert session.identity.phone_partial == "0199"
    assert reply.startswith(prompts.PARTIAL_PHONE)
    turn(agent, session, "rest of the number")
    assert session.identity.phone == "01995550101"
    assert session.identity.failures == 0


def test_thanks_during_a_check_does_not_ask_for_id_again(env):
    from agent.turn import Session

    agent = make()
    session = Session()
    turn(agent, session, "What's my balance?")
    reply, timing = turn(agent, session, "Okay thanks")
    assert session.pending_question is None
    assert timing.values["reply_path"] == "llm"
    assert "last four digits" not in reply


def test_followup_keeps_the_last_file(env):
    from agent.turn import Session

    agent = make()
    session = Session()
    turn(agent, session, "How much is the Go Smart plan?")
    _, timing = turn(agent, session, "Okay thanks")
    assert "plans.md" in timing.values["files"]


def test_reply_plans_file_is_shorter_but_keeps_the_facts(env):
    agent = make()
    slim = agent.kb.reply_files["plans.md"]
    assert len(slim) < len(agent.kb.files["plans.md"])
    assert "Go Smart costs one hundred thirty pounds" in slim
    assert "Rollover is lost" in slim


def test_jev_request_matches_documented_shape():
    from agent import decisions

    questions = decisions.routing_questions(include_answerable=True)
    assert questions["intent"]["type"] == "choice" and isinstance(questions["intent"]["criteria"], dict)
    assert questions["urgency"]["type"] == "score" and isinstance(questions["urgency"]["criteria"], list)
    assert set(questions["needs_human"]["criteria"]) == {"true", "false"}
    assert set(decisions.INTENT_CRITERIA) == {
        "billing", "plan_info", "plan_change", "coverage_outage", "roaming", "device_sim",
        "technical_issue", "complaint", "account_lookup", "human_request", "out_of_scope", "chitchat",
    }


def test_first_sentence_does_not_stop_at_times():
    from benchmark.e2e_bench import first_sentence

    assert first_sentence("It opens at ten a.m. on Friday") is None
    assert first_sentence("It opens at ten a.m. on Friday. Come by.") == "It opens at ten a.m. on Friday."
    assert first_sentence("Not yet") is None


def test_benchmarks_and_report_run_end_to_end(env, tmp_path):
    root = Path(__file__).parent.parent
    run_env = {**os.environ, **env, "PYTHONIOENCODING": "utf-8"}

    def run(*args):
        result = subprocess.run(
            [sys.executable, "-m", *args], cwd=root, env=run_env, capture_output=True, text=True, timeout=120
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    run("benchmark.decisions_bench", "--limit", "6")
    decisions = [json.loads(l) for l in (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(decisions) == 18
    assert sum(1 for d in decisions if d["cold_start"]) == 3

    run("benchmark.e2e_bench", "--mode", "two_call", "--size", "10", "--speech", "--judge")
    rows = [json.loads(l) for l in (tmp_path / "e2e_two_call.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not [r for r in rows if "error" in r], rows
    assert all(r["t_total"] is not None for r in rows)
    assert all(r["stt_done_ms"] is not None for r in rows)

    run("benchmark.report")
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "Accuracy per field" in report and "End-to-end turns" in report


def test_long_replies_stop_at_the_sentence_limit():
    from agent.turn import _within_limit

    assert _within_limit("One. Two.", " Three") == (" Three", False)
    piece, finished = _within_limit("One. Two. Three", ". Four. ")
    assert piece == "." and finished
    assert _within_limit("It opens at ten a.m. ", "today")[1] is False


def test_asking_about_someone_else_is_refused_without_a_check(env):
    from agent.turn import Session

    agent = make()
    session = Session()
    _reply, timing = turn(agent, session, "I'm calling for my wife, what's her balance?")
    assert timing.values["reply_path"] == "llm"
    assert session.pending_question is None
    system = mock_app.state.last_prompts[-1][0]["content"]
    assert "someone else's account" in system
    assert "CUST-" not in system


def test_second_topic_loads_both_main_files():
    from agent.kb import select_files

    files = select_files({"plan_info": 0.9}, "plan_info", "coverage_outage")[:2]
    assert files == ["plans.md", "coverage_and_outages.md"]


def _tone(hz, level=0.2, seconds=2.0, rate=16000):
    import numpy as np

    t = np.arange(int(seconds * rate)) / rate
    return (level * 32767 * np.sin(2 * np.pi * hz * t)).astype(np.int16).tobytes()


def test_voice_analysis_reads_pitch_and_loudness():
    from agent.voice_cues import analyze

    low, high = analyze(_tone(120), 16000), analyze(_tone(240), 16000)
    assert abs(low.pitch_hz - 120) < 5 and abs(high.pitch_hz - 240) < 10
    quiet, loud = analyze(_tone(150, level=0.05), 16000), analyze(_tone(150, level=0.4), 16000)
    assert loud.loudness_db - quiet.loudness_db > 15


def test_voice_analysis_ignores_silence_and_noise():
    import numpy as np

    from agent.voice_cues import analyze

    assert analyze(b"\0\0" * 32000, 16000) is None
    assert analyze(b"\0\0" * 10, 16000) is None
    noise = (np.random.default_rng(1).normal(0, 3000, 32000)).astype(np.int16).tobytes()
    assert analyze(noise, 16000) is None


def test_voice_baseline_compares_with_earlier_in_the_call():
    from agent.voice_cues import VoiceBaseline, VoiceSample

    baseline = VoiceBaseline()
    calm = VoiceSample(loudness_db=-30, pitch_hz=120, speech_seconds=4)
    # The first turn has nothing to compare with, and short turns teach nothing.
    assert baseline.describe_and_learn(calm, 2) == (None, "too short to judge")
    assert baseline.describe_and_learn(calm, 10) == (None, "learning your voice")
    assert baseline.describe_and_learn(calm, 10)[1] == "steady"
    text, label = baseline.describe_and_learn(VoiceSample(-20, 170, 4), 16)
    assert label == "louder, higher pitched, faster"
    assert "other causes" in text


def test_voice_cues_reach_jev_only_when_present(env, monkeypatch):
    from agent.turn import Session

    agent = make()
    seen = []
    original = agent.jev.__class__.decide

    async def spy(self, state, questions):
        seen.append((state, questions))
        return await original(self, state, questions)

    monkeypatch.setattr(agent.jev.__class__, "decide", spy)
    turn(agent, Session(), "How much is the Go Smart plan?")
    assert "voice_cues" not in seen[0][0]
    assert "voice_cues" not in seen[0][1]["sentiment"]["instructions"]

    seen.clear()
    fresh_jev(agent)

    async def collect():
        from agent.timing import TurnTiming
        from agent.turn import stream_turn

        return [d async for d in stream_turn(agent, Session(), "How much is it?", TurnTiming(), "The caller sounds louder.")]

    asyncio.run(collect())
    assert seen[0][0]["voice_cues"] == "The caller sounds louder."
    assert "voice_cues" in seen[0][1]["sentiment"]["instructions"]
    assert "voice_cues" in seen[0][1]["urgency"]["instructions"]
    assert "The caller sounds louder." in mock_app.state.last_prompts[-1][0]["content"]


def test_percentile():
    from benchmark.common import percentile

    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile([5], 0.95) == 5
