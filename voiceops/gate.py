"""Release gate: the live prompt and brand configs must match an accepted, evaluated run.

`accept` records a finished run as the new baseline. It refuses a run whose files differ
from the current ones, and a run that breaks calls the previous baseline passed, unless
the regressions are acknowledged. `check` runs in CI and fails when a prompt, brand config
or the test suite changed after the last accepted run.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from voiceops import diff, prompts, report, settings
from voiceops.brand import all_brands
from voiceops.settings import Project
from voiceops.simulate import load_transcripts


class GateError(RuntimeError):
    pass


def fingerprints(project: Project, agent_prompt: str | None = None) -> dict[str, Any]:
    agent_prompt = agent_prompt or project.agent_prompt
    return {
        "agent_prompt": agent_prompt,
        "agent_prompt_hash": prompts.prompt_fingerprint(agent_prompt),
        "brands": {b: prompts.brand_fingerprint(b) for b in all_brands()},
        "suite": prompts.suite_fingerprint(project.shopper_prompt, project.today),
    }


def _meta(run_id: str) -> dict[str, Any]:
    path = settings.RUNS_DIR / run_id / "meta.json"
    if not path.exists():
        raise GateError(f"no run named {run_id} in evals/runs")
    return json.loads(path.read_text(encoding="utf-8"))


def load_accepted() -> dict[str, Any] | None:
    if not settings.ACCEPTED_PATH.exists():
        return None
    return json.loads(settings.ACCEPTED_PATH.read_text(encoding="utf-8"))


def explain_mismatch(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    problems = []
    if recorded["agent_prompt"] != current["agent_prompt"]:
        problems.append(f"agent prompt is {current['agent_prompt']}, run used {recorded['agent_prompt']}")
    elif recorded["agent_prompt_hash"] != current["agent_prompt_hash"]:
        problems.append(f"prompts/{current['agent_prompt']}.md changed after the run")
    for brand in sorted(set(recorded["brands"]) | set(current["brands"])):
        if brand not in current["brands"]:
            problems.append(f"brand {brand} was removed")
        elif brand not in recorded["brands"]:
            problems.append(f"brand {brand} was added and has not been evaluated")
        elif recorded["brands"][brand] != current["brands"][brand]:
            problems.append(f"brands/{brand}.yaml changed after the run")
    if recorded["suite"] != current["suite"]:
        problems.append("scenarios, test orders, the shopper prompt, the simulated date or the "
                        "simulation code changed")
    return problems


def accept(run_id: str, note: str = "", allow_regressions: bool = False) -> dict[str, Any]:
    project = settings.load_project()
    meta = _meta(run_id)
    if not meta.get("complete"):
        raise GateError(f"{run_id} did not finish; rerun it before accepting")
    if meta.get("suite") != "full" or meta.get("filtered"):
        raise GateError(f"{run_id} ran a partial suite; accept only a full, unfiltered run")
    current = fingerprints(project, meta["agent_prompt"])
    problems = explain_mismatch(meta["fingerprints"], current)
    if problems:
        raise GateError("the run does not match the current files: " + "; ".join(problems))

    summary = report.write_report(settings.RUNS_DIR / run_id)
    previous = load_accepted()
    regressions: list[str] = []
    if previous and previous["run_id"] != run_id:
        compared = diff.compare(load_transcripts(settings.RUNS_DIR / previous["run_id"]),
                                load_transcripts(settings.RUNS_DIR / run_id))
        regressions = [r["case_id"] for r in compared["rows"] if r["kind"] == "broken"]
        if regressions and not allow_regressions:
            raise GateError(f"{len(regressions)} calls passed in {previous['run_id']} and fail now: "
                            f"{', '.join(regressions)}. Fix them, or rerun accept with "
                            "--allow-regressions and a --note saying why.")
        if regressions and not note:
            raise GateError("accepting regressions needs a --note explaining why")

    record = {
        "run_id": run_id,
        "agent_prompt": meta["agent_prompt"],
        "fingerprints": current,
        "passed": summary["passed"],
        "total": summary["total"],
        "accepted_at": datetime.now().isoformat(timespec="seconds"),
        "previous_run_id": previous["run_id"] if previous else None,
        "regressions_accepted": regressions,
        "note": note,
    }
    settings.ACCEPTED_PATH.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    settings.set_agent_prompt(meta["agent_prompt"])
    report.publish(run_id)
    return record


def check() -> list[str]:
    """Return what is wrong; an empty list means the gate passes."""
    accepted = load_accepted()
    if accepted is None:
        return ["no accepted run yet: run the full suite, then `python -m voiceops accept <run>`"]
    run_dir = settings.RUNS_DIR / accepted["run_id"]
    if not (run_dir / "results.json").exists():
        return [f"accepted run {accepted['run_id']} is missing from evals/runs; commit it"]
    project = settings.load_project()
    problems = explain_mismatch(accepted["fingerprints"], fingerprints(project))
    if not report.readme_matches(accepted["run_id"]):
        problems.append("README results do not match the accepted run; run "
                        f"`python -m voiceops publish {accepted['run_id']}`")
    return problems
