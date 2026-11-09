// Calls to the cloud services. Each is a plain HTTP request, which is what lets this run on Workers.

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export const STT_HINT =
  "Nabda telecom company. Go Smart, Go Mini, Go Plus, Go Youth, Go Week, Flex Silver, Flex Gold, Flex Platinum, " +
  "Boost, Home Fiber, Home Air, eSIM, roaming. Maadi, Smouha, Sidi Gaber.";

export function makeJev(env, fetcher = fetch) {
  const model = env.JEV_MODEL || "jev-1.14.0";
  const base = (env.JEV_BASE_URL || "https://api.typesafe.ai").replace(/\/$/, "");
  return {
    model,
    async decide(state, questions) {
      for (let attempt = 0; ; attempt++) {
        const started = Date.now();
        const response = await fetcher(`${base}/v1/systemone`, {
          method: "POST",
          headers: { authorization: `Bearer ${env.JEV_API_KEY}`, "content-type": "application/json" },
          body: JSON.stringify({ model, state, questions }),
        });
        // TypeSafe's docs say to back off on these two and not to retry anything else.
        if ((response.status === 429 || response.status === 529) && attempt < 2) {
          await sleep(500 * 2 ** attempt);
          continue;
        }
        if (!response.ok) throw new Error(`Jev returned ${response.status}`);
        const data = await response.json();
        return { answers: data.answers, ms: Date.now() - started, model: data.model, usage: data.usage || {} };
      }
    },
  };
}

function modelOptions(model) {
  // Qwen can turn reasoning off. GPT-OSS can't, so it gets the lowest setting and tokens to allow for its reasoning.
  if (model.startsWith("qwen/")) return [{ reasoning_effort: "none" }, 0];
  if (model.startsWith("openai/gpt-oss")) return [{ reasoning_effort: "low", include_reasoning: false }, 300];
  return [{}, 0];
}

const retryAfter = response => {
  const seconds = Number(response.headers.get("retry-after"));
  return Number.isFinite(seconds) && seconds > 0 ? seconds : 5;
};

export function makeLlm(env, fetcher = fetch) {
  const base = (env.GROQ_BASE_URL || "https://api.groq.com/openai/v1").replace(/\/$/, "");
  const replyModel = env.LLM_MODEL || "qwen/qwen3.8-27b";
  const fallbackModel = env.LLM_FALLBACK_MODEL || "openai/gpt-oss-120b";
  const smallModel = env.CLASSIFIER_MODEL || "openai/gpt-oss-20b";
  const post = body =>
    fetcher(`${base}/chat/completions`, {
      method: "POST",
      headers: { authorization: `Bearer ${env.GROQ_API_KEY}`, "content-type": "application/json" },
      body: JSON.stringify(body),
    });

  return {
    async completeJson(messages, maxTokens = 200) {
      const [options, extra] = modelOptions(smallModel);
      for (let attempt = 0; ; attempt++) {
        const response = await post({ model: smallModel, messages, temperature: 0, max_completion_tokens: maxTokens + extra, response_format: { type: "json_object" }, ...options });
        if (response.status === 429 && attempt < 3) {
          await sleep(retryAfter(response) * 1000);
          continue;
        }
        if (!response.ok) throw new Error(`Groq returned ${response.status}`);
        return (await response.json()).choices[0].message.content || "{}";
      }
    },

    async *stream(messages, stats, maxTokens = 140) {
      let model = replyModel;
      for (let attempt = 0; attempt < 4; attempt++) {
        const [options, extra] = modelOptions(model);
        const response = await post({ model, messages, stream: true, temperature: 0.4, max_completion_tokens: maxTokens + extra, ...options });
        if (response.status === 429) {
          const wait = retryAfter(response);
          // On a call, a different model now beats silence.
          if (wait > 1 && model !== fallbackModel) {
            model = fallbackModel;
            stats.fellBack = true;
            continue;
          }
          // Silence that long feels broken on a call, so the caller gets told instead.
          if (wait > 8) throw Object.assign(new Error("Groq rate limit"), { rateLimited: true });
          stats.waitMs += wait * 1000;
          await sleep(wait * 1000);
          continue;
        }
        if (!response.ok) throw new Error(`Groq returned ${response.status}`);
        stats.model = model;
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) return;
          buffer += value;
          const lines = buffer.split("\n");
          buffer = lines.pop();
          for (const line of lines) {
            if (!line.startsWith("data: ") || line.includes("[DONE]")) continue;
            const delta = JSON.parse(line.slice(6)).choices?.[0]?.delta?.content;
            if (delta) yield delta;
          }
        }
      }
      throw Object.assign(new Error("Groq stayed rate limited"), { rateLimited: true });
    },
  };
}

export async function transcribe(env, audio, fetcher = fetch) {
  const form = new FormData();
  form.append("file", new Blob([audio], { type: "audio/wav" }), "speech.wav");
  form.append("model", env.STT_MODEL || "whisper-large-v3-turbo");
  form.append("language", "en");
  form.append("response_format", "json");
  form.append("temperature", "0");
  form.append("prompt", STT_HINT);
  const base = (env.GROQ_BASE_URL || "https://api.groq.com/openai/v1").replace(/\/$/, "");
  const response = await fetcher(`${base}/audio/transcriptions`, { method: "POST", headers: { authorization: `Bearer ${env.GROQ_API_KEY}` }, body: form });
  if (!response.ok) throw new Error(`Whisper returned ${response.status}`);
  return (await response.json()).text.trim();
}

// Returns Cartesia's streaming response, which is passed straight to the browser as 24 kHz 16-bit audio.
export function speak(env, text, fetcher = fetch) {
  return fetcher(env.CARTESIA_BYTES_URL || "https://api.cartesia.ai/tts/bytes", {
    method: "POST",
    headers: { "x-api-key": env.CARTESIA_API_KEY, "cartesia-version": env.CARTESIA_VERSION || "2026-08-14", "content-type": "application/json" },
    body: JSON.stringify({
      model_id: env.TTS_MODEL || "sonic-3.6-2026-08-27",
      transcript: text,
      voice: { mode: "id", id: env.TTS_VOICE || "f786b574-daa5-4673-aa0c-cbe3e8534c02" },
      language: "en",
      output_format: { container: "raw", encoding: "pcm_s16le", sample_rate: 24000 },
    }),
  });
}
