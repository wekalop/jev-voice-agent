"""Builds results/REPORT.md from the benchmark logs: python -m benchmark.report"""

import json
from collections import Counter
from pathlib import Path

from agent.config import load_settings
from benchmark.common import latency_summary, load_tests

FIELDS = ["intent", "needs_human", "answerable_from_kb", "sentiment", "urgency"]
JEV_PRICE_PER_MILLION = 0.042


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def predicted(row: dict, field: str, thresholds: dict):
    """The row's answer for a field. Jev's probabilities become yes or no at the agent's thresholds."""
    if field == "needs_human" and "p_needs_human" in row:
        return row["p_needs_human"] >= thresholds["needs_human"]
    if field == "answerable_from_kb" and "p_answerable" in row:
        return row["p_answerable"] >= thresholds["answerable"]
    return row.get(field)


def fmt(summary: dict) -> str:
    if not summary.get("n"):
        return "no data"
    return f"{summary['median']:.0f} / {summary['p95']:.0f} (n={summary['n']})"


def decision_tables(rows: list[dict], labels: dict, thresholds: dict) -> list[str]:
    sources = sorted({r["source"] for r in rows})
    lines = ["## Decisions: Jev and the LLM classifier", ""]
    lines += ["Latency excludes the first run of each source (a cold start) and any call that hit a rate limit.", ""]
    lines += ["| Source | Latency median / p95 ms | Cold start ms | Failed calls or bad output |", "|---|---|---|---|"]
    for source in sources:
        own = [r for r in rows if r["source"] == source]
        cold = [r["latency_ms"] for r in own if r.get("cold_start")]
        warm = [r["latency_ms"] for r in own if not r.get("cold_start") and not r.get("backoff_ms")]
        invalid = sum(1 for r in own if r.get("valid_output") is False)
        lines.append(f"| {source} | {fmt(latency_summary(warm))} | {cold[0] if cold else 'n/a'} | {invalid} |")
    two_call = [r["second_latency_ms"] for r in rows if r.get("second_latency_ms") and not r.get("cold_start")]
    if two_call:
        lines += ["", f"The second Jev call in two_call mode (knowledge loaded): {fmt(latency_summary(two_call))} ms."]

    tokens = [r["input_tokens"] for r in rows if r["source"] == "jev_two_call" and r.get("input_tokens")]
    if tokens:
        average = sum(tokens) / len(tokens)
        lines += ["", f"Jev cost in two_call mode: {average:.0f} input tokens a turn, about ${average * JEV_PRICE_PER_MILLION / 1e6:.6f} a turn at the listed price."]

    models = sorted({r["jev_model_reported"] for r in rows if r.get("jev_model_reported")})
    if models:
        lines += ["", "Jev model reported by the API: " + ", ".join(models) + "."]

    lines += ["", "### Accuracy per field", "", "| Source | " + " | ".join(FIELDS) + " | n |", "|---|" + "---|" * (len(FIELDS) + 1)]
    for source in sources:
        own = [r for r in rows if r["source"] == source and r["id"] in labels]
        cells = []
        for field in FIELDS:
            right = sum(1 for r in own if predicted(r, field, thresholds) == labels[r["id"]][field])
            cells.append(f"{100 * right / len(own):.0f}%" if own else "n/a")
        lines.append(f"| {source} | " + " | ".join(cells) + f" | {len(own)} |")

    # Paired counts, because 100 questions is a small set and small gaps may be noise.
    jev_rows = {r["id"]: r for r in rows if r["source"] == "jev_two_call"}
    llm_sources = [s for s in sources if s.startswith("llm_")]
    for llm_source in llm_sources:
        lines += ["", f"### Paired comparison: jev_two_call and {llm_source}", ""]
        lines += ["| Field | Only Jev right | Only LLM right | Both right | Both wrong |", "|---|---|---|---|---|"]
        llm_rows = {r["id"]: r for r in rows if r["source"] == llm_source}
        for field in FIELDS:
            counts = Counter()
            for test_id in jev_rows.keys() & llm_rows.keys():
                truth = labels[test_id][field]
                jev_ok = predicted(jev_rows[test_id], field, thresholds) == truth
                llm_ok = predicted(llm_rows[test_id], field, thresholds) == truth
                counts[(jev_ok, llm_ok)] += 1
            lines.append(f"| {field} | {counts[(True, False)]} | {counts[(False, True)]} | {counts[(True, True)]} | {counts[(False, False)]} |")

    lines += ["", "### Threshold sweep", "", "Accuracy if the yes/no threshold were set differently.", ""]
    lines += ["| Source and field | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 |", "|---|---|---|---|---|---|"]
    for source in [s for s in sources if s.startswith("jev_")]:
        own = [r for r in rows if r["source"] == source and r["id"] in labels]
        for field, key in (("needs_human", "p_needs_human"), ("answerable_from_kb", "p_answerable")):
            cells = []
            for threshold in (0.3, 0.4, 0.5, 0.6, 0.7):
                right = sum(1 for r in own if (r[key] >= threshold) == labels[r["id"]][field])
                cells.append(f"{100 * right / len(own):.0f}%" if own else "n/a")
            lines.append(f"| {source} {field} | " + " | ".join(cells) + " |")
    return lines


def failure_section(rows: list[dict], labels: dict, tests: dict, thresholds: dict) -> list[str]:
    lines = ["", "## Jev failure cases (two_call)", ""]
    misses = []
    for row in rows:
        if row["source"] != "jev_two_call":
            continue
        wrong = []
        for field in FIELDS:
            guess = predicted(row, field, thresholds)
            if guess != labels[row["id"]][field]:
                wrong.append(f"{field}: said {guess}, label {labels[row['id']][field]}")
        if wrong:
            misses.append(f"- **{row['id']}** ({row['category']}) \"{tests[row['id']]['utterance']}\" {'; '.join(wrong)}")
    return lines + (misses or ["None."])


def e2e_section(results_dir: Path) -> list[str]:
    lines = ["", "## End-to-end turns (replay)", ""]
    files = sorted(results_dir.glob("e2e_*.jsonl"))
    if not files:
        return lines + ["No end-to-end runs yet."]
    stages = [
        ("STT done", "stt_done_ms"),
        ("Jev done", "jev_done_ms"),
        ("LLM first token", "llm_first_token_ms"),
        ("First sentence ready", "first_sentence_ms"),
        ("First audio (t_total)", "t_total_excluding_waits"),
    ]
    lines += ["Median / p95 ms, measured from the end of speech. Cold starts and rate-limited turns are excluded.", ""]
    lines += ["| Run | " + " | ".join(s[0] for s in stages) + " | Errors | Rate-limited | Fell back |", "|---|" + "---|" * (len(stages) + 3)]
    judged_lines = []
    wrong_replies = []
    for path in files:
        rows = read_jsonl(path)
        ok = [r for r in rows if "error" not in r]
        warm = [r for r in ok if not r.get("cold_start") and r.get("reply_path") == "llm" and not r.get("rate_limit_wait_ms")]
        cells = [fmt(latency_summary([r.get(key) for r in warm])) for _, key in stages]
        errors = len(rows) - len(ok)
        limited = sum(1 for r in ok if r.get("rate_limit_wait_ms"))
        fell_back = sum(1 for r in ok if r.get("llm_fell_back"))
        lines.append(f"| {path.stem} | " + " | ".join(cells) + f" | {errors} | {limited} | {fell_back} |")
        judged = [r for r in ok if isinstance(r.get("judge"), dict) and r["judge"].get("pass") is not None]
        if judged:
            passed = sum(1 for r in judged if r["judge"]["pass"])
            judged_lines.append(f"- {path.stem}: {passed} of {len(judged)} passed.")
            for row in judged:
                if not row["judge"]["pass"]:
                    wrong_replies.append(f"- **{row['id']}** ({path.stem}) reply: \"{row['reply']}\" Judge: {row['judge'].get('reason')}")
        paths = Counter(r.get("reply_path") for r in ok)
        lines_note = ", ".join(f"{k}={v}" for k, v in paths.items())
        judged_lines.append(f"- {path.stem} reply paths: {lines_note}.")
    lines += ["", "The table only covers turns that reached the LLM. Fixed lines (identity prompts and declines) skip it.", ""]
    lines += ["### Final answers", "", "An LLM judge graded replies against the notes in the test file, so treat this as a guide rather than a precise score.", ""]
    lines += judged_lines or ["Not judged."]
    lines += ["", "### Failed answers", ""] + (wrong_replies or ["None."])
    return lines


def live_section(results_dir: Path) -> list[str]:
    rows = read_jsonl(results_dir / "turns_live.jsonl")
    lines = ["", "## Live voice calls", ""]
    if not rows:
        return lines + ["No live calls logged yet."]
    warm = [r for r in rows if not r.get("cold_start")]
    stages = [("STT done", "stt_done_ms"), ("Jev done", "jev_done_ms"), ("LLM first token", "llm_first_token_ms"), ("First audio (t_total)", "t_total")]
    lines += ["| Stage | Median / p95 ms |", "|---|---|"]
    for label, key in stages:
        lines.append(f"| {label} | {fmt(latency_summary([r.get(key) for r in warm]))} |")
    lines += ["", "Timing starts when the voice detector ends the turn, which is after its silence wait. Network playback to the browser is not included."]
    return lines


def main() -> None:
    settings = load_settings()
    results = settings.results_dir
    tests = {t["id"]: t for t in load_tests(settings)}
    thresholds = {"needs_human": settings.threshold_needs_human, "answerable": settings.threshold_answerable}
    rows = read_jsonl(results / "decisions.jsonl")

    out = ["# Jev voice agent: results", ""]
    network = read_jsonl(results / "network.jsonl")
    if network:
        latest = network[-1]
        setup = latest["connection_setup_ms"]
        out += [f"Run from Cairo on {latest['time']}.", ""]
        out += ["Connection setup time, median of five (ms): " + ", ".join(f"{k} {v}" for k, v in setup.items()) + ".", ""]
    out += [f"Thresholds: needs_human {thresholds['needs_human']}, answerable_from_kb {thresholds['answerable']}.", ""]
    if rows:
        out += decision_tables(rows, tests, thresholds)
        out += failure_section(rows, tests, tests, thresholds)
    else:
        out += ["No decision runs yet."]
    out += e2e_section(results)
    out += live_section(results)
    out += [
        "",
        "## Limits of these results",
        "",
        "- The labels were written by Claude Code when it generated the test set, not by independent annotators.",
        "- One location, one time of day, and free-tier services, so timings may differ elsewhere or later.",
        "- With 100 questions, a gap of a few points between two systems may be noise.",
        "- Any end-to-end or live row with fewer than 20 turns is a spot check, not a benchmark.",
    ]
    path = results / "REPORT.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
