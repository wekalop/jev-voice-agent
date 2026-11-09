// The company knowledge base, packed into the Worker by scripts/build.mjs. Ported from agent/kb.py.

import { COMPANY } from "./kb.generated.js";

const INTENT_FILES = {
  billing: ["billing_policy.md"],
  plan_info: ["plans.md", "overview.md"],
  plan_change: ["billing_policy.md", "plans.md"],
  coverage_outage: ["coverage_and_outages.md"],
  roaming: ["roaming.md"],
  device_sim: ["devices_and_sim.md"],
  technical_issue: ["coverage_and_outages.md"],
  complaint: ["billing_policy.md"],
  account_lookup: ["billing_policy.md"],
  human_request: [],
  out_of_scope: [],
  chitchat: [],
};

export const kb = {
  files: COMPANY.files,
  replyFiles: COMPANY.replyFiles,
  topics: COMPANY.topics,
  customerByPhone: phone => COMPANY.customers.find(c => c.phone === phone) || null,
  customerById: id => COMPANY.customers.find(c => c.id === id) || null,
  // The record the LLM may see. The identity details are removed.
  customerView(customer) {
    const { verification, ...view } = customer;
    return {
      ...view,
      tickets: COMPANY.tickets.filter(t => t.customer_id === customer.id),
      orders: COMPANY.orders.filter(o => o.customer_id === customer.id),
    };
  },
  // Each topic's main file comes first, so that both topics fit when the file limit applies.
  selectFiles(probabilities, topIntent, secondTopic = "none", minProbability = 0.1) {
    const intents = [topIntent];
    if (secondTopic !== "none") intents.push(secondTopic);
    else {
      const runnerUp = Object.entries(probabilities).sort((a, b) => b[1] - a[1]).find(([intent, p]) => intent !== topIntent && p >= minProbability);
      if (runnerUp) intents.push(runnerUp[0]);
    }
    const lists = intents.map(intent => INTENT_FILES[intent] || []);
    return [...new Set([...lists.filter(list => list.length).map(list => list[0]), ...lists.flat()])];
  },
};
