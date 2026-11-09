// The call page. Voice detection, voice measurements, and playback run here in the browser,
// and the Worker does everything that needs a key.

const $ = id => document.getElementById(id);
const STATES = {
  idle: ["Ready to call", "Press start and allow your microphone."],
  requesting: ["Waiting for your microphone", "Allow access in your browser."],
  connecting: ["Getting ready", "The first call loads the voice detector, which can take a few seconds."],
  listening: ["Listening", "Go ahead, I can hear you."],
  hearing: ["Hearing you", "Keep going. I reply when you pause."],
  thinking: ["Thinking", "Working out your answer."],
  speaking: ["Speaking", "You can talk over me at any time."],
  ended: ["Call ended", "Press start to call again."],
};
const PATH_TAGS = { fixed_identity: "Fixed line", fixed_human: "Fixed line", decline: "Not in knowledge base" };
const MAX_CALL_MS = 10 * 60 * 1000;
const SAMPLE_RATE = 16000;
const VOICE_RATE = 24000;

let audioContext = null, vadInstance = null, micStream = null, inCall = false, muted = false, localState = "idle";
let sessionToken = "", turns = [], greeting = null, callTimer = null;
let active = null; // The turn in progress: { turn, controller, done, heard, promise }
let pendingPrefix = ""; // Words from a turn that was cut off before the caller heard any reply.
let nextStart = 0, sources = new Set(), speakChain = Promise.resolve(), speakGeneration = 0, speakControllers = new Set(), idleTimer = null;

function setState(state) {
  localState = state;
  const [word, sub] = STATES[state] || STATES.idle;
  $("call").dataset.state = state;
  $("stateWord").textContent = word;
  $("stateSub").textContent = muted ? "Your microphone is muted." : sub;
}

function showAlert(title, detail) {
  const box = $("alert");
  box.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = title;
  box.append(strong, detail);
  box.hidden = false;
}

function setCallButton(calling, busy) {
  $("callLabel").textContent = busy ? "Getting ready" : calling ? "End call" : "Start call";
  $("callButton").classList.toggle("end", calling && !busy);
  $("callButton").disabled = busy;
  $("mute").hidden = !calling || busy;
}

// Voice measurements. The same method as agent/voice_cues.py.
function measureVoice(samples) {
  const frameLength = Math.floor(0.03 * SAMPLE_RATE), hop = Math.floor(0.02 * SAMPLE_RATE);
  const minLag = Math.floor(SAMPLE_RATE / 400), maxLag = Math.min(Math.floor(SAMPLE_RATE / 75), frameLength - 1);
  const start = Math.max(0, samples.length - 15 * SAMPLE_RATE);
  const levels = [];
  for (let i = start; i + frameLength <= samples.length; i += hop) {
    let sum = 0;
    for (let j = 0; j < frameLength; j++) sum += samples[i + j] ** 2;
    levels.push(Math.sqrt(sum / frameLength));
  }
  if (!levels.length) return null;
  const sorted = [...levels].sort((a, b) => a - b);
  const threshold = Math.max(0.003, sorted[Math.floor(sorted.length * 0.9)] * 0.1);
  const voicedLevels = [], pitches = [], positions = [];
  const frame = new Float32Array(frameLength);
  levels.forEach((level, index) => {
    if (level <= threshold) return;
    const offset = start + index * hop;
    let mean = 0;
    for (let j = 0; j < frameLength; j++) mean += samples[offset + j];
    mean /= frameLength;
    for (let j = 0; j < frameLength; j++) frame[j] = samples[offset + j] - mean;
    let energy = 0;
    for (let j = 0; j < frameLength; j++) energy += frame[j] * frame[j];
    if (energy <= 0) return;
    let bestLag = minLag, best = -Infinity;
    for (let lag = minLag; lag <= maxLag; lag++) {
      let total = 0;
      for (let j = 0; j + lag < frameLength; j++) total += frame[j] * frame[j + lag];
      if (total > best) [best, bestLag] = [total, lag];
    }
    if (best / energy >= 0.5) {
      pitches.push(SAMPLE_RATE / bestLag);
      voicedLevels.push(level);
      positions.push(index);
    }
  });
  if (pitches.length * 0.02 < 0.4) return null;
  pitches.sort((a, b) => a - b);
  const mean = voicedLevels.reduce((a, b) => a + b, 0) / voicedLevels.length;
  return {
    loudness_db: 20 * Math.log10(mean),
    pitch_hz: pitches[pitches.length >> 1],
    speech_seconds: (positions[positions.length - 1] - positions[0] + 1) * 0.02,
  };
}

function toWav(samples) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, value)) * 32767, true));
  return buffer;
}

// Playback. Each sentence is fetched straight away, and played in order.
function stopAudio() {
  speakGeneration += 1;
  speakControllers.forEach(controller => controller.abort());
  speakControllers.clear();
  sources.forEach(source => {
    try { source.stop(); } catch (error) { /* It had already finished. */ }
  });
  sources.clear();
  nextStart = 0;
  speakChain = Promise.resolve();
  clearTimeout(idleTimer);
}

function scheduleChunk(bytes) {
  const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength >> 1);
  const buffer = audioContext.createBuffer(1, samples.length, VOICE_RATE);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  const at = Math.max(nextStart, audioContext.currentTime + 0.02);
  source.start(at);
  nextStart = at + buffer.duration;
  sources.add(source);
  source.onended = () => {
    sources.delete(source);
    if (!sources.size && inCall && localState === "speaking") {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => { if (!sources.size && localState === "speaking") setState("listening"); }, 250);
    }
  };
}

function speakSentence(text, onFirstAudio) {
  const generation = speakGeneration;
  const controller = new AbortController();
  speakControllers.add(controller);
  const response = fetch("/api/speak", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
    signal: controller.signal,
  });
  speakChain = speakChain.then(async () => {
    if (generation !== speakGeneration) return;
    const reply = await response;
    if (!reply.ok) throw new Error(`voice ${reply.status}`);
    const reader = reply.body.getReader();
    let leftover = new Uint8Array(0);
    for (;;) {
      const { value, done } = await reader.read();
      if (done || generation !== speakGeneration) return;
      const joined = new Uint8Array(leftover.length + value.length);
      joined.set(leftover);
      joined.set(value, leftover.length);
      const usable = joined.length & ~1;
      if (usable) {
        scheduleChunk(joined.slice(0, usable));
        if (onFirstAudio) { onFirstAudio(); onFirstAudio = null; }
        setState("speaking");
      }
      leftover = joined.slice(usable);
    }
  }).catch(error => {
    if (error.name !== "AbortError") showAlert("The voice didn't come through", "The reply is still shown below. Please try again.");
  }).finally(() => speakControllers.delete(controller));
}

// One turn: send the speech, show the reply as it arrives, and speak each complete sentence.
const SENTENCE_END = /(?<![ap]\.m)[.!?](?=\s)/;

async function handleSpeech(samples) {
  const started = performance.now();
  const previous = active;
  const prefix = pendingPrefix;
  pendingPrefix = "";
  if (previous && !previous.done) await previous.promise.catch(() => {});
  if (!inCall) return;
  setState("thinking");
  const run = { turn: null, controller: new AbortController(), done: false, heard: false, muted: false, prefix };
  active = run;
  run.promise = runTurn(samples, run, started, prefix).finally(() => { run.done = true; });
  return run.promise;
}

async function runTurn(samples, run, started, prefix) {
  const elapsed = () => performance.now() - started;
  const headers = { "content-type": "audio/wav", "x-session": sessionToken, "x-voice": JSON.stringify(measureVoice(samples)) };
  if (prefix) headers["x-prefix"] = encodeURIComponent(prefix);
  let text = "", spokenUpTo = 0;
  const flush = final => {
    if (run.muted) return;
    for (;;) {
      const rest = text.slice(spokenUpTo);
      const match = SENTENCE_END.exec(rest);
      if (match) {
        const end = match.index + 1;
        queueSpeech(rest.slice(0, end).trim());
        spokenUpTo += end;
      } else {
        if (final && rest.trim()) {
          queueSpeech(rest.trim());
          spokenUpTo = text.length;
        }
        return;
      }
    }
  };
  const queueSpeech = sentence => speakSentence(sentence, () => {
    run.heard = true;
    if (run.turn && run.turn.timings.total === null) {
      run.turn.timings.total = elapsed();
      render();
    }
  });
  try {
    const response = await fetch("/api/turn", { method: "POST", headers, body: toWav(samples), signal: run.controller.signal });
    if (response.status === 401) { location.href = "/login"; return; }
    if (!response.ok) throw new Error(`turn ${response.status}`);
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "heard") {
          run.turn = { caller: event.text, agent: "", voice: event.voice, path: null, fallback: false, cold: turns.length === 0, waited: false, timings: { stt: elapsed(), jev: null, llm: null, total: null } };
          turns.push(run.turn);
        } else if (event.type === "meta" && run.turn) {
          run.turn.timings.jev = elapsed();
          run.turn.path = event.path;
          if (event.decision) { run.turn.mood = event.decision.sentiment; run.turn.urgency = event.decision.urgency; }
        } else if (event.type === "text" && run.turn) {
          if (run.turn.timings.llm === null && (run.turn.path === "llm" || run.turn.path === "decline")) run.turn.timings.llm = elapsed();
          text += event.text;
          run.turn.agent = text;
          flush(false);
        } else if (event.type === "stats" && run.turn) {
          run.turn.fallback = event.fell_back;
          run.turn.waited = event.wait_ms > 0;
        } else if (event.type === "done") {
          sessionToken = event.session;
          flush(true);
        } else if (event.type === "empty") {
          if (localState === "thinking") setState("listening");
        } else if (event.type === "error") {
          showAlert("That didn't go through", event.detail);
          if (run.turn && !run.turn.agent) turns = turns.filter(item => item !== run.turn);
          if (localState === "thinking") setState("listening");
        }
        render();
      }
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    showAlert("Couldn't reach the agent", "Check your connection, then try again.");
    if (localState === "thinking") setState("listening");
  }
}

function interrupted() {
  // Talking over the agent stops the voice. A reply they've started hearing is kept, without speaking the rest.
  if (active && !active.done) {
    active.muted = true;
    if (!active.heard) {
      // A pause mid-sentence: the first part had no reply yet, so both parts become one question.
      pendingPrefix = active.turn ? active.turn.caller : active.prefix || "";
      active.controller.abort();
      if (active.turn) turns = turns.filter(item => item !== active.turn);
      render();
    }
  }
  stopAudio();
  setState("hearing");
}

async function startCall() {
  $("alert").hidden = true;
  setCallButton(false, true);
  setState("requesting");
  try {
    audioContext = new AudioContext();
    await audioContext.resume();
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (error) {
    const denied = error.name === "NotAllowedError";
    showAlert(denied ? "Microphone access is blocked" : "No microphone found",
      `${denied ? "Allow the microphone for this page in your browser settings, then try again." : "Connect a microphone, then try again."} The Claude app preview blocks microphones, so open this page in Chrome or Edge if you are in it.`);
    setCallButton(false, false);
    setState("idle");
    return;
  }
  try {
    setState("connecting");
    ort.env.wasm.numThreads = 1;
    ort.env.wasm.wasmPaths = "/vendor/";
    vadInstance = await vad.MicVAD.new({
      model: "legacy",
      baseAssetPath: "/vendor/",
      onnxWASMBasePath: "/vendor/",
      getStream: async () => micStream,
      pauseStream: async () => {},
      resumeStream: async () => micStream,
      redemptionMs: 500,
      preSpeechPadMs: 300,
      minSpeechMs: 200,
      onSpeechStart: () => { if (localState !== "speaking") setState("hearing"); },
      onSpeechRealStart: interrupted,
      onSpeechEnd: audio => { if (inCall) handleSpeech(audio); },
      onVADMisfire: () => { if (localState === "hearing") setState("listening"); },
    });
    await vadInstance.start();
    inCall = true;
    turns = [];
    sessionToken = "";
    active = null;
    pendingPrefix = "";
    const config = await (await fetch("/api/config")).json();
    greeting = config.greeting;
    render();
    setCallButton(true, false);
    setState("listening");
    speakSentence(greeting, null);
    callTimer = setTimeout(() => {
      showAlert("The call reached its ten minute limit", "Press start to call again.");
      endCall();
    }, MAX_CALL_MS);
  } catch (error) {
    console.error(error);
    showAlert("Couldn't start the call", "The voice detector didn't load. Reload the page, then try again.");
    endCall();
  }
}

function endCall() {
  inCall = false;
  clearTimeout(callTimer);
  if (active && !active.done) active.controller.abort();
  stopAudio();
  // A reply that never arrived shouldn't stay on screen as "Thinking".
  turns = turns.filter(turn => turn.agent);
  render();
  if (vadInstance) { vadInstance.destroy(); vadInstance = null; }
  if (micStream) { micStream.getTracks().forEach(track => track.stop()); micStream = null; }
  if (audioContext) { audioContext.close(); audioContext = null; }
  muted = false;
  $("mute").setAttribute("aria-pressed", "false");
  $("mute").setAttribute("aria-label", "Mute microphone");
  setCallButton(false, false);
  setState(turns.length ? "ended" : "idle");
}

$("callButton").addEventListener("click", () => (inCall ? endCall() : startCall()));
$("mute").addEventListener("click", () => {
  muted = !muted;
  if (micStream) micStream.getAudioTracks().forEach(track => { track.enabled = !muted; });
  $("mute").setAttribute("aria-pressed", String(muted));
  $("mute").setAttribute("aria-label", muted ? "Unmute microphone" : "Mute microphone");
  setState(localState);
});

// Showing the conversation and the timings.
function bubble(kind, name, text, meta) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${kind}`;
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = name;
  const body = document.createElement("div");
  body.className = "bubble";
  if (text) body.textContent = text;
  else { body.classList.add("typing"); body.textContent = "Thinking"; }
  wrap.append(who, body);
  if (meta && meta.length) {
    const row = document.createElement("div");
    row.className = "meta";
    meta.forEach(item => {
      const span = document.createElement("span");
      span.textContent = item.text;
      if (item.tag) span.className = "tag";
      if (item.mono) span.classList.add("mono");
      row.append(span);
    });
    wrap.append(row);
  }
  return wrap;
}

const ms = value => (value === null || value === undefined ? "--" : `${Math.round(value).toLocaleString("en-US")} ms`);
const seconds = value => `${(value / 1000).toFixed(2)} s`;

function turnMeta(turn) {
  const meta = [];
  if (turn.timings.total) meta.push({ text: `${seconds(turn.timings.total)} to first audio`, mono: true });
  if (PATH_TAGS[turn.path]) meta.push({ text: PATH_TAGS[turn.path], tag: true });
  if (turn.fallback) meta.push({ text: "Fallback model", tag: true });
  if (turn.waited) meta.push({ text: "Rate limit wait", tag: true });
  return meta;
}

function renderTranscript() {
  const box = $("transcript");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const nodes = [];
  if (greeting) nodes.push(bubble("agent", "Nabda assistant", greeting));
  turns.forEach(turn => {
    const heard = [];
    if (turn.mood) heard.push({ text: `Mood: ${turn.mood}` });
    if (turn.urgency) heard.push({ text: `Urgency: ${turn.urgency}` });
    if (turn.voice) heard.push({ text: `Voice: ${turn.voice}` });
    nodes.push(bubble("caller", "You", turn.caller, heard));
    nodes.push(bubble("agent", "Nabda assistant", turn.agent, turnMeta(turn)));
  });
  if (nodes.length) box.replaceChildren(...nodes);
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

function renderLatest() {
  const turn = [...turns].reverse().find(item => item.timings.total);
  if (!turn) return;
  const { stt, jev, llm, total } = turn.timings;
  const parts = [];
  let previous = 0;
  [["stt", stt], ["jev", jev], ["llm", llm], ["tts", total]].forEach(([name, reached]) => {
    if (reached === null || reached === undefined) { parts.push([name, null]); return; }
    parts.push([name, Math.max(0, reached - previous)]);
    previous = reached;
  });
  $("latestTotal").replaceChildren(seconds(total));
  $("latestHint").textContent = turn.cold ? "First turn of the call, so it may be slower." : "Time from when you stop talking to the first audio.";
  const ids = { stt: "vStt", jev: "vJev", llm: "vLlm", tts: "vTts" };
  $("stack").replaceChildren(...parts.filter(([, value]) => value).map(([name, value]) => {
    const span = document.createElement("span");
    span.className = name;
    span.style.flex = `${value} 0 0`;
    return span;
  }));
  parts.forEach(([name, value]) => { $(ids[name]).textContent = value === null ? "Skipped" : ms(value); });
}

const SUMMARY_ROWS = [["Speech to text", "stt"], ["Jev decisions", "jev"], ["Reply first word", "llm"], ["First audio", "total"]];
function renderSummary() {
  const timed = turns.filter(turn => turn.timings.total && !turn.cold && !turn.waited).map(turn => turn.timings);
  const rows = SUMMARY_ROWS.map(([label, key]) => {
    const values = timed.map(item => item[key]).filter(value => value !== null).sort((a, b) => a - b);
    const median = values.length ? (values.length % 2 ? values[values.length >> 1] : (values[(values.length >> 1) - 1] + values[values.length >> 1]) / 2) : null;
    const p95 = values.length ? values[Math.max(0, Math.ceil(values.length * 0.95) - 1)] : null;
    const tr = document.createElement("tr");
    [label, median === null ? "--" : Math.round(median).toLocaleString("en-US"), p95 === null ? "--" : Math.round(p95).toLocaleString("en-US")].forEach((text, index) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (index) td.className = "mono";
      tr.append(td);
    });
    return tr;
  });
  $("summary").replaceChildren(...rows);
  $("summaryHint").textContent = timed.length < 20
    ? `${timed.length} of 20 turns measured. The first turn and rate-limit waits don't count.`
    : `${timed.length} turns measured, in milliseconds from the end of speech.`;
}

function render() {
  renderTranscript();
  renderLatest();
  renderSummary();
}
renderSummary();

(function setUpTheme() {
  const root = document.documentElement;
  try {
    const saved = localStorage.getItem("theme");
    if (saved) root.dataset.theme = saved;
  } catch (error) { /* Storage may be blocked, and the system theme still works. */ }
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (error) { /* See above. */ }
  });
})();
