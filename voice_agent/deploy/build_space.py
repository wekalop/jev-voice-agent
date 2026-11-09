"""Builds the exact folder that goes to Hugging Face: python -m deploy.build_space

Only listed paths are copied, so .env, logs, and the virtual environment can't be included by accident.
The build also fails if any value from .env appears in the output.
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT.parent
OUTPUT = ROOT / "dist" / "space"
AGENT_PATHS = ["agent", "benchmark", "live", "project_guide.html"]
COMPANY_FILES = [
    "overview.md", "plans.md", "plans.json", "billing_policy.md", "coverage_and_outages.md", "roaming.md",
    "devices_and_sim.md", "support_policy.md", "faq.md", "customers.json", "orders_and_tickets.json",
]
KEEP_ENV = {"JEV_MODEL", "JEV_BASE_URL", "JEV_MODE", "STT_PROVIDER", "STT_MODEL", "LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACK_MODEL",
            "CLASSIFIER_MODEL", "TTS_PROVIDER", "TTS_MODEL", "TTS_VOICE", "THRESHOLD_NEEDS_HUMAN", "THRESHOLD_ANSWERABLE"}

README = """---
title: Nabda Voice Test
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

A voice customer-service test for Nabda, a fictional telecom. All company and customer data is invented.
"""

DOCKERFILE = """FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH HOST=0.0.0.0 PORT=7860 RESULTS_DIR=/tmp/results PYTHONUNBUFFERED=1
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .
WORKDIR /home/user/app/voice_agent
CMD ["python", "-m", "live.server"]
"""

REQUIREMENTS = (ROOT / "requirements.txt").read_text(encoding="utf-8").replace("pytest\n", "")


def secret_values() -> list[str]:
    env = ROOT / ".env"
    if not env.exists():
        return []
    values = []
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            if name.strip() not in KEEP_ENV and len(value) >= 6:
                values.append(value)
    return values


def build() -> Path:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    agent_dir = OUTPUT / "voice_agent"
    agent_dir.mkdir(parents=True)
    for name in AGENT_PATHS:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, agent_dir / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, agent_dir / name)
    company = OUTPUT / "company"
    company.mkdir()
    for name in COMPANY_FILES:
        shutil.copy2(PROJECT / "company" / name, company / name)
    (OUTPUT / "README.md").write_text(README, encoding="utf-8")
    (OUTPUT / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (OUTPUT / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")

    secrets = secret_values()
    files = [path for path in OUTPUT.rglob("*") if path.is_file()]
    for path in files:
        data = path.read_bytes()
        if any(secret.encode() in data for secret in secrets):
            shutil.rmtree(OUTPUT)
            raise SystemExit(f"Stopped: a value from .env was found in {path.name}. Nothing was left in dist.")
    print(f"Built {len(files)} files in {OUTPUT}, with no .env values found.")
    return OUTPUT


if __name__ == "__main__":
    build()
