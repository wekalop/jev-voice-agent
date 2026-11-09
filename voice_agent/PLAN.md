# Plan: Jev voice agent latency test

## What I checked first

- `company/` and `tests/` are in place, with 100 labeled questions, and `validate.py` passes.
- Jev's API comes from TypeSafe's docs: `POST https://api.typesafe.ai/v1/systemone`, with `state`, `model`, and `questions`.
- Current Jev is `jev-1.14.0` (October 2026). `jev-1.13.0` is still available. The default pin is 1.14.0, and the version is a config value so both can be compared.
- Keep each request within the default context limits. TypeSafe says long-context mode is slower and less accurate.
- Stack: Groq Whisper, Jev, Groq `qwen/qwen3.8-27b` with reasoning off, Cartesia `sonic-3.6-2026-08-27`, Pipecat 1.15.
- Groq shut down both Llama models on 16 August 2026, so `openai/gpt-oss-120b` is the fallback and `openai/gpt-oss-20b` is the classifier.

## Structure

- `agent/` holds the shared turn logic: Jev decisions, knowledge routing, identity gate, reply prompt, timing log.
- `agent/providers/` holds swappable STT, LLM, and TTS modules, chosen in `.env`.
- `benchmark/` holds the replay, the LLM classifier baseline, and the report.
- `live/` holds the Pipecat browser mode, which calls the same turn logic as the replay.

## Critical path

Speech end -> STT -> Jev -> LLM first sentence -> TTS first audio.

- STT, Jev routing, the LLM, and TTS each depend on the step before, so they stay in sequence.
- All routing questions go in one Jev request, because Jev answers them in parallel.
- `answerable_from_kb` needs the knowledge file, which depends on the intent. Three modes will be measured:
  - `one_call`: one request with a topic list, not the file. Fastest, least informed.
  - `two_call`: route first, then judge with the file loaded. Adds a second Jev call on the critical path.
  - `two_call_parallel`: the second Jev call races the LLM, and nothing is spoken until it says yes. Latency is the slower of the two, but a decline wastes an LLM call.
- Off the critical path: writing logs, and the TTS connection, which is opened ahead of time.

## Safety in code, not just in the prompt

- A customer's record is only added to the LLM prompt after identity checks pass in code. Without that, the model has no data to leak.
- `support_policy.md` goes into every reply prompt.

## Benchmark

- Decisions: all 100 questions through Jev and through the LLM classifier.
- Replies: a stratified sample, because the Groq free tier may not cover 100 full replies a day.
- Speech: test audio is generated once with Cartesia and cached, so STT and end-to-end timing can run without a microphone.
- Report median and p95, with the first run flagged as a cold start.

## Needs from you

- A `.env` with `JEV_API_KEY`, `GROQ_API_KEY`, and `CARTESIA_API_KEY`. Cerebras is optional.
- A few minutes of talking to the agent in the browser, since live voice timing needs a real microphone.
