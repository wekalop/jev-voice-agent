// Compares a caller's voice with earlier in the call. Ported from agent/voice_cues.py.
// The browser measures the audio and sends only three numbers. The sentence is written here,
// so that a caller can't put their own words into the prompt through this route.

const MIN_WORDS = 3, LOUDER_DB = 3, PITCH_SEMITONES = 2, RATE_CHANGE = 0.2, KEEP = 20;

const median = values => {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

export function validSample(sample) {
  if (!sample) return null;
  const { loudness_db: loudness, pitch_hz: pitch, speech_seconds: seconds } = sample;
  const ok = Number.isFinite(loudness) && loudness >= -120 && loudness <= 0 && Number.isFinite(pitch) && pitch >= 50 && pitch <= 500 && Number.isFinite(seconds) && seconds > 0 && seconds <= 60;
  return ok ? { loudness, pitch, seconds } : null;
}

export const newBaseline = () => ({ loudness: [], pitch: [], rate: [] });

// Returns a sentence for Jev (or null), and a short label for the call page.
export function describeAndLearn(baseline, sample, words) {
  if (!sample || words < MIN_WORDS) return [null, "too short to judge"];
  const rate = words / sample.seconds;
  let description = null, label = "learning your voice";
  if (baseline.loudness.length) {
    const loudnessChange = sample.loudness - median(baseline.loudness);
    const semitones = 12 * Math.log2(sample.pitch / median(baseline.pitch));
    const rateChange = rate / median(baseline.rate) - 1;
    const cues = [];
    if (loudnessChange >= LOUDER_DB) cues.push("louder");
    else if (loudnessChange <= -LOUDER_DB) cues.push("quieter");
    if (semitones >= PITCH_SEMITONES) cues.push("higher pitched");
    else if (semitones <= -PITCH_SEMITONES) cues.push("lower pitched");
    if (rateChange >= RATE_CHANGE) cues.push("faster");
    else if (rateChange <= -RATE_CHANGE) cues.push("slower");
    label = cues.length ? cues.join(", ") : "steady";
    description = `Compared with earlier in this call, the caller's voice sounds ${cues.length ? cues.join(" and ") : "about the same"}. This comes from audio measurements, and it can have other causes, such as background noise.`;
  }
  baseline.loudness = [...baseline.loudness, sample.loudness].slice(-KEEP);
  baseline.pitch = [...baseline.pitch, sample.pitch].slice(-KEEP);
  baseline.rate = [...baseline.rate, rate].slice(-KEEP);
  return [description, label];
}
