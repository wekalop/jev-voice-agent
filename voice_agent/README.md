# Jev voice agent latency test

A browser voice agent for the fictional telecom Nabda. It measures how fast TypeSafe's Jev makes decisions inside a speech-to-speech pipeline, and how accurate those decisions are. See [PLAN.md](PLAN.md) for the design.

Mic -> Groq Whisper -> Jev -> Groq Qwen -> Cartesia -> speaker

## Setup

Copy `.env.example` to `.env` and add your keys. If your Jev key comes from OpenRouter, change `JEV_BASE_URL` and `JEV_MODEL` as the file describes.

Run all commands from this folder.

```bash
.venv/Scripts/python -m agent.check
```

This checks every key and model ID against the live APIs, and prints your real Groq limits.

## Benchmarks

Decisions on all 100 questions, Jev and the LLM classifier. Uses no audio credits:

```bash
.venv/Scripts/python -m benchmark.decisions_bench
```

Full turns on a sample, with first audio timing and a judged answer. Change `--mode` to compare `one_call`, `two_call`, and `two_call_parallel`:

```bash
.venv/Scripts/python -m benchmark.e2e_bench --mode two_call --size 25 --judge
```

Add `--speech` to generate test audio and include STT timing. Add `--no-tts` to save Cartesia credits. Then build the report at `results/REPORT.md`:

```bash
.venv/Scripts/python -m benchmark.report
```

## Live voice

```bash
.venv/Scripts/python -m live.server
```

Open http://localhost:7860, press start, and talk. Each turn is added to `results/turns_live.jsonl` and shown on the page. Try a lookup by giving a mock customer's phone number, name, and last four ID digits from `../company/customers.json`.

## Tests

These use local stand-ins for the APIs, so they need no keys and say nothing about real speed or accuracy.

```bash
.venv/Scripts/python -m pytest tests -q
```

## Where things are

- `agent/turn.py`: the turn logic and the three Jev modes.
- `agent/decisions.py`: the questions sent to Jev.
- `agent/kb.py`: which knowledge file each intent loads.
- `agent/identity.py`: identity checks, enforced in code.
- `agent/prompts.py`: the reply prompt and the fixed lines.
- `agent/providers/` and `live/factory.py`: swappable STT, LLM, and TTS.

## What the timings mean

- `t_end_of_speech` is when the voice detector ends the turn, which is after its silence wait (`VAD_STOP_SECS`, default 0.5 seconds). The caller feels that wait too, but the timings start after it.
- `t_total` ends when the first audio leaves Cartesia. Playback to the browser adds a little more.
- In replay, lookup questions start with the caller already verified, and speech timing starts when the audio is sent.

## Voice cues

`agent/voice_cues.py` compares each turn's loudness, pitch, and speech rate with earlier turns in the call. Jev and the reply model get the result as a sentence. It is not an emotion detector, and the first turn has nothing to compare with. The browser's automatic gain control flattens loudness, so pitch and rate are the steadier signals. The 100 test questions are text, so they cannot measure whether this helps.

## Known limits

- The decision benchmark in `results/` ran before the conversation changes (new Jev questions and routing). Rerun it to measure the current version.
- On the free tier, Groq's token limit can switch a reply to the fallback model, `openai/gpt-oss-120b`, whose style differs slightly.
- The agent has no tools. It explains the next step, but does not really block a line, change a plan, or open a ticket.
- Voice activity detection (Silero) is a small model that runs on your machine. STT, the LLM, and TTS are all cloud APIs.
