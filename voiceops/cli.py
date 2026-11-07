"""Command line: python -m voiceops <command>. Run with --help for the list."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from voiceops import diff, gate, prompts, report, settings
from voiceops.brand import all_brands, brand_path, load_brand
from voiceops.llm import LLM, DailyLimitError, FatalLLMError
from voiceops.scenarios import build_cases, load_order, load_scenarios

COD_STATUSES = {"pending_confirmation", "confirmed", "cancelled", "prepaid_link_sent"}
NDR_STATUSES = {"delivery_failed", "reattempt_scheduled", "cancelled"}
SHARED_STATUSES = {"callback_scheduled", "handoff", "wrong_number", "opted_out",
                   "no_response", "not_called"}


def make_llm(cfg: settings.Settings, trace_path: Path | None = None) -> LLM:
    return LLM(cfg.base_url, cfg.api_key, cache_path=settings.CACHE_PATH, trace_path=trace_path,
               reasoning_effort=cfg.reasoning_effort, json_mode=cfg.json_mode, seed=cfg.seed)


def resolve_run(name: str) -> Path:
    if name == "latest":
        # By when the run last wrote its metadata, not by name: two runs can start in the
        # same second, and "full" sorts before "smoke".
        runs = [p for p in settings.RUNS_DIR.glob("*") if (p / "meta.json").exists()]
        if not runs:
            sys.exit("no runs yet")
        return max(runs, key=lambda p: ((p / "meta.json").stat().st_mtime_ns, p.name))
    path = settings.RUNS_DIR / name
    if not (path / "meta.json").exists():
        sys.exit(f"no run named {name} in evals/runs")
    return path


# --- commands -----------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    project = settings.load_project()
    brands = all_brands()
    scenarios = load_scenarios()
    cases = build_cases("full")
    smoke = build_cases("smoke")
    today = date.fromisoformat(project.today)
    problems = []
    for case in cases:
        allowed = (COD_STATUSES if case.scenario.use_case == "cod" else NDR_STATUSES) | SHARED_STATUSES
        bad = set(case.expect.status) - allowed
        if bad:
            problems.append(f"{case.case_id}: {sorted(bad)} cannot happen on a {case.scenario.use_case} call")
        if not case.scenario.script:
            prompts.compile_shopper_prompt(project.shopper_prompt, case.order, case.scenario.persona,
                                           case.scenario.goal, case.facts, case.scenario.style)
    for brand in brands.values():
        for use_case in ("cod", "ndr"):
            if not brand.enabled(use_case):
                continue
            text = prompts.compile_agent_prompt(project.agent_prompt, brand, use_case,
                                                load_order(brand, use_case), today)
            print(f"{brand.brand_id}/{use_case}: agent prompt ~{len(text) // 4} tokens")
    print(f"{len(brands)} brands, {len(scenarios)} scenarios, {len(cases)} calls in the full "
          f"suite, {len(smoke)} in the smoke suite.")
    for problem in problems:
        print("PROBLEM:", problem)
    return 1 if problems else 0


def cmd_show_prompt(args: argparse.Namespace) -> int:
    project = settings.load_project()
    brand = load_brand(args.brand)
    print(prompts.compile_agent_prompt(args.prompt or project.agent_prompt, brand, args.use_case,
                                       load_order(brand, args.use_case), date.fromisoformat(project.today)))
    return 0


def cmd_new_brand(args: argparse.Namespace) -> int:
    target = brand_path(args.brand_id)
    if target.exists():
        sys.exit(f"brands/{args.brand_id}.yaml already exists")
    text = (settings.BRANDS_DIR / "_template.yaml").read_text(encoding="utf-8")
    target.write_text(text.replace("brand_id: new_brand", f"brand_id: {args.brand_id}"), encoding="utf-8")
    print(f"Created brands/{args.brand_id}.yaml. Fill it in from the merchant intake "
          "(docs/ONBOARDING.md), add test orders for it in scenarios/orders.yaml, add it to the "
          "scenarios that apply, then run `python -m voiceops validate`.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    project = settings.load_project()
    cfg = settings.from_env()
    prompt_id = args.prompt or project.agent_prompt
    prompts.load_template(prompt_id)
    cases = build_cases(args.suite, args.brand, args.scenario)
    if not cases:
        sys.exit("no calls match those filters")
    base_id = f"{datetime.now():%Y%m%d-%H%M%S}-{prompt_id}-{args.suite}"
    run_id, n = base_id, 1
    while (settings.RUNS_DIR / run_id).exists():
        n += 1
        run_id = f"{base_id}-{n}"
    run_dir = settings.RUNS_DIR / run_id
    run_dir.mkdir(parents=True)
    meta = {
        "run_id": run_id, "agent_prompt": prompt_id, "shopper_prompt": project.shopper_prompt,
        "suite": args.suite, "filtered": bool(args.brand or args.scenario), "today": project.today,
        "models": {"agent": cfg.agent.model, "shopper": cfg.shopper.model},
        "settings": {"agent_temperature": cfg.agent.temperature,
                     "shopper_temperature": cfg.shopper.temperature,
                     "reasoning_effort": cfg.reasoning_effort, "json_mode": cfg.json_mode,
                     "seed": cfg.seed},
        "fingerprints": gate.fingerprints(project, prompt_id),
    }
    print(f"Run {run_id}: {len(cases)} calls, agent {cfg.agent.model}, shopper {cfg.shopper.model}")
    llm = make_llm(cfg, run_dir / "trace.jsonl")
    from voiceops.simulate import run_suite
    try:
        run_suite(cases, llm, cfg, project, prompt_id, run_dir, meta)
    except DailyLimitError as exc:
        print(f"\nStopped: {exc}\nThe calls that finished are saved in {run_dir.name}. Run the same "
              "command again later: everything already answered replays from the cache.")
        return 2
    except FatalLLMError as exc:
        print(f"\nStopped: {exc}")
        shutil.rmtree(run_dir, ignore_errors=True)
        return 2
    finally:
        llm.close()
    summary = report.write_report(run_dir)
    print(f"\n{summary['passed']} of {summary['total']} calls passed. "
          f"Report: evals/runs/{run_id}/report.md")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = resolve_run(args.run)
    report.write_report(run_dir)
    print((run_dir / "report.md").read_text(encoding="utf-8"))
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    accepted = gate.load_accepted()
    for run_dir in sorted(settings.RUNS_DIR.glob("*")):
        results = run_dir / "results.json"
        if not results.exists():
            print(f"{run_dir.name}  (incomplete)")
            continue
        summary = json.loads(results.read_text(encoding="utf-8"))
        mark = "  <- accepted" if accepted and accepted["run_id"] == run_dir.name else ""
        print(f"{run_dir.name}  {summary['passed']}/{summary['total']} passed{mark}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    from voiceops.simulate import load_transcripts
    before, after = resolve_run(args.before), resolve_run(args.after)
    result = diff.compare(load_transcripts(before), load_transcripts(after))
    text = diff.markdown(result, before.name, after.name)
    suites = [json.loads((r / "meta.json").read_text(encoding="utf-8"))["fingerprints"]["suite"]
              for r in (before, after)]
    if suites[0] != suites[1]:
        text = ("Note: these runs used different scenarios, shopper prompt or simulation code, so "
                "not every difference comes from the agent prompt.\n\n" + text)
    print(text)
    if args.save:
        (after / f"diff_vs_{before.name}.md").write_text(text, encoding="utf-8")
    return 0


def cmd_accept(args: argparse.Namespace) -> int:
    try:
        record = gate.accept(resolve_run(args.run).name, args.note or "", args.allow_regressions)
    except gate.GateError as exc:
        print(f"Not accepted: {exc}")
        return 1
    print(f"Accepted {record['run_id']} ({record['passed']}/{record['total']} passed). "
          f"Live prompt is now {record['agent_prompt']}; README results updated.")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    problems = gate.check()
    if problems:
        print("Release gate FAILED:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    accepted = gate.load_accepted()
    print(f"Release gate passed: live files match accepted run {accepted['run_id']}.")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    report.publish(resolve_run(args.run).name)
    print("README results updated.")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    from voiceops.chat import sample_call
    project = settings.load_project()
    cfg = settings.from_env()
    llm = make_llm(cfg, settings.SAMPLE_CALLS_DIR / "trace.jsonl")
    try:
        sample_call(args.brand, args.use_case, args.prompt or project.agent_prompt, llm, cfg, project)
    except (FatalLLMError, KeyboardInterrupt) as exc:
        print(f"\nStopped: {exc}" if str(exc) else "\nStopped.")
        return 2
    finally:
        llm.close()
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    from voiceops.judge import judge_run
    run_dir = resolve_run(args.run)
    cfg = settings.from_env()
    llm = make_llm(cfg, run_dir / "trace.jsonl")
    try:
        judge_run(run_dir, llm, cfg, settings.load_project())
    except FatalLLMError as exc:
        print(f"Stopped: {exc}")
        return 2
    finally:
        llm.close()
    print(f"Saved evals/runs/{run_dir.name}/judge.json")
    return 0


def cmd_label(args: argparse.Namespace) -> int:
    from voiceops.judge import label_run
    labels = label_run(resolve_run(args.run), args.limit)
    print(f"{len(labels)} calls labelled.")
    return 0


def cmd_agreement(args: argparse.Namespace) -> int:
    from voiceops.judge import agreement
    result = agreement(resolve_run(args.run))
    print(f"Judge vs your labels on {result['cases']} calls:")
    for item, r in result["items"].items():
        print(f"- {item}: agree {r['agree']}/{r['n']}, judge too lenient {r['judge_too_lenient']}, "
              f"too strict {r['judge_too_strict']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="python -m voiceops",
                                     description="Per-brand voice agent configs, simulated calls and a release gate.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="check brand configs, scenarios and prompts").set_defaults(fn=cmd_validate)
    p = sub.add_parser("show-prompt", help="print the compiled agent prompt for a brand and call type")
    p.add_argument("brand")
    p.add_argument("use_case", choices=["cod", "ndr"])
    p.add_argument("--prompt", help="prompt id, e.g. agent_v2 (default: the live one)")
    p.set_defaults(fn=cmd_show_prompt)
    p = sub.add_parser("new-brand", help="scaffold a brand config from the template")
    p.add_argument("brand_id")
    p.set_defaults(fn=cmd_new_brand)
    p = sub.add_parser("run", help="play simulated calls and score them")
    p.add_argument("--suite", choices=["smoke", "full"], default="smoke")
    p.add_argument("--brand", action="append", help="only this brand (repeatable)")
    p.add_argument("--scenario", action="append", help="only this scenario id (repeatable)")
    p.add_argument("--prompt", help="agent prompt id to test (default: the live one)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="rebuild and print a run's report")
    p.add_argument("run", help="run id or 'latest'")
    p.set_defaults(fn=cmd_report)
    sub.add_parser("runs", help="list runs").set_defaults(fn=cmd_runs)
    p = sub.add_parser("diff", help="what changed between two runs, call by call")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--save", action="store_true", help="also save the diff in the newer run's folder")
    p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("accept", help="make a full run the accepted baseline and promote its prompt")
    p.add_argument("run")
    p.add_argument("--note", help="why; required when accepting regressions")
    p.add_argument("--allow-regressions", action="store_true")
    p.set_defaults(fn=cmd_accept)
    sub.add_parser("gate", help="CI check: live files match the accepted run").set_defaults(fn=cmd_gate)
    p = sub.add_parser("publish", help="write a run's numbers into the README")
    p.add_argument("run")
    p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("chat", help="sample call: you type as the customer")
    p.add_argument("brand")
    p.add_argument("use_case", choices=["cod", "ndr"])
    p.add_argument("--prompt")
    p.set_defaults(fn=cmd_chat)
    p = sub.add_parser("judge", help="LLM judge for language, tone and grounding")
    p.add_argument("run")
    p.set_defaults(fn=cmd_judge)
    p = sub.add_parser("label", help="record your own verdicts to check the judge")
    p.add_argument("run")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_label)
    p = sub.add_parser("agreement", help="compare the judge with your labels")
    p.add_argument("run")
    p.set_defaults(fn=cmd_agreement)

    args = parser.parse_args(argv)
    return args.fn(args)
