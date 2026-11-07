"""LLM judge for what rules cannot check, plus hand labels to test the judge itself.

The judge is only worth trusting where it agrees with a person. `label` records your own
verdicts on a sample of calls, and `agreement` compares the two item by item.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from voiceops import prompts
from voiceops.agent import ask
from voiceops.brand import load_brand
from voiceops.llm import LLM
from voiceops.scenarios import load_order, load_scenarios
from voiceops.settings import Project, Settings
from voiceops.simulate import load_transcripts

ITEMS = ["language_match", "concise", "grounded", "answered_questions", "no_false_claims"]


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    passed: bool = Field(alias="pass")
    evidence: str = ""


class JudgeReply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    language_match: Verdict
    concise: Verdict
    grounded: Verdict
    answered_questions: Verdict
    no_false_claims: Verdict


def transcript_text(t: dict[str, Any]) -> str:
    return "\n".join(f"{'Agent' if turn['speaker'] == 'agent' else 'Customer'}: "
                     f"{turn['text'] or '(silence)'}" for turn in t["turns"]
                     if not turn.get("wrap_up"))


def action_log(t: dict[str, Any]) -> str:
    lines = [f"- turn {a['turn']}: {a['action']} {'succeeded' if a['ok'] else 'FAILED'} ({a['message']})"
             for a in t["actions"]]
    return "\n".join(lines) or "- no actions"


def judge_run(run_dir: Path, llm: LLM, cfg: Settings, project: Project,
              progress: Callable[[str], None] = print) -> dict[str, Any]:
    template = prompts.load_template(project.judge_prompt)
    scenarios = {s.id: s for s in load_scenarios()}
    results: dict[str, Any] = {}
    for t in load_transcripts(run_dir):
        if not t["called"] or not t["turns"]:
            continue
        brand = load_brand(t["brand"])
        order = load_order(brand, t["use_case"], scenarios[t["scenario"]].order_overrides)
        policy = prompts.policy_summary(brand, t["use_case"], order, date.fromisoformat(t["today"]))
        content = prompts.render(template, {"policy": policy, "action_log": action_log(t),
                                            "transcript": transcript_text(t)})
        asked = ask(llm, "judge", project.judge_prompt, cfg.judge,
                    [{"role": "user", "content": content}], JudgeReply, f"{t['case_id']}:judge")
        reply: JudgeReply = asked.parsed
        results[t["case_id"]] = {item: {"pass": getattr(reply, item).passed,
                                        "evidence": getattr(reply, item).evidence} for item in ITEMS}
        failed = [i for i in ITEMS if not results[t["case_id"]][i]["pass"]]
        progress(f"judged {t['case_id']}: {'all pass' if not failed else 'fails ' + ', '.join(failed)}")
    out = {"judge_prompt": project.judge_prompt, "model": cfg.judge.model, "results": results}
    (run_dir / "judge.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    return out


def label_run(run_dir: Path, limit: int, input_fn: Callable[[str], str] = input,
              print_fn: Callable[[str], None] = print) -> dict[str, Any]:
    path = run_dir / "labels.json"
    labels: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = [t for t in load_transcripts(run_dir) if t["called"] and t["turns"]
            and t["case_id"] not in labels][:limit]
    for t in todo:
        print_fn(f"\n=== {t['case_id']} ({t['title']}) ===\n{transcript_text(t)}\n"
                 f"Actions:\n{action_log(t)}\n")
        verdicts = {}
        for item in ITEMS:
            answer = ""
            while answer not in ("y", "n"):
                answer = input_fn(f"{item}? [y/n] ").strip().lower()[:1]
            verdicts[item] = answer == "y"
        labels[t["case_id"]] = verdicts
        path.write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8")
    return labels


def agreement(run_dir: Path) -> dict[str, Any]:
    judged = json.loads((run_dir / "judge.json").read_text(encoding="utf-8"))["results"]
    labels = json.loads((run_dir / "labels.json").read_text(encoding="utf-8"))
    shared = sorted(set(judged) & set(labels))
    per_item = {}
    for item in ITEMS:
        pairs = [(judged[c][item]["pass"], labels[c][item]) for c in shared]
        agree = sum(j == h for j, h in pairs)
        per_item[item] = {
            "agree": agree, "n": len(pairs),
            # the judge passed something you failed: the costly kind of mistake
            "judge_too_lenient": sum(j and not h for j, h in pairs),
            "judge_too_strict": sum(h and not j for j, h in pairs),
        }
    return {"cases": len(shared), "items": per_item}
