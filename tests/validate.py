#!/usr/bin/env python3
"""Consistency checks for the Nabda knowledge base and its test questions.

Usage: python tests/validate.py
Exits with status 0 if everything passes, and 1 otherwise.
"""

import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANY = ROOT / "company"
TESTS = ROOT / "tests"

AS_OF = date(2026, 11, 3)

REQUIRED_FILES = [
    "README.md", "overview.md", "plans.md", "plans.json", "billing_policy.md",
    "coverage_and_outages.md", "roaming.md", "devices_and_sim.md",
    "support_policy.md", "faq.md", "customers.json", "orders_and_tickets.json",
]
JSON_FILES = ["plans.json", "customers.json", "orders_and_tickets.json"]
ALLOWED_INTENTS = {
    "billing", "plan_info", "plan_change", "coverage_outage", "roaming", "device_sim",
    "technical_issue", "complaint", "account_lookup", "human_request", "out_of_scope", "chitchat",
}
ALLOWED_SENTIMENT = {"calm", "confused", "annoyed", "angry"}
ALLOWED_URGENCY = {"low", "medium", "high"}
TEST_FIELDS = {
    "id": str, "utterance": str, "intent": str, "answerable_from_kb": bool, "needs_human": bool,
    "sentiment": str, "urgency": str, "expected_answer_notes": str, "category": str,
}
# Requested share of each bucket, with a tolerance in percentage points.
BUCKET_TARGETS = {"normal": 40, "lookup": 20, "angry_urgent": 15, "unanswerable": 15, "tricky": 10}
BUCKET_TOLERANCE = 3
MIN_TESTS = 80

# Real names that must not appear anywhere in the knowledge base or test set.
BRANDS_ANY_CASE = [
    "vodafone", "etisalat", "telecom egypt", "ntra", "whatsapp", "facebook", "instagram",
    "tiktok", "youtube", "netflix", "snapchat", "telegram", "iphone", "samsung", "huawei",
    "xiaomi", "oppo", "nokia", "mastercard", "meeza", "fawry", "instapay", "paypal", "google",
    "zentel",
]
BRANDS_EXACT_CASE = ["Orange", "Apple", "Visa", "WE Telecom", "Amazon", "Microsoft"]

# Topics left undefined on purpose. The README lists them, so it is not scanned.
GAP_PATTERNS = [
    r"static ip", r"trade[- ]in", r"insurance", r"\bceo\b", r"\btv\b", r"television",
    r"business plan", r"corporate", r"student discount", r"latency", r"\bping\b",
    r"shareholder", r"stock price",
]

# Spoken facts that must be stated the same way in each file named.
KEY_FACTS = [
    ("late fee of thirty pounds", ["billing_policy.md"]),
    ("late fee is thirty pounds", ["faq.md"]),
    ("reconnection fee of fifty pounds", ["billing_policy.md"]),
    ("fifty pound reconnection fee", ["faq.md"]),
    ("five pound", ["billing_policy.md", "faq.md"]),
    ("fourteen days after your bill date", ["faq.md"]),
    ("fourteen days after the bill date", ["billing_policy.md"]),
    ("within sixty days of the bill date", ["billing_policy.md", "faq.md"]),
    ("one hundred twenty pounds", ["roaming.md", "faq.md"]),
    ("one hundred ninety pounds", ["roaming.md", "faq.md"]),
    ("two hundred eighty pounds", ["roaming.md", "faq.md"]),
    ("one thousand pounds per billing cycle", ["roaming.md", "faq.md"]),
    ("fifty pounds", ["devices_and_sim.md", "faq.md"]),
    ("thirty pounds", ["devices_and_sim.md", "faq.md"]),
    ("eight a.m. to midnight", ["overview.md", "support_policy.md", "faq.md"]),
    ("five hundred pounds once", ["plans.md", "faq.md"]),
    ("up to fifty pounds of goodwill credit", ["support_policy.md"]),
]

errors = []


def fail(group, message):
    errors.append(f"[{group}] {message}")


def read(name):
    return (COMPANY / name).read_text(encoding="utf-8")


def number_words(n):
    ones = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
            "fourteen fifteen sixteen seventeen eighteen nineteen").split()
    tens = "x x twenty thirty forty fifty sixty seventy eighty ninety".split()
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10] + ("-" + ones[n % 10] if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return ones[n // 100] + " hundred" + (" " + number_words(rest) if rest else "")
    rest = n % 1000
    return number_words(n // 1000) + " thousand" + (" " + number_words(rest) if rest else "")


def to_date(value):
    return date.fromisoformat(value)


def check_files():
    for name in REQUIRED_FILES:
        if not (COMPANY / name).is_file():
            fail("files", f"missing company/{name}")
    for path in [TESTS / "test_questions.jsonl", TESTS / "validate.py"]:
        if not path.is_file():
            fail("files", f"missing {path.relative_to(ROOT)}")


def load_json():
    loaded = {}
    for name in JSON_FILES:
        try:
            loaded[name] = json.loads(read(name))
        except (OSError, json.JSONDecodeError) as exc:
            fail("json", f"{name} does not parse: {exc}")
    return loaded


def load_tests():
    tests = []
    path = TESTS / "test_questions.jsonl"
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            tests.append(json.loads(line))
        except json.JSONDecodeError as exc:
            fail("json", f"test_questions.jsonl line {number} does not parse: {exc}")
    return tests


def parse_plans_md():
    """Return {plan name: fields} from plans.md."""
    parsed = {}
    current = None
    in_fine_print = False
    for line in read("plans.md").splitlines():
        if line.startswith("### "):
            current = {"fine_print": []}
            parsed[line[4:].strip()] = current
            in_fine_print = False
        elif line.startswith("## "):
            current = None
        elif current is not None:
            if line.startswith("  - ") and in_fine_print:
                current["fine_print"].append(line[4:].strip())
            elif line.startswith("- Fine print:"):
                in_fine_print = True
            elif line.startswith("- ") and ": " in line:
                key, value = line[2:].split(": ", 1)
                current[key] = value.strip()
    return parsed


def check_plans(plans):
    names = [p["name"] for p in plans]
    for name, count in Counter(names).items():
        if count > 1:
            fail("plans", f"duplicate plan name {name}")

    counts = Counter(p["category"] for p in plans)
    for category, minimum in [("prepaid", 4), ("postpaid", 3), ("home_internet", 3), ("bundle", 6)]:
        if counts[category] < minimum:
            fail("plans", f"need at least {minimum} {category} plans, found {counts[category]}")

    md = parse_plans_md()
    if set(md) != set(names):
        fail("plans", f"plan names differ between md and json: {sorted(set(md) ^ set(names))}")

    for plan in plans:
        name = plan["name"]
        spoken = number_words(plan["price_egp"]) + " pounds"
        if plan["price_spoken"] != spoken:
            fail("plans", f"{name}: price_spoken is '{plan['price_spoken']}', expected '{spoken}'")
        if plan["price_spoken"] not in plan["spoken_summary"]:
            fail("plans", f"{name}: spoken_summary does not state the price")
        if not plan["spoken_summary"].startswith(name):
            fail("plans", f"{name}: spoken_summary does not start with the plan name")
        if re.search(r"\d", plan["spoken_summary"] + " ".join(plan["fine_print"])):
            fail("plans", f"{name}: spoken text contains digits")
        if plan["data_gb"]:
            unit = "gigabyte" if plan["data_gb"] == 1 else "gigabytes"
            if f"{number_words(plan['data_gb'])} {unit}" not in plan["spoken_summary"]:
                fail("plans", f"{name}: spoken_summary does not match data_gb")
        if plan["minutes"] and f"{number_words(plan['minutes'])} minutes" not in plan["spoken_summary"]:
            fail("plans", f"{name}: spoken_summary does not match minutes")

        fields = md.get(name)
        if not fields:
            continue
        speed = "same as main plan" if plan["speed_mbps"] is None else f"up to {plan['speed_mbps']} Mbps"
        validity = f"{plan['validity_days']} day" + ("" if plan["validity_days"] == 1 else "s")
        expected = {
            "ID": plan["id"],
            "Category": plan["category"],
            "Price": f"{plan['price_egp']} EGP",
            "Data": f"{plan['data_gb']} GB",
            "Minutes": str(plan["minutes"]),
            "Validity": validity,
            "Speed": speed,
            "Network": plan["network"],
            "Rollover": f"{plan['rollover_gb']} GB",
            "Say": plan["spoken_summary"],
        }
        for key, value in expected.items():
            if fields.get(key) != value:
                fail("plans", f"{name}: {key} is '{fields.get(key)}' in md, '{value}' in json")
        if fields["fine_print"] != plan["fine_print"]:
            fail("plans", f"{name}: fine print differs between md and json")


def check_plan_references(plan_names, tests):
    """Every capitalized plan-like name must be a real plan."""
    pattern = re.compile(r"\b(?:Go|Flex|Boost) [A-Z][a-z]+|\bHome (?:Fiber|Air) [A-Z][a-z]+(?: Hundred)?")
    sources = {p.name: p.read_text(encoding="utf-8") for p in COMPANY.iterdir() if p.is_file()}
    sources["test_questions.jsonl"] = "\n".join(
        t.get("utterance", "") + " " + t.get("expected_answer_notes", "") for t in tests)
    for filename, text in sources.items():
        for match in set(pattern.findall(text)):
            if match not in plan_names:
                fail("references", f"{filename} mentions unknown plan '{match}'")


def check_customers(customers, plans_by_name):
    if len(customers) != 15:
        fail("customers", f"expected 15 customers, found {len(customers)}")
    for field in ("id", "phone"):
        repeats = [v for v, c in Counter(c[field] for c in customers).items() if c > 1]
        if repeats:
            fail("customers", f"duplicate {field}: {repeats}")

    required = {"id", "name", "phone", "plan", "balance_egp", "bill_status", "data_usage", "open_tickets"}
    for customer in customers:
        cid = customer.get("id", "?")
        missing = required - set(customer)
        if missing:
            fail("customers", f"{cid} is missing {sorted(missing)}")
            continue
        plan = plans_by_name.get(customer["plan"])
        if not plan:
            fail("customers", f"{cid} has unknown plan {customer['plan']}")
            continue
        if plan["category"] == "bundle":
            fail("customers", f"{cid} has a bundle as its main plan")

        usage = customer["data_usage"]
        bundles = customer["active_bundles"]
        bundle_gb = 0
        for bundle in bundles:
            if bundle["name"] not in plans_by_name:
                fail("customers", f"{cid} has unknown bundle {bundle['name']}")
            else:
                bundle_gb += plans_by_name[bundle["name"]]["data_gb"]
        total = plan["data_gb"] + usage["rollover_gb"] + bundle_gb
        if usage["allowance_gb"] != total:
            fail("customers", f"{cid} allowance is {usage['allowance_gb']}, expected {total}")
        if usage["rollover_gb"] > plan["rollover_gb"]:
            fail("customers", f"{cid} has more rollover than {plan['name']} allows")
        if usage["used_gb"] > usage["allowance_gb"]:
            fail("customers", f"{cid} used more data than the allowance")

        if plan["category"] == "prepaid":
            check_prepaid(customer, cid)
        else:
            check_postpaid(customer, cid, plan)

    if not any(c["bill_status"] == "overdue" and c["line_status"] == "active" for c in customers):
        fail("customers", "no customer has an overdue bill on an active line")
    if not any(c["line_status"] == "suspended" for c in customers):
        fail("customers", "no customer has a suspended line")


def check_prepaid(customer, cid):
    if customer["bill"] is not None or customer["bill_status"] != "no_bill_prepaid":
        fail("customers", f"{cid} is prepaid but has a bill")
    if not customer["renewal_date"]:
        fail("customers", f"{cid} is prepaid but has no renewal date")


def check_postpaid(customer, cid, plan):
    bill = customer["bill"]
    if not bill:
        fail("customers", f"{cid} is billed but has no bill")
        return
    bill_date, due = to_date(bill["bill_date"]), to_date(bill["due_date"])
    if due != bill_date + timedelta(days=14):
        fail("customers", f"{cid} due date is not fourteen days after the bill date")
    if plan["category"] == "home_internet" and bill_date.day != 1:
        fail("customers", f"{cid} home bill is not issued on the first")
    if bill_date.day not in (1, 10, 20):
        fail("customers", f"{cid} bill date is not on the first, tenth, or twentieth")

    charges = bill["charges"]
    total = sum(c["amount_egp"] for c in charges)
    if total - bill["payments_egp"] != bill["amount_due_egp"]:
        fail("customers", f"{cid} charges minus payments do not equal the amount due")
    if bill["amount_due_egp"] != customer["balance_egp"]:
        fail("customers", f"{cid} balance does not equal the amount due")
    plan_fees = [c for c in charges if c["label"].startswith("Plan fee")]
    if not plan_fees or any(c["amount_egp"] != plan["price_egp"] for c in plan_fees):
        fail("customers", f"{cid} plan fee on the bill does not match {plan['name']}")

    status = customer["bill_status"]
    late_fees = [c for c in charges if c["label"].startswith("Late fee")]
    if any(c["amount_egp"] != 30 for c in late_fees):
        fail("customers", f"{cid} late fee is not thirty pounds")
    if status == "paid" and bill["amount_due_egp"] != 0:
        fail("customers", f"{cid} is marked paid but owes money")
    if status == "unpaid_not_due" and due < AS_OF:
        fail("customers", f"{cid} is marked not due, but the due date has passed")
    if status == "overdue":
        oldest = to_date(bill["oldest_unpaid_due_date"])
        days = (AS_OF - oldest).days
        if days < 4 or len(late_fees) != len(plan_fees):
            fail("customers", f"{cid} overdue state does not match late fee rules")
        if (days >= 30) != (customer["line_status"] == "suspended"):
            fail("customers", f"{cid} is {days} days past due, line is {customer['line_status']}")
        if days < 15 and customer["line_status"] != "active":
            fail("customers", f"{cid} should still be active at {days} days past due")
    elif customer["line_status"] != "active":
        fail("customers", f"{cid} line is {customer['line_status']}, but the bill is {status}")

    installment = customer["device_installment"]
    charged = [c for c in charges if c["label"].startswith("Device installment")]
    if installment and (not charged or charged[0]["amount_egp"] != installment["monthly_egp"]):
        fail("customers", f"{cid} installment on the bill does not match its record")


def check_tickets_and_orders(data, customers):
    tickets, orders = data["tickets"], data["orders"]
    if len(tickets) != 10:
        fail("tickets", f"expected 10 tickets, found {len(tickets)}")
    if len(orders) != 10:
        fail("tickets", f"expected 10 orders, found {len(orders)}")
    by_id = {c["id"]: c for c in customers}
    outage_kb = read("coverage_and_outages.md")

    groups = [
        (tickets, "open_tickets", set(data["open_ticket_statuses"]), set(data["closed_ticket_statuses"])),
        (orders, "open_orders", set(data["open_order_statuses"]), set(data["closed_order_statuses"])),
    ]
    for records, field, open_statuses, closed_statuses in groups:
        for record in records:
            if record["customer_id"] not in by_id:
                fail("references", f"{record['id']} has unknown customer {record['customer_id']}")
            if record["status"] not in open_statuses | closed_statuses:
                fail("tickets", f"{record['id']} has unknown status {record['status']}")
        expected = {}
        for record in records:
            if record["status"] in open_statuses:
                expected.setdefault(record["customer_id"], set()).add(record["id"])
        known = {r["id"] for r in records}
        for customer in customers:
            listed = set(customer[field])
            for item in listed - known:
                fail("references", f"{customer['id']} lists unknown {item}")
            if listed != expected.get(customer["id"], set()):
                fail("references", f"{customer['id']} {field} {sorted(listed)} do not match "
                                   f"{sorted(expected.get(customer['id'], set()))}")

    for ticket in tickets:
        outage = ticket.get("related_outage")
        if outage and f"### {outage}" not in outage_kb:
            fail("references", f"{ticket['id']} links to unknown outage {outage}")


def check_id_references(customers, data, tests):
    """Every ID written anywhere must exist."""
    known = {c["id"] for c in customers}
    known |= {t["id"] for t in data["tickets"]} | {o["id"] for o in data["orders"]}
    known |= set(re.findall(r"^### (OUT-\d+)", read("coverage_and_outages.md"), re.M))
    sources = {p.name: p.read_text(encoding="utf-8") for p in COMPANY.iterdir() if p.is_file()}
    sources["test_questions.jsonl"] = (TESTS / "test_questions.jsonl").read_text(encoding="utf-8")
    for filename, text in sources.items():
        for found in set(re.findall(r"\b(?:CUST|TCK|ORD|OUT)-\d+", text)):
            if found not in known:
                fail("references", f"{filename} mentions unknown ID {found}")


def check_outages():
    text = read("coverage_and_outages.md")
    sections = re.findall(r"^### (OUT-\d+)\n(.*?)(?=^#|\Z)", text, re.M | re.S)
    if len(sections) < 3:
        fail("outages", "expected at least three outages")
    current = text.split("## Current outages")[1].split("## Recently fixed")[0]
    for outage_id, body in re.findall(r"^### (OUT-\d+)\n(.*?)(?=^#|\Z)", current, re.M | re.S):
        for label in ("Service:", "Area:", "Started:", "Expected fix:", "Say:"):
            if label not in body:
                fail("outages", f"{outage_id} has no {label}")
    if "Expected fix: not known" not in current:
        fail("outages", "no outage has an unknown fix time, which is a case the agent must handle")


def check_stores():
    text = read("overview.md").split("## Store locations")[1]
    total = 0
    cities = re.findall(r"^### (.+?): (\d+) stores?\n((?:- .+\n?)+)", text, re.M)
    for city, count, block in cities:
        listed = len(re.findall(r"^- ", block, re.M))
        if listed != int(count):
            fail("overview", f"{city} says {count} stores but lists {listed}")
        total += int(count)
    if total != 40:
        fail("overview", f"store total is {total}, expected 40")


def parse_faq():
    entries = re.findall(r"^## (.+?)\n(.+?)(?=^## |\Z)", read("faq.md"), re.M | re.S)
    return [(q.strip(), a.strip()) for q, a in entries]


def check_faq(plans_by_name):
    entries = parse_faq()
    if len(entries) < 40:
        fail("faq", f"expected at least 40 questions, found {len(entries)}")
    questions = Counter(q for q, _ in entries)
    for question, count in questions.items():
        if count > 1:
            fail("faq", f"duplicate question: {question}")
    pattern = re.compile("|".join(re.escape(n) for n in sorted(plans_by_name, key=len, reverse=True)))
    for question, answer in entries:
        if re.search(r"\d", answer):
            fail("faq", f"digits in answer to: {question}")
        if re.search(r"[*_#`|\[\]]", answer) or "\n" in answer:
            fail("faq", f"markdown or line break in answer to: {question}")
        sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", answer) if s]
        if not 1 <= len(sentences) <= 3:
            fail("faq", f"{len(sentences)} sentences in answer to: {question}")
        mentioned = set(pattern.findall(answer))
        if len(mentioned) == 1 and "pounds" in answer:
            plan = plans_by_name[mentioned.pop()]
            if plan["price_spoken"] not in answer:
                fail("faq", f"answer gives a price, but not {plan['name']}'s price: {question}")


def check_key_facts():
    for phrase, files in KEY_FACTS:
        for name in files:
            if phrase not in read(name):
                fail("facts", f"{name} does not contain '{phrase}'")


def check_style():
    for path in COMPANY.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*\|.*\|\s*$", text, re.M):
            fail("style", f"{path.name} has a table")


def scan_targets():
    targets = [p for p in COMPANY.iterdir() if p.is_file()]
    targets.append(TESTS / "test_questions.jsonl")
    return targets


def check_invented_only():
    any_case = re.compile(r"\b(?:" + "|".join(re.escape(b) for b in BRANDS_ANY_CASE) + r")\b", re.I)
    exact = re.compile(r"\b(?:" + "|".join(re.escape(b) for b in BRANDS_EXACT_CASE) + r")\b")
    for path in scan_targets():
        text = path.read_text(encoding="utf-8")
        found = set(m.lower() for m in any_case.findall(text)) | set(exact.findall(text))
        if found:
            fail("invented", f"{path.name} mentions real names: {sorted(found)}")

        # Domains must use the reserved .example name.
        for domain in re.findall(r"\b[\w-]+\.(?:com|net|org|eg|io)\b", text):
            fail("invented", f"{path.name} has a real-looking domain: {domain}")

        # Any long run of digits must be one of the fictional number blocks.
        clean = re.sub(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?", " ", text)
        for raw in re.findall(r"\+?\d[\d \-]{6,}\d", clean):
            digits = re.sub(r"\D", "", raw)
            allowed = (
                re.fullmatch(r"0199555\d{4}", digits)
                or re.fullmatch(r"(?:0|20)25550\d{4}", digits)
            )
            if not allowed:
                fail("invented", f"{path.name} has a number outside the fictional blocks: {raw}")


def check_gaps():
    for path in COMPANY.iterdir():
        if not path.is_file() or path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in GAP_PATTERNS:
            if re.search(pattern, text, re.I):
                fail("gaps", f"{path.name} covers a deliberate gap: {pattern}")


def check_tests(tests, customers):
    if len(tests) < MIN_TESTS:
        fail("tests", f"expected at least {MIN_TESTS} questions, found {len(tests)}")
    for test_id, count in Counter(t.get("id") for t in tests).items():
        if count > 1:
            fail("tests", f"duplicate id {test_id}")
    for utterance, count in Counter(t.get("utterance") for t in tests).items():
        if count > 1:
            fail("tests", f"duplicate utterance: {utterance}")

    kb_files = {p.name for p in COMPANY.iterdir()}
    by_id = {c["id"]: c for c in customers}
    for test in tests:
        tid = test.get("id", "?")
        for field, kind in TEST_FIELDS.items():
            if not isinstance(test.get(field), kind):
                fail("tests", f"{tid} has a missing or wrongly typed field: {field}")
        if test.get("intent") not in ALLOWED_INTENTS:
            fail("tests", f"{tid} has a bad intent: {test.get('intent')}")
        if test.get("sentiment") not in ALLOWED_SENTIMENT:
            fail("tests", f"{tid} has a bad sentiment: {test.get('sentiment')}")
        if test.get("urgency") not in ALLOWED_URGENCY:
            fail("tests", f"{tid} has a bad urgency: {test.get('urgency')}")
        category = test.get("category")
        if category not in BUCKET_TARGETS:
            fail("tests", f"{tid} has a bad category: {category}")
            continue

        notes = test.get("expected_answer_notes", "")
        for filename in re.findall(r"\b\w+\.(?:md|json)\b", notes):
            if filename not in kb_files:
                fail("tests", f"{tid} cites a missing file: {filename}")
        if test.get("answerable_from_kb") and not re.search(r"\b\w+\.(?:md|json)\b", notes):
            fail("tests", f"{tid} is answerable, but its notes cite no file")

        if category == "lookup":
            cited = re.findall(r"CUST-\d+", notes)
            if "customers.json" not in notes or not cited:
                fail("tests", f"{tid} is a lookup, but its notes do not cite a customer")
            elif by_id[cited[0]]["name"] not in test["utterance"]:
                fail("tests", f"{tid} utterance does not name customer {cited[0]}")
            if not test.get("answerable_from_kb"):
                fail("tests", f"{tid} is a lookup, so it must be answerable")
        if category == "unanswerable" and test.get("answerable_from_kb"):
            fail("tests", f"{tid} is in the unanswerable bucket, but marked answerable")
        if category == "angry_urgent":
            if test.get("sentiment") not in {"angry", "annoyed"} and test.get("urgency") != "high":
                fail("tests", f"{tid} is in the angry bucket, but is neither angry nor urgent")

    total = len(tests) or 1
    buckets = Counter(t.get("category") for t in tests)
    for bucket, target in BUCKET_TARGETS.items():
        share = 100 * buckets[bucket] / total
        if abs(share - target) > BUCKET_TOLERANCE:
            fail("tests", f"{bucket} is {share:.0f} percent, target is {target} percent")
    unused = ALLOWED_INTENTS - {t.get("intent") for t in tests}
    if unused:
        fail("tests", f"intents with no question: {sorted(unused)}")
    return buckets


def main():
    check_files()
    data = load_json()
    tests = load_tests()
    if errors or len(data) != len(JSON_FILES):
        report(None, tests)
        return 1

    plans = data["plans.json"]["plans"]
    plans_by_name = {p["name"]: p for p in plans}
    customers = data["customers.json"]["customers"]

    check_plans(plans)
    check_plan_references(set(plans_by_name), tests)
    check_customers(customers, plans_by_name)
    check_tickets_and_orders(data["orders_and_tickets.json"], customers)
    check_id_references(customers, data["orders_and_tickets.json"], tests)
    check_outages()
    check_stores()
    check_faq(plans_by_name)
    check_key_facts()
    check_style()
    check_invented_only()
    check_gaps()
    buckets = check_tests(tests, customers)

    summary = {
        "plans": len(plans),
        "customers": len(customers),
        "tickets": len(data["orders_and_tickets.json"]["tickets"]),
        "orders": len(data["orders_and_tickets.json"]["orders"]),
        "faq": len(parse_faq()),
    }
    report((summary, buckets), tests)
    return 1 if errors else 0


def report(details, tests):
    if details:
        summary, buckets = details
        print("Counts: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
        print(f"Tests: {len(tests)} questions, " + ", ".join(f"{k}={v}" for k, v in sorted(buckets.items())))
    if errors:
        print(f"\nFAILED with {len(errors)} problem(s):")
        for error in errors:
            print("  " + error)
    else:
        print("\nPASSED: all checks are clean.")


if __name__ == "__main__":
    sys.exit(main())
