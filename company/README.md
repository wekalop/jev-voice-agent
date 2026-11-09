# Nabda knowledge base

Nabda is a fictional Egyptian mobile and home-internet operator. It is the reference knowledge base for a voice customer-service agent. Nothing here describes a real company, customer, or service.

The name is two syllables: NAB-da. It means "pulse" in Arabic.

This README is for developers. Do not load it into the agent's retrieval index, because the section on deliberate gaps would give away what the knowledge base is meant not to answer.

## What each file is

- `overview.md`: history, store locations, contact channels, opening hours.
- `plans.md` and `plans.json`: the same plan data in two formats. The Markdown is for retrieval and the JSON is for tools. Every plan has a spoken summary.
- `billing_policy.md`: billing cycle, due dates, late fees, payment methods, refunds, disputes, how to read a bill.
- `coverage_and_outages.md`: coverage by city and area, the current outage list, basic troubleshooting.
- `roaming.md`: zones, passes, pay-as-you-go rates, activation, bill-shock protections.
- `devices_and_sim.md`: SIM replacement, eSIM, porting, device installments, unlocking.
- `support_policy.md`: what the agent may do, what needs a human, escalation, compensation limits, identity checks, hard rules.
- `faq.md`: short spoken-style answers.
- `customers.json`: fifteen mock customers.
- `orders_and_tickets.json`: ten mock tickets and ten mock orders.

## How the files relate

- `plans.json` is the source of truth for plan facts. Every other file refers to plans by exact name.
- `billing_policy.md` is the source of truth for fees and dates. `customers.json` applies those rules, so its overdue and suspended cases can be recomputed from the policy.
- `customers.json` links to `orders_and_tickets.json` through ticket and order IDs. Links run both ways.
- Tickets link to outages in `coverage_and_outages.md` through `OUT-` IDs.
- `support_policy.md` overrides every other file when it comes to what the agent is allowed to do.
- `faq.md` restates facts from the other files. If they ever differ, the source file wins and the FAQ is wrong.
- `../tests/validate.py` checks these links, and `../tests/test_questions.jsonl` is the evaluation set.

## Fixed reference point

The knowledge base is frozen at Tuesday, the third of November 2026, nine a.m. Cairo time. Outages, due dates, and days overdue are all calculated from that moment. Your agent should treat that as "now" during evaluation.

## How the invented details stay invented

- Phone numbers look like `0199 555 0101`. Mobile operators in Egypt use the prefixes 010, 011, 012, and 015, so 019 falls outside them.
- Landlines use `02 5550`, a block picked to be obviously fictional. The short code is `155`, which only has meaning inside this fictional network.
- Web and email addresses use `nabda.example`. The `.example` domain is reserved and can never be registered.
- Device brands (Kestrel, Orbit), the wallet (Nabda Wallet), and the one competitor mentioned in tests (Kovra Mobile) are invented.
- Cities, districts, and roaming countries are real places, because a coverage file needs them.
- National ID digits and birth dates in `customers.json` are made up.

A quick web search found no Egyptian telecom operator called Nabda, but that is not a trademark search. Check it properly if this data will ever be shown outside the project.

## Deliberate gaps

These topics are left undefined so the agent has to decline or hand off instead of inventing an answer. `validate.py` confirms that the terms stay out of the knowledge base.

- Competitors and price comparisons
- Legal advice
- Static IP addresses, and ping or latency figures
- Phone trade-in and device insurance
- Business or corporate plans
- TV packages
- Student discounts
- Future prices and network rollout dates
- Executive names and company shares
- Health questions about 5G
- Payment arrangement terms (the billing team sets these, and only the fact that they exist is documented)
- Regulator contact details

## Build plan

1. Write this README and fix the core facts: the name, prices, fees, and the reference date.
2. Write the plan files, then the policy files, so later files can quote earlier ones.
3. Write the mock customers, tickets, and orders by applying the policies to specific cases.
4. Write the FAQ and the test questions.
5. Write `validate.py`, run it, fix what it finds, and repeat until it passes.

## Running the checks

From the folder that holds `company/` and `tests/`:

```bash
python tests/validate.py
```

The test file has one field beyond the requested schema: `category`. It records which coverage bucket a question belongs to (`normal`, `lookup`, `angry_urgent`, `unanswerable`, `tricky`). A tricky case cannot be derived from the other fields, and the validator needs it to count the buckets. Strip it before showing a question to the agent.
