"""The whole loop on a copy of the project: run, report, accept, gate, diff, judge."""
import json
from datetime import date

from tests.fakes import RuleBasedLLM, ScriptedLLM
from voiceops import cli, gate, report, settings
from voiceops.judge import agreement, judge_run, label_run
from voiceops.scenarios import build_cases
from voiceops.simulate import load_transcripts, run_suite


def run(project_copy, cfg, suite="full", prompt="agent_v1", run_id="r1"):
    project = settings.load_project()
    cases = build_cases(suite)
    run_dir = settings.RUNS_DIR / run_id
    run_dir.mkdir(parents=True)
    meta = {"run_id": run_id, "agent_prompt": prompt, "shopper_prompt": project.shopper_prompt,
            "suite": suite, "filtered": False, "today": project.today,
            "models": {"agent": "fake", "shopper": "fake"},
            "fingerprints": gate.fingerprints(project, prompt)}
    today = date.fromisoformat(project.today)
    run_suite(cases, None, cfg, project, prompt, run_dir, meta,
              llm_for=lambda case: RuleBasedLLM(case, today), progress=lambda _m: None)
    return report.write_report(run_dir)


def test_every_scenario_runs_end_to_end(project_copy, cfg):
    summary = run(project_copy, cfg)
    assert summary["total"] == len(build_cases("full")) == 24
    statuses = {c["case_id"]: c["status"] for c in summary["cases"]}
    assert statuses["cod_outside_window@trailknot"] == "not_called"
    assert statuses["cod_wrong_number@kesar_and_clay"] == "wrong_number"
    # the crude fake gets most calls right, which also proves the expectations are reachable
    assert summary["passed"] >= 18, [c for c in summary["cases"] if not c["passed"]]
    assert (settings.RUNS_DIR / "r1" / "report.md").read_text(encoding="utf-8").startswith("# Run r1")


def test_accept_then_gate_then_detect_drift(project_copy, cfg):
    assert gate.check() == ["no accepted run yet: run the full suite, then "
                            "`python -m voiceops accept <run>`"]
    run(project_copy, cfg)
    record = gate.accept("r1")
    assert record["agent_prompt"] == "agent_v1"
    assert gate.check() == []
    assert "simulated calls passed every check" in settings.README.read_text(encoding="utf-8")

    brand = settings.BRANDS_DIR / "trailknot.yaml"
    brand.write_text(brand.read_text(encoding="utf-8").replace("cod_fee: 0", "cod_fee: 20"),
                     encoding="utf-8")
    assert gate.check() == ["brands/trailknot.yaml changed after the run"]


def test_accept_refuses_partial_or_stale_runs(project_copy, cfg):
    run(project_copy, cfg, suite="smoke", run_id="smoke1")
    try:
        gate.accept("smoke1")
        raise AssertionError("smoke run was accepted")
    except gate.GateError as exc:
        assert "partial suite" in str(exc)
    run(project_copy, cfg, run_id="full1")
    prompt = settings.PROMPTS_DIR / "agent_v1.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nBe extra polite.\n", encoding="utf-8")
    try:
        gate.accept("full1")
        raise AssertionError("stale run was accepted")
    except gate.GateError as exc:
        assert "prompts/agent_v1.md changed" in str(exc)


def test_new_prompt_version_is_promoted_only_without_regressions(project_copy, cfg):
    run(project_copy, cfg, run_id="base")
    gate.accept("base")
    (settings.PROMPTS_DIR / "agent_v2.md").write_text(
        (settings.PROMPTS_DIR / "agent_v1.md").read_text(encoding="utf-8"), encoding="utf-8")
    run(project_copy, cfg, prompt="agent_v2", run_id="v2")
    # break one call in the v2 run to stand in for a regression
    path = settings.RUNS_DIR / "v2" / "transcripts" / "cod_confirm__kesar_and_clay.json"
    t = json.loads(path.read_text(encoding="utf-8"))
    t["final"]["status"] = "cancelled"
    path.write_text(json.dumps(t), encoding="utf-8")
    try:
        gate.accept("v2")
        raise AssertionError("regression was accepted")
    except gate.GateError as exc:
        assert "cod_confirm@kesar_and_clay" in str(exc)
    record = gate.accept("v2", note="testing the override", allow_regressions=True)
    assert record["regressions_accepted"] == ["cod_confirm@kesar_and_clay"]
    assert settings.load_project().agent_prompt == "agent_v2"
    assert gate.check() == []


def test_diff_command_reports_fixes_and_breaks(project_copy, cfg, capsys):
    run(project_copy, cfg, run_id="a")
    run(project_copy, cfg, run_id="b")
    assert cli.main(["diff", "a", "b"]) == 0
    assert "Fixed 0, broke 0" in capsys.readouterr().out


def test_validate_and_show_prompt_commands(project_copy, capsys):
    assert cli.main(["validate"]) == 0
    assert "24 calls in the full suite" in capsys.readouterr().out
    assert cli.main(["show-prompt", "trailknot", "ndr"]) == 0
    assert "Saturday, 14 November 2026" in capsys.readouterr().out


def test_new_brand_scaffold_validates(project_copy, capsys):
    assert cli.main(["new-brand", "glow_co"]) == 0
    from voiceops.brand import load_brand
    assert load_brand("glow_co").brand_id == "glow_co"


def test_judge_and_agreement(project_copy, cfg):
    run(project_copy, cfg, suite="smoke", run_id="j")
    called = [t for t in load_transcripts(settings.RUNS_DIR / "j") if t["called"] and t["turns"]]
    verdict = {"pass": True, "evidence": "ok"}
    replies = [{item: verdict for item in ("language_match", "concise", "grounded",
                                           "answered_questions", "no_false_claims")}
               for _ in called]
    llm = ScriptedLLM(judge=replies)
    judge_run(settings.RUNS_DIR / "j", llm, cfg, settings.load_project(), progress=lambda _m: None)
    answers = iter(["y", "y", "n", "y", "y"] * len(called))
    label_run(settings.RUNS_DIR / "j", limit=2, input_fn=lambda _p: next(answers), print_fn=lambda _m: None)
    result = agreement(settings.RUNS_DIR / "j")
    assert result["cases"] == 2
    assert result["items"]["grounded"] == {"agree": 0, "n": 2, "judge_too_lenient": 2, "judge_too_strict": 0}


def test_run_without_a_key_stops_before_writing_results(project_copy, monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "load_dotenv", lambda path=None: None)
    assert cli.main(["run", "--suite", "smoke"]) == 2
    assert "GROQ_API_KEY is not set" in capsys.readouterr().out
    assert not any(settings.RUNS_DIR.glob("*"))


def test_latest_means_most_recently_written_not_alphabetical(project_copy, cfg):
    import os
    run(project_copy, cfg, suite="smoke", run_id="20261109-120000-agent_v1-smoke")
    run(project_copy, cfg, suite="full", run_id="20261109-120000-agent_v1-full")
    full_meta = settings.RUNS_DIR / "20261109-120000-agent_v1-full" / "meta.json"
    smoke_meta = settings.RUNS_DIR / "20261109-120000-agent_v1-smoke" / "meta.json"
    os.utime(smoke_meta, ns=(1, 1))
    assert cli.resolve_run("latest").name == "20261109-120000-agent_v1-full"
    os.utime(full_meta, ns=(1, 1))
    os.utime(smoke_meta, ns=(2, 2))
    assert cli.resolve_run("latest").name == "20261109-120000-agent_v1-smoke"
