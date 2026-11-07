"""Turn a run's transcripts into the numbers an operations review needs."""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from voiceops import checks, settings
from voiceops.simulate import load_transcripts

START, END = "<!-- results:start -->", "<!-- results:end -->"


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def summarize(transcripts: list[dict[str, Any]]) -> dict[str, Any]:
    cases, latencies = [], []
    calls = repairs = rejected = tokens_in = tokens_out = cached = 0
    by_check: dict[str, dict[str, int]] = {name: {"applies": 0, "passed": 0} for name in checks.CHECK_NAMES}
    for t in transcripts:
        agent = checks.agent_turns(t)
        failed = [c["name"] for c in t["score"]["checks"] if c["passed"] is False]
        for c in t["score"]["checks"]:
            if c["passed"] is not None:
                by_check[c["name"]]["applies"] += 1
                by_check[c["name"]]["passed"] += int(c["passed"])
        usage = t["usage"]
        calls += sum(usage["calls"].values())
        cached += usage["cached_calls"]
        tokens_in += usage["input_tokens"]
        tokens_out += usage["output_tokens"]
        latencies += usage["agent_latency_ms"]
        records = checks.agent_records(t)
        repairs += sum(turn.get("repairs", 0) for turn in records)
        rejected += sum(len(turn.get("rejected", [])) for turn in records)
        cases.append({
            "case_id": t["case_id"], "scenario": t["scenario"], "title": t["title"],
            "brand": t["brand"], "use_case": t["use_case"], "called": t["called"],
            "status": t["final"]["status"], "expected": t["expect"]["status"],
            "passed": t["score"]["passed"], "failed_checks": failed,
            "agent_turns": len(agent), "ended_by": t["ended_by"],
            "tokens": usage["input_tokens"] + usage["output_tokens"],
        })

    brands: dict[str, Any] = {}
    for brand in sorted({c["brand"] for c in cases}):
        mine = [c for c in cases if c["brand"] == brand]
        called = [c for c in mine if c["called"]]
        brands[brand] = {
            "cases": len(mine), "passed": sum(c["passed"] for c in mine),
            "outcomes": dict(Counter(c["status"] for c in mine).most_common()),
            "avg_agent_turns": round(statistics.mean(c["agent_turns"] for c in called), 1) if called else None,
            "handoffs": sum(c["status"] == "handoff" for c in mine),
        }
    called_cases = [c for c in cases if c["called"]]
    agent_calls = sum(t["usage"]["calls"].get("agent", 0) for t in transcripts)
    return {
        "cases": cases,
        "total": len(cases),
        "passed": sum(c["passed"] for c in cases),
        "brands": brands,
        "checks": by_check,
        "ops": {
            "llm_calls": calls,
            "cached_calls": cached,
            "agent_calls": agent_calls,
            "json_repairs": repairs,
            "rejected_actions": rejected,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_per_called_case": round((tokens_in + tokens_out) / len(called_cases)) if called_cases else None,
            "agent_latency_p50_ms": _percentile(latencies, 0.5),
            "agent_latency_p95_ms": _percentile(latencies, 0.95),
            "latency_samples": len(latencies),
        },
    }


def markdown(meta: dict[str, Any], summary: dict[str, Any]) -> str:
    ops = summary["ops"]
    lines = [
        f"# Run {meta['run_id']}",
        "",
        f"Agent prompt `{meta['agent_prompt']}`, agent model `{meta['models']['agent']}`, "
        f"shopper model `{meta['models']['shopper']}`, suite `{meta['suite']}`, "
        f"simulated date {meta['today']}. All shoppers are simulated.",
        "",
        f"**{summary['passed']} of {summary['total']} calls passed every check.**",
        "",
        "## By Brand",
        "",
        "| Brand | Passed | Outcomes | Avg agent turns |",
        "|---|---|---|---|",
    ]
    for brand, b in summary["brands"].items():
        outcomes = ", ".join(f"{k} {v}" for k, v in b["outcomes"].items())
        lines.append(f"| {brand} | {b['passed']}/{b['cases']} | {outcomes} | {b['avg_agent_turns']} |")
    lines += ["", "## By Check", "", "| Check | Passed | Calls it applies to |", "|---|---|---|"]
    for name, c in summary["checks"].items():
        if c["applies"]:
            lines.append(f"| {name} | {_pct(c['passed'], c['applies'])} | {c['applies']} |")
    p50, p95 = ops["agent_latency_p50_ms"], ops["agent_latency_p95_ms"]
    latency = (f"{p50} ms median, {p95} ms p95 over {ops['latency_samples']} uncached agent calls"
               if p50 is not None else "not measured (every agent call came from the cache)")
    lines += [
        "", "## Operations", "",
        f"- LLM calls: {ops['llm_calls']} ({ops['cached_calls']} replayed from the cache)",
        f"- Agent replies needing a JSON repair: {ops['json_repairs']} of {ops['agent_calls']} agent calls",
        f"- Actions the backend rejected: {ops['rejected_actions']}",
        f"- Tokens: {ops['tokens_in']:,} in, {ops['tokens_out']:,} out "
        f"({ops['tokens_per_called_case']} per call placed)",
        f"- Agent model latency: {latency}",
        "", "## Calls", "",
        "| Call | Outcome | Expected | Result | Failed checks |", "|---|---|---|---|---|",
    ]
    for c in summary["cases"]:
        result = "pass" if c["passed"] else "**fail**"
        lines.append(f"| {c['case_id']} | {c['status']} | {' or '.join(c['expected'])} | {result} "
                     f"| {', '.join(c['failed_checks'])} |")
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path) -> dict[str, Any]:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    transcripts = load_transcripts(run_dir)
    for t in transcripts:  # re-score, so check changes apply to old runs too
        t["score"] = checks.score(t)
    summary = summarize(transcripts)
    (run_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (run_dir / "report.md").write_text(markdown(meta, summary), encoding="utf-8")
    return summary


def readme_block(run_id: str) -> str:
    run_dir = settings.RUNS_DIR / run_id
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    ops = summary["ops"]
    rows = "\n".join(f"| {name} | {_pct(c['passed'], c['applies'])} ({c['passed']}/{c['applies']}) |"
                     for name, c in summary["checks"].items() if c["applies"])
    brands = "; ".join(f"{b} {v['passed']}/{v['cases']}" for b, v in summary["brands"].items())
    p50 = ops["agent_latency_p50_ms"]
    return "\n".join([
        START,
        f"Accepted run [`{run_id}`](evals/runs/{run_id}/report.md): agent prompt "
        f"`{meta['agent_prompt']}` on `{meta['models']['agent']}`, against simulated shoppers on "
        f"`{meta['models']['shopper']}`.",
        "",
        f"- **{summary['passed']} of {summary['total']} simulated calls passed every check** ({brands}).",
        f"- {ops['json_repairs']} JSON repairs in {ops['agent_calls']} agent calls; "
        f"{ops['rejected_actions']} actions rejected by the backend.",
        f"- About {ops['tokens_per_called_case']:,} tokens per call placed"
        + (f"; agent model latency {p50} ms median." if p50 is not None else "."),
        "",
        "| Check | Passed |", "|---|---|", rows,
        END,
    ])


def publish(run_id: str) -> None:
    text = settings.README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise ValueError("README.md has no results markers")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    settings.README.write_text(head + readme_block(run_id) + tail, encoding="utf-8")


def readme_matches(run_id: str) -> bool:
    text = settings.README.read_text(encoding="utf-8").replace("\r\n", "\n")
    return readme_block(run_id) in text
