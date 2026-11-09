// One caller turn: identity, Jev decisions, knowledge routing, gates, and the streamed reply.
// Ported from agent/turn.py, with the two_call and one_call modes. It yields events as they happen.

import { answerableQuestion, parseDecision, routingQuestions } from "./decisions.js";
import { absorb, isLocked, missingForLevelOne } from "./identity.js";
import * as prompts from "./prompts.js";

const MAX_HISTORY = 12;
const MAX_MESSAGE_CHARS = 600;
const MAX_FILES = 2;
const MAX_SENTENCES = 3;
const NO_GATE_INTENTS = new Set(["chitchat", "human_request"]);
const NO_ACCOUNT_INTENTS = new Set(["chitchat", "human_request", "out_of_scope"]);
const DETAILS_PLACEHOLDER = "[The caller gave identity details.]";
const NOW = "Tuesday, the third of November 2026, nine a.m. Cairo time";

const sentenceEnds = text => [...text.matchAll(/(?<![ap]\.m)[.!?](?=\s)/g)];

export function withinLimit(soFar, delta) {
  const ends = sentenceEnds(soFar + delta);
  if (ends.length < MAX_SENTENCES) return [delta, false];
  return [(soFar + delta).slice(soFar.length, ends[MAX_SENTENCES - 1].index + 1), true];
}

function remember(session, role, content) {
  session.history.push({ role, content: content.slice(0, MAX_MESSAGE_CHARS) });
  session.history = session.history.slice(-MAX_HISTORY);
}
function rememberBoth(session, caller, reply) {
  remember(session, "user", caller);
  remember(session, "assistant", reply);
}

function jevState(session, text, extra = {}) {
  const messages = session.history.map(m => ({ from: m.role === "user" ? "customer" : "agent", text: m.content }));
  messages.push({ from: "customer", text });
  return { messages, ...extra };
}

function requiredLevel(decision, handOff) {
  if (handOff || NO_ACCOUNT_INTENTS.has(decision.intent)) return 0;
  if (decision.p_requests_account_change >= 0.5) return 2;
  if (decision.p_needs_account_access >= 0.5) return 1;
  return 0;
}

function nextIdentityPrompt(session, failed, progress) {
  const identity = session.identity;
  if (identity.level > 0) return prompts.ASK_FOR_BIRTH_DATE;
  const missing = missingForLevelOne(identity);
  if (failed) return `${prompts.NOT_MATCHED} ${prompts.askAgain(missing)}`;
  if (identity.phone_partial) {
    const rest = missing.filter(item => !item.includes("phone"));
    return prompts.PARTIAL_PHONE + (rest.length ? ` I also need ${prompts.joinItems(rest)}.` : "");
  }
  return progress ? prompts.askForRest(missing) : prompts.askForIdentity(missing);
}

export async function* runTurn({ session, text, voiceCues = null, deps }) {
  const { jev, llm, kb, env } = deps;
  const mode = env.JEV_MODE === "one_call" ? "one_call" : "two_call";
  const thresholdHuman = Number(env.THRESHOLD_NEEDS_HUMAN ?? 0.7);
  const thresholdAnswerable = Number(env.THRESHOLD_ANSWERABLE ?? 0.3);
  const identity = session.identity;
  let decideText = text;
  let historyText = text;

  const fixed = function* (path, reply, decision = null) {
    yield { type: "meta", path, decision };
    yield { type: "text", text: reply };
  };

  // A check is in progress, so read the details first. Ordinary turns skip this.
  if (session.pending_question !== null) {
    const failuresBefore = identity.failures;
    const gaveDetails = await absorb(identity, text, kb, llm);
    if (gaveDetails) historyText = DETAILS_PLACEHOLDER;
    if (identity.level >= session.pending_level) {
      decideText = session.pending_question;
      historyText = `[Identity confirmed.] ${session.pending_question}`;
      session.pending_question = null;
    } else if (isLocked(identity)) {
      session.pending_question = null;
      rememberBoth(session, historyText, prompts.LOCKED);
      return yield* fixed("fixed_identity", prompts.LOCKED);
    } else if (gaveDetails) {
      // Still incomplete or wrong. No Jev call is needed to ask again, and no hint is given.
      const reply = nextIdentityPrompt(session, identity.failures > failuresBefore, true);
      rememberBoth(session, historyText, reply);
      return yield* fixed("fixed_identity", reply);
    }
  }

  // First Jev call. Nothing else can start until it returns, because it picks the files.
  const routingState = mode === "one_call" ? jevState(session, decideText, { knowledge_topics: kb.topics }) : jevState(session, decideText);
  if (voiceCues) routingState.voice_cues = voiceCues;
  const first = await jev.decide(routingState, routingQuestions(mode === "one_call", Boolean(voiceCues)));
  const decision = parseDecision(first.answers);
  const jevMs = { routing: first.ms, answerable: null };
  yield { type: "decided", jev_ms: first.ms, model: first.model };

  const handOff = decision.p_needs_human >= thresholdHuman || decision.intent === "human_request";
  const otherPerson = decision.p_about_another_person >= 0.5;

  // The caller moved on to something that does not need the account, so drop the check.
  if (session.pending_question !== null && (otherPerson || handOff || NO_ACCOUNT_INTENTS.has(decision.intent) || decision.p_needs_account_access < 0.5)) {
    session.pending_question = null;
  }

  // Asking for a person gets a straight answer, with no check and no LLM.
  if (decision.intent === "human_request") {
    rememberBoth(session, historyText, prompts.HUMAN_REQUEST);
    return yield* fixed("fixed_human", prompts.HUMAN_REQUEST, decision);
  }

  // Someone else's details are refused, so no check is asked for and their words are never read as identity.
  const required = otherPerson ? 0 : requiredLevel(decision, handOff);
  if (required > identity.level) {
    if (isLocked(identity)) {
      rememberBoth(session, historyText, prompts.LOCKED);
      return yield* fixed("fixed_identity", prompts.LOCKED, decision);
    }
    if (session.pending_question !== null) {
      const reply = nextIdentityPrompt(session, false, true);
      rememberBoth(session, historyText, reply);
      return yield* fixed("fixed_identity", reply, decision);
    }
    // The first message may already hold the details, so read it before asking.
    session.pending_question = decideText;
    session.pending_level = required;
    await absorb(identity, text, kb, llm);
    if (identity.level >= required) session.pending_question = null;
    else {
      const reply = nextIdentityPrompt(session, false, false);
      rememberBoth(session, text, reply);
      return yield* fixed("fixed_identity", reply, decision);
    }
  }

  let files = kb.selectFiles(decision.intent_probabilities, decision.intent, decision.second_topic);
  // A short follow-up, such as "and how much is that?", still needs the file from the last answer.
  if (NO_GATE_INTENTS.has(decision.intent) || decision.intent === "out_of_scope" || decision.intent_confidence < 0.7) {
    files = [...files, ...session.last_files.filter(name => !files.includes(name))];
  }
  files = files.slice(0, MAX_FILES);
  const customer = identity.customer_id ? kb.customerById(identity.customer_id) : null;
  const customerView = customer && !otherPerson ? kb.customerView(customer) : null;
  const gateApplies = !handOff && !otherPerson && !NO_GATE_INTENTS.has(decision.intent);

  if (gateApplies && mode !== "one_call") {
    const knowledge = Object.fromEntries(files.map(name => [name, kb.replyFiles[name]]));
    knowledge["support_policy.md"] = kb.files["support_policy.md"];
    const second = await jev.decide(
      jevState(session, decideText, { knowledge, account_data: customerView || "Available once the caller passes identity checks." }),
      { answerable_from_kb: answerableQuestion() },
    );
    decision.p_answerable = second.answers.answerable_from_kb.noul;
    jevMs.answerable = second.ms;
  }
  const passes = !gateApplies || decision.p_answerable === null || decision.p_answerable >= thresholdAnswerable;
  yield { type: "meta", path: passes ? "llm" : "decline", decision, jev_ms: jevMs, files };

  remember(session, "user", historyText);
  session.last_files = files;
  const messages = prompts.buildMessages({
    now: NOW,
    supportPolicy: kb.files["support_policy.md"],
    knowledge: passes ? Object.fromEntries(files.map(name => [name, kb.replyFiles[name]])) : {},
    decision,
    customerView,
    identityLevel: identity.level,
    handOff,
    history: session.history,
    notCovered: !passes,
    otherPerson,
    voiceCues,
  });

  const stats = { model: "", fellBack: false, waitMs: 0 };
  let reply = "";
  try {
    for await (const delta of llm.stream(messages, stats)) {
      const [piece, finished] = withinLimit(reply, delta);
      reply += piece;
      if (piece) yield { type: "text", text: piece };
      if (finished) break;
    }
  } catch (error) {
    if (reply) throw error;
    // A decline has a safe fixed line. Anything else surfaces as an error to the caller.
    if (passes) throw error;
    reply = prompts.DECLINE;
    yield { type: "text", text: reply };
  }
  remember(session, "assistant", reply || "(no reply)");
  yield { type: "stats", model: stats.model, fell_back: stats.fellBack, wait_ms: stats.waitMs };
}
