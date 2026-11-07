"""Compare two runs call by call: what a prompt or config change fixed and what it broke."""
from __future__ import annotations

from typing import Any

from voiceops import checks


def _index(transcripts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Re-score every call, so a run made before a check changed is judged by today's checks."""
    return {t["case_id"]: {**t, "score": checks.score(t)} for t in transcripts}


def _check_map(t: dict[str, Any]) -> dict[str, bool | None]:
    return {c["name"]: c["passed"] for c in t["score"]["checks"]}


def compare(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    a, b = _index(before), _index(after)
    rows = []
    for case_id in sorted(set(a) & set(b)):
        old, new = a[case_id], b[case_id]
        old_checks, new_checks = _check_map(old), _check_map(new)
        broke = sorted(k for k, v in new_checks.items() if v is False and old_checks.get(k) is not False)
        fixed = sorted(k for k, v in old_checks.items() if v is False and new_checks.get(k) is not False)
        was, now = old["score"]["passed"], new["score"]["passed"]
        kind = ("broken" if was and not now else "fixed" if now and not was
                else "still_failing" if not now else "still_passing")
        rows.append({"case_id": case_id, "kind": kind,
                     "status_before": old["final"]["status"], "status_after": new["final"]["status"],
                     "checks_broken": broke, "checks_fixed": fixed})
    count = {k: sum(r["kind"] == k for r in rows)
             for k in ("fixed", "broken", "still_failing", "still_passing")}
    return {"rows": rows, "count": count,
            "only_before": sorted(set(a) - set(b)), "only_after": sorted(set(b) - set(a))}


def markdown(result: dict[str, Any], before: str, after: str) -> str:
    c = result["count"]
    lines = [f"# {before} vs {after}", "",
             f"Fixed {c['fixed']}, broke {c['broken']}, still failing {c['still_failing']}, "
             f"still passing {c['still_passing']}.", ""]
    changed = [r for r in result["rows"] if r["kind"] != "still_passing"
               or r["checks_broken"] or r["status_before"] != r["status_after"]]
    if changed:
        lines += ["| Call | Change | Outcome before | Outcome after | Checks broken | Checks fixed |",
                  "|---|---|---|---|---|---|"]
        for r in changed:
            lines.append(f"| {r['case_id']} | {r['kind'].replace('_', ' ')} | {r['status_before']} "
                         f"| {r['status_after']} | {', '.join(r['checks_broken'])} "
                         f"| {', '.join(r['checks_fixed'])} |")
    for label, ids in (("Only in " + before, result["only_before"]), ("Only in " + after, result["only_after"])):
        if ids:
            lines += ["", f"{label}: {', '.join(ids)}"]
    return "\n".join(lines) + "\n"
