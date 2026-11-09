"""Runs full turns on a sample of test questions, and times each stage.

python -m benchmark.e2e_bench --mode two_call --size 25 [--speech] [--no-tts] [--judge] [--llm-provider cerebras --llm-model ID]

--speech generates each utterance as audio once (cached), and sends it through STT first.
Lookup questions start with the caller already verified as the customer named in the notes,
because the benchmark tests the answer rather than the back-and-forth of the identity check.
"""

import argparse
import asyncio
import hashlib
import io
import json
import re
import time
import wave

from agent.config import Settings, load_settings
from agent.identity import IdentityState
from agent.providers.llm import LLMStats, make_llm
from agent.providers.stt import make_stt
from agent.providers.tts import SAMPLE_RATE, make_tts
from agent.timing import TimingLog, TurnTiming
from agent.turn import MODES, Session, stream_turn
from benchmark.common import CUSTOMER_ID, load_tests, make_agent, stratified_sample

SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

JUDGE_PROMPT = """You grade a voice customer-service agent for a fictional telecom company.
You get the caller's words, notes on what a correct response does, and the agent's reply.
Pass the reply if it follows the notes. It may be shorter than the notes, but it must not state anything that contradicts them.
A reply that asks for identity details passes when the notes describe account data and the check is reasonable.
This agent is a demo with no tools, so saying it cannot carry out an action is acceptable when it names the right next step.
Reply with JSON only: {"pass": true or false, "reason": "one short sentence"}."""


def first_sentence(text: str) -> str | None:
    """The first complete sentence, or None if there is not one yet. Does not stop at a.m. or p.m."""
    for match in SENTENCE_END.finditer(text):
        end = match.start() + 1
        if not re.search(r"\b[ap]\.m\.$", text[:end]):
            return text[:end].strip()
    return None


async def cached_audio(settings: Settings, tts, text: str) -> bytes:
    cache = settings.results_dir / "audio_cache"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{settings.tts_model}:{settings.tts_voice}:{text}".encode()).hexdigest()[:24]
    path = cache / f"{key}.wav"
    if path.exists():
        return path.read_bytes()
    pcm = b"".join([chunk async for chunk in tts.synthesize(text)])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def session_for(agent, test: dict) -> Session:
    session = Session()
    if test["category"] == "lookup":
        customer_id = CUSTOMER_ID.search(test["expected_answer_notes"]).group()
        session.identity = IdentityState(level=2, customer_id=customer_id)
    return session


async def judge(judge_llm, model: str, test: dict, reply: str) -> dict:
    content = json.dumps({"caller": test["utterance"], "notes": test["expected_answer_notes"], "agent_reply": reply})
    raw = await judge_llm.complete_json(
        [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": content}], model, LLMStats()
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"pass": None, "reason": "The judge returned invalid JSON."}


async def run_one(agent, test: dict, run: int, stt, tts, audio: bytes | None) -> dict:
    timing = TurnTiming()
    text = test["utterance"]
    if audio is not None:
        text = await stt.transcribe(audio)
        timing.mark("t_stt_done")
    session = session_for(agent, test)

    reply = ""
    spoken = False

    async def speak(sentence: str) -> None:
        timing.mark("t_first_sentence")
        async for _ in tts.synthesize(sentence):
            timing.mark("t_tts_first_audio")

    async for delta in stream_turn(agent, session, text, timing):
        reply += delta
        if tts is not None and not spoken:
            sentence = first_sentence(reply)
            if sentence:
                spoken = True
                await speak(sentence)
    if tts is not None and not spoken and reply.strip():
        await speak(reply.strip())

    row = timing.row()
    row.update(
        id=test["id"],
        category=test["category"],
        cold_start=run == 0,
        transcript=text,
        reply=reply,
        location_note="Cairo",
    )
    return row


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=MODES, default="two_call")
    parser.add_argument("--size", type=int, default=25)
    parser.add_argument("--speech", action="store_true")
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--jev-model")
    parser.add_argument("--llm-provider")
    parser.add_argument("--llm-model")
    parser.add_argument("--judge-model", default="openai/gpt-oss-120b")
    parser.add_argument("--pace", type=float, default=0, help="Seconds to wait between turns, to stay under rate limits.")
    parser.add_argument("--output")
    args = parser.parse_args()

    settings = load_settings()
    agent = make_agent(settings, args.mode, args.jev_model, args.llm_provider, args.llm_model)
    # A benchmark waits out rate limits, so that every reply comes from the model being measured.
    agent.llm.fallback_model = ""
    tests = stratified_sample(load_tests(settings), args.size)
    tts = None if args.no_tts else make_tts(settings)
    stt = make_stt(settings) if args.speech else None
    judge_llm = make_llm(settings, "groq") if args.judge else None
    label = args.output or f"e2e_{args.mode}{'_' + args.llm_provider if args.llm_provider else ''}.jsonl"
    log = TimingLog(settings.results_dir / label)

    if tts is not None:
        await tts.connect()

    for run, test in enumerate(tests):
        audio = await cached_audio(settings, tts, test["utterance"]) if args.speech else None
        try:
            row = await run_one(agent, test, run, stt, tts, audio)
        except Exception as exc:  # A failed turn is a result, so record it and carry on.
            row = {"id": test["id"], "category": test["category"], "error": f"{type(exc).__name__}: {exc}", "mode": args.mode}
            print(f"{test['id']}: FAILED {row['error']}")
        else:
            if judge_llm is not None:
                row["judge"] = await judge(judge_llm, args.judge_model, test, row["reply"])
            total = row.get("t_total") or row.get("first_sentence_ms") or row.get("jev_done_ms")
            print(f"{run + 1}/{len(tests)} {test['id']} {total} ms: {row['reply'][:80]}", flush=True)
        row["finished_at"] = time.time()
        log.write(row)
        if args.pace:
            await asyncio.sleep(args.pace)

    await agent.jev.close()
    if tts is not None:
        await tts.close()
    if stt is not None:
        await stt.close()
    print(f"Done. Results in {log.path}")


if __name__ == "__main__":
    asyncio.run(main())
