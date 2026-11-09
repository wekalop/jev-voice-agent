# The Nabda voice agent, explained

This file explains the whole project so you can describe it to someone else. It covers what was built, why, how a call works, what was measured, and what hasn't been proven.

## 1. What the project is

**Goal:** test TypeSafe's Jev decision model inside a voice customer-service agent, and measure its **latency** (how fast) and **accuracy** (how correct).

**Nabda** is a fictional Egyptian telecom company. It exists only so the agent has something to talk about. Its plans, prices, policies, customers, orders and tickets are all made up, and they live in `company/`.

**Live address:** https://nabda-voice.hassan-medhat26.workers.dev (password-protected, runs with your PC off).

## 2. The key idea: Jev decides, the LLM writes

Jev is **not** the part that talks. It answers structured questions about the caller's latest message and returns scores. Our code then acts on those scores.

| Part | Job |
|---|---|
| **Jev** (TypeSafe, `jev-1.14.0`) | Classifies the message: intent, mood, urgency, whether a person is needed, whether the account is needed, whether it can be answered from the knowledge |
| **Our code** | Enforces the rules: identity checks, which files to use, refusals, hand-offs |
| **LLM** (Qwen on Groq) | Writes the short spoken reply from what the code gives it |

This split is the point of the project. It tests whether a fast decision model can replace slower, more error-prone logic in the middle of a voice call.

## 3. What happens in one turn

1. **Speech detection (browser):** a voice detector (Silero VAD) decides when you start and stop talking. It also measures loudness and pitch. Speaking over the agent stops its audio immediately.
2. **Speech to text:** Whisper (`whisper-large-v3-turbo` on Groq) transcribes the audio. A hint list of Nabda plan names helps it spell them correctly.
3. **First Jev call:** the Worker sends the conversation and eight questions. Jev returns scores for intent, sentiment, urgency, needs a human, needs account access, requests an account change, second topic and about another person.
4. **Code decides what to do:**
   - Asking for a person gets a fixed line, with no LLM.
   - A question about the account without verified identity triggers an identity check, with fixed wording and no LLM.
   - A question about someone else's account is refused.
   - Otherwise it picks up to **2 knowledge files** based on the intent scores.
5. **Second Jev call (the answerable gate):** Jev is asked whether the chosen files can answer the question. If not, the agent declines politely instead of guessing.
6. **LLM writes the reply:** Qwen (`qwen/qwen3.8-27b`) gets the files, Jev's decision, the conversation and the caller's voice cues. It streams back at most **3 sentences**. If Groq's rate limit hits, it switches to `openai/gpt-oss-120b`.
7. **Text to speech:** Cartesia (`sonic-3.6-2026-08-27`) voices each sentence as soon as it's complete, so you hear the start before the reply is finished.
8. **Browser plays the audio** and shows the transcript and timings.

### Two Jev modes
- `two_call` (default): routing first, then the answerable check with the chosen files in the state.
- `one_call`: one call that also includes the topic list, which is faster but chooses files with less information.

## 4. Safety rules enforced in code, not in the prompt

- **Identity before account data:** a customer's record is not put in the LLM prompt until the caller has passed the check (phone number, full name, last four digits of national ID). The LLM can't leak what it never received.
- **No hints on failure:** a wrong answer gets "that didn't match", not which detail was wrong. Repeated failures lock the check.
- **Other people's accounts are refused:** a caller asking about a spouse or relative gets a refusal, and their words aren't read as identity details.
- **Hand-offs are fixed text:** the agent gives support hours and numbers. It can't transfer calls from this test line.
- **Declines are fixed:** if the question isn't covered, the code supplies a safe line.
- **Tampered sessions reset:** the call state travels in an encrypted token, so an altered token starts a new call with no account access.

## 5. Voice cues (mood from sound)

The system looks at **loudness, pitch and speaking speed**, compared with the caller's own baseline in that call. A much louder, higher or faster voice is described to Jev in a sentence like "louder and faster than usual", so it can read anger or urgency even when the words are mild.

**Limit:** this isn't an emotion detector. It measures changes in sound relative to the caller, and early in a call it says "learning your voice" because there's no baseline yet.

## 6. The two versions of the code

| | Python version | Cloudflare Workers version (live) |
|---|---|---|
| Folder | `agent/`, `live/`, `benchmark/` | `edge/` |
| Runs on | Your PC | Cloudflare's servers |
| Audio path | WebRTC (Pipecat, SmallWebRTC) plus a Turnix relay | Plain HTTPS requests |
| Speech detection | On the server | In the browser |
| Needs your PC on | Yes | No |
| Saves timings to a log | Yes | No, shown on the page only |
| Extra mode | `two_call_parallel` | Not included |

The Python version produced the benchmarks. The Workers version is what the manager uses.

## 7. Project map

| Path | What it holds |
|---|---|
| `company/` | The fictional knowledge base: plans, billing, roaming, devices, coverage, FAQ, support policy, customers, orders and tickets |
| `tests/` | 100 test questions with expected answers and a validator |
| `voice_agent/agent/` | Python agent: Jev client, decisions, turn logic, identity |
| `voice_agent/benchmark/` | Latency and accuracy benchmark scripts |
| `voice_agent/live/` | Python live voice server and web page |
| `voice_agent/edge/` | The Workers version: `src/` (Worker), `public/` (browser page), `test/` |
| `voice_agent/results/REPORT.md` | Benchmark report |
| `voice_agent/DEPLOY.md` | How sharing and hosting work |
| `voice_agent/PLAN.md` | The plan |
| `voice_agent/project_guide.html` | The interactive project guide |
| `voice_agent/.env` | Your keys and the access password. Never share or paste it |

## 8. What was measured

These benchmarks come from the Python version and ran **before** later changes to the agent.

**Speed of the decision step (median / slowest 5%):**
- Jev `two_call`: **190 ms** / 227 ms
- A Groq LLM classifier doing a similar job: **484 ms** / 1,504 ms
- Jev's second call: about 201 ms / 238 ms

**Accuracy (intent / needs human / answerable / sentiment / urgency):**

| Method | Intent | Needs human | Answerable | Sentiment | Urgency |
|---|---|---|---|---|---|
| Jev `two_call` | 79% | 93% | 97% | 70% | 83% |
| Jev `one_call` | 85% | 96% | 90% | 76% | 80% |
| LLM classifier | 73% | 91% | 77% | 78% | 79% |

**How to read these honestly:**
- The labels aren't independent, because the questions and expected answers were written by the same side.
- Jev was clearly faster and steadier. On accuracy it was better on most measures but not all: the classifier won on sentiment in `two_call`.
- The end-to-end benchmark (full speech to speech) was stopped after 2 of 25 turns, so the full-turn numbers aren't in the report.
- On the live Workers version, one clean test turn reached first audio in about **2.1 s**: speech to text about 0.67 s, Jev about 1.16 s, LLM about 1.78 s, all cumulative. That's one turn, not a benchmark.

## 9. Models and services

| Job | Service | Model |
|---|---|---|
| Decisions | TypeSafe Jev | `jev-1.14.0` |
| Speech to text | Groq | `whisper-large-v3-turbo` |
| Reply | Groq | `qwen/qwen3.8-27b`, fallback `openai/gpt-oss-120b` |
| Identity extraction | Groq | `openai/gpt-oss-20b` |
| Text to speech | Cartesia | `sonic-3.6-2026-08-27` |
| Hosting | Cloudflare Workers | Free plan |

Groq's free plan allows about 8,000 tokens a minute per model, which affects rapid back-to-back calls.

## 10. Known limits and unproven items

- **Groq free limit:** in a live test, 3 of 12 voice turns sent about 13 seconds apart got "reached its free usage limit". The next turn recovered each time.
- **Cloudflare free CPU limit:** turns used up to 24 ms of CPU against the free plan's 10 ms limit, and none was stopped. Cloudflare could still enforce it, which would show as Error 1102.
- **First call is slower:** the browser downloads about 17 MB for the voice detector.
- **No lockout on password guesses.** Keep the password long and random.
- **Ten-minute call limit** runs in the browser page, not on the server.
- **Timings on the Workers version include the network**, because they're measured in the browser.
- **Voice cue behaviour with real emotional speech** hasn't been tested with a wide range of speakers.
- **Untried:** your manager's network and equipment.

## 11. How to run, test and redeploy

From `voice_agent/edge`:

```bash
npm test
```

```bash
npm run build
```

```bash
npx wrangler deploy
```

```bash
node scripts/sync_secrets.mjs remote
```

To watch a live call: `npx wrangler tail nabda-voice`.

To run the Python version locally, run the agent server from `voice_agent`, with the steps in `DEPLOY.md`.

