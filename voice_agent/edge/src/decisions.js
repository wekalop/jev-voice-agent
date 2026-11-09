// The questions Jev answers each turn, and how the answers are read. Ported from agent/decisions.py.

export const INTENT_CRITERIA = {
  billing: "Bills, charges, due dates, late fees, payments, refunds, or disputes.",
  plan_info: "What the company offers: plans, bundles, prices, and general company details such as stores and opening hours.",
  plan_change: "Moving a line to a different plan, or how and when a plan change takes effect.",
  coverage_outage: "Network coverage in a place, or whether there is a known outage.",
  roaming: "Using the phone abroad: zones, passes, prices, activation, or roaming charges.",
  device_sim: "SIM cards, eSIM, lost or stolen phones, moving a number between networks, device installments, or unlocking.",
  technical_issue: "Something is not working and the caller wants help fixing it.",
  complaint: "The caller is mainly complaining about the service or demanding compensation or cancellation.",
  account_lookup: "The caller wants details from a specific account: balance, data use, ticket status, or order status.",
  human_request: "The caller is asking to speak to a person.",
  out_of_scope: "Anything unrelated to this telecom company's services, or other companies.",
  chitchat: "Greetings, thanks, goodbyes, or small talk.",
};
const SENTIMENT_CRITERIA = {
  calm: "Neutral or polite.",
  confused: "Unsure, or does not understand something.",
  annoyed: "Irritated or impatient, but controlled.",
  angry: "Openly angry, hostile, or accusing.",
};
const URGENCY_LEVELS = ["low", "medium", "high"];
const URGENCY_CRITERIA = [
  "Low: a general question with no time pressure.",
  "Medium: a current problem or a concern about money, but no immediate harm.",
  "High: needs action now, such as a stolen phone, a full loss of service, or fraud.",
];
const NEEDS_HUMAN_CRITERIA = {
  true:
    "The request must go to a human agent: the caller asks for a person; suspected fraud or number takeover; " +
    "a legal threat or a mention of a lawyer, the press, or the regulator; cancelling a line or contract; " +
    "a transfer code to move a number away; payment plans; raising the roaming limit; compensation above fifty pounds; " +
    "a third complaint about the same problem; a caller at risk; or a sales request the assistant has no information about.",
  false: "A virtual assistant can handle it by giving information, reading account data, or opening a ticket.",
};
const ANSWERABLE_CRITERIA = {
  true: "The knowledge states the fact or a specific rule needed to respond, including account data available after identity checks.",
  false:
    "The knowledge does not cover it: off-topic requests, other companies, legal, medical, or financial advice, " +
    "future plans, or a message too vague to tell what is being asked.",
};
const NOT_A_TOPIC = new Set(["human_request", "out_of_scope", "chitchat", "complaint"]);
const VOICE_NOTE =
  " Consider the words and also voice_cues in the state, which describe how the customer sounds. " +
  "A much louder, higher, or faster voice can show anger or urgency even when the words are mild.";

export function answerableQuestion() {
  return {
    type: "noul",
    instructions: "Can the customer's latest message be answered using the knowledge in the state?",
    criteria: ANSWERABLE_CRITERIA,
  };
}

export function routingQuestions(includeAnswerable, withVoice = false) {
  const note = withVoice ? VOICE_NOTE : "";
  const topics = Object.fromEntries(Object.entries(INTENT_CRITERIA).filter(([key]) => !NOT_A_TOPIC.has(key)));
  const questions = {
    intent: { type: "choice", instructions: "What is the main intent of the customer's latest message?", criteria: INTENT_CRITERIA },
    sentiment: { type: "choice", instructions: "What is the emotional tone of the customer's latest message?" + note, criteria: SENTIMENT_CRITERIA },
    urgency: { type: "score", instructions: "How urgent is the customer's latest message?" + note, criteria: URGENCY_CRITERIA },
    needs_human: { type: "noul", instructions: "Does the customer's latest message need a human agent?", criteria: NEEDS_HUMAN_CRITERIA },
    needs_account_access: {
      type: "noul",
      instructions: "Can this only be answered or done by opening the customer's own account records?",
      criteria: {
        true: "Their own balance, data use, bill amount, renewal date, or the status of their own ticket or order, or a change to their account.",
        false: "Anything general, even if it is about their problem: plans, prices, policies, how things work, coverage, known outages, or complaints.",
      },
    },
    requests_account_change: {
      type: "noul",
      instructions: "Is the customer asking to change their plan, buy a bundle, or turn on roaming right now?",
      criteria: {
        true: "They want one of those three things done now.",
        false: "They are only asking how it works, or they want something else, such as a credit or a refund.",
      },
    },
    second_topic: {
      type: "choice",
      instructions: "Does the customer's latest message also ask about a second, different topic? If so, which one?",
      criteria: { none: "There is only one topic.", ...topics },
    },
    about_another_person: {
      type: "noul",
      instructions: "In their latest message only, is the customer asking for account details that belong to someone else?",
      criteria: {
        true: "The latest message asks about another person's account, such as a spouse, parent, friend, or someone who owes them money.",
        false: "The latest message asks about their own account or a general question, even if someone else came up earlier.",
      },
    },
  };
  if (includeAnswerable) questions.answerable_from_kb = answerableQuestion();
  return questions;
}

export function parseDecision(answers) {
  const { intent, urgency } = answers;
  // Score levels come back keyed by position, so the likeliest level gives the label.
  const probabilities = urgency.probabilities || {};
  const keys = Object.keys(probabilities);
  const level = keys.length ? Number(keys.reduce((best, key) => (probabilities[key] > probabilities[best] ? key : best))) : Math.round(urgency.score);
  return {
    intent: intent.choice,
    intent_confidence: intent.confidence ?? 0,
    intent_probabilities: intent.probabilities || {},
    sentiment: answers.sentiment.choice,
    urgency: URGENCY_LEVELS[level],
    urgency_score: urgency.score,
    p_needs_human: answers.needs_human.noul,
    p_needs_account_access: answers.needs_account_access.noul,
    p_requests_account_change: answers.requests_account_change.noul,
    second_topic: answers.second_topic?.choice ?? "none",
    p_about_another_person: answers.about_another_person?.noul ?? 0,
    p_answerable: answers.answerable_from_kb?.noul ?? null,
  };
}
