"""Loads the company knowledge base and picks the files for a turn."""

import json
import re
from pathlib import Path

# Which files answer each intent. support_policy.md goes in every reply prompt separately.
INTENT_FILES = {
    "billing": ["billing_policy.md"],
    "plan_info": ["plans.md", "overview.md"],
    "plan_change": ["billing_policy.md", "plans.md"],
    "coverage_outage": ["coverage_and_outages.md"],
    "roaming": ["roaming.md"],
    "device_sim": ["devices_and_sim.md"],
    "technical_issue": ["coverage_and_outages.md"],
    "complaint": ["billing_policy.md"],
    "account_lookup": ["billing_policy.md"],
    "human_request": [],
    "out_of_scope": [],
    "chitchat": [],
}
PLAN_LINES_TO_DROP = ("- ID:", "- Category:", "- Price:", "- Data:", "- Minutes:", "- Validity:", "- Network:")
ACCOUNT_NOTE = (
    "Customer account records (balance, data use, bills, tickets, and orders) "
    "are available once the caller passes identity checks."
)


class KnowledgeBase:
    def __init__(self, kb_dir: Path):
        self.files = {p.name: p.read_text(encoding="utf-8") for p in kb_dir.glob("*.md") if p.name != "README.md"}
        self.customers = json.loads((kb_dir / "customers.json").read_text(encoding="utf-8"))["customers"]
        records = json.loads((kb_dir / "orders_and_tickets.json").read_text(encoding="utf-8"))
        self.tickets = records["tickets"]
        self.orders = records["orders"]
        self.topics = self._build_topics()
        # Replies use a shorter plans file. The spoken summary already holds price, data, minutes, and validity.
        self.reply_files = dict(self.files)
        self.reply_files["plans.md"] = "\n".join(
            line for line in self.files["plans.md"].splitlines() if not line.startswith(PLAN_LINES_TO_DROP)
        )

    def _build_topics(self) -> dict:
        """A short index of what the files cover, built from their headings."""
        topics = {}
        for name, text in self.files.items():
            if name == "faq.md":
                continue
            # Plan and outage headings are detail, not topics, so only the top levels are used.
            topics[name] = re.findall(r"^#{1,2} (.+)$", text, re.M)
        return {"files": topics, "account_data": ACCOUNT_NOTE}

    def customer_by_phone(self, phone: str) -> dict | None:
        return next((c for c in self.customers if c["phone"] == phone), None)

    def customer_by_id(self, customer_id: str) -> dict | None:
        return next((c for c in self.customers if c["id"] == customer_id), None)

    def customer_view(self, customer: dict) -> dict:
        """The record the LLM may see. The identity details are removed."""
        view = {k: v for k, v in customer.items() if k != "verification"}
        view["tickets"] = [t for t in self.tickets if t["customer_id"] == customer["id"]]
        view["orders"] = [o for o in self.orders if o["customer_id"] == customer["id"]]
        return view


def select_files(
    probabilities: dict[str, float], top_intent: str, second_topic: str = "none", min_probability: float = 0.1
) -> list[str]:
    """Each topic's main file comes first, so that both topics fit when the file limit applies."""
    ranked = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
    intents = [top_intent]
    if second_topic != "none":
        intents.append(second_topic)
    else:
        intents += [i for i, p in ranked if i != top_intent and p >= min_probability][:1]
    lists = [INTENT_FILES.get(intent, []) for intent in intents]
    files: list[str] = []
    for name in [items[0] for items in lists if items] + [name for items in lists for name in items]:
        if name not in files:
            files.append(name)
    return files
