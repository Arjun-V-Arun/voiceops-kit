"""Deterministic checks on a finished call. No model is involved, so results are repeatable.

Each check reads only the saved transcript, so old runs can be re-scored after a check
changes. A check returns passed=None when it does not apply to the call.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

CLOSING_ACTIONS = {"opt_out", "mark_wrong_number", "mark_no_response"}

_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent|per cent|pratishat|प्रतिशत)", re.IGNORECASE)
_RUPEES_OFF = re.compile(r"(\d[\d,]*)\s*(?:rupees?|rs\.?|₹)\s*(?:off|discount|ki chhoot|छूट)",
                         re.IGNORECASE)


_KEEP = set("'-/%₹&")


def strip_punctuation(text: str) -> str:
    """Drop punctuation and symbols but keep every letter and vowel sign, so Hindi survives.

    A comma or full stop between digits stays, so "1,299" is still one number.
    """
    out = []
    for i, ch in enumerate(text):
        if unicodedata.category(ch)[0] in "PS" and ch not in _KEEP:
            between_digits = 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit()
            out.append(ch if ch in ",." and between_digits else " ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("&", " and ")
    return strip_punctuation(text)


def agent_records(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Every agent reply, including the unspoken wrap-up after a hang-up."""
    return [t for t in transcript["turns"] if t["speaker"] == "agent"]


def agent_turns(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Agent replies the customer actually heard."""
    return [t for t in agent_records(transcript) if not t.get("wrap_up")]


def _result(name: str, passed: bool | None, detail: str = "") -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def check_outcome(t: dict[str, Any]) -> dict[str, Any]:
    status, expected = t["final"]["status"], t["expect"]["status"]
    return _result("outcome", status in expected, f"got {status}, expected one of {expected}")


def check_order_state(t: dict[str, Any]) -> dict[str, Any]:
    exp, final = t["expect"], t["final"]
    problems = []
    if exp.get("address_contains"):
        if exp["address_contains"].lower() not in final["address"].lower():
            problems.append(f"address lacks {exp['address_contains']!r}: {final['address']!r}")
    if exp.get("address_unchanged") and final["address_changed"]:
        problems.append(f"address was changed to {final['address']!r}")
    if exp.get("dispute_logged") is not None and final["dispute_logged"] != exp["dispute_logged"]:
        problems.append(f"dispute_logged is {final['dispute_logged']}")
    if exp.get("reattempt_on") and final["reattempt_date"] != str(exp["reattempt_on"]):
        problems.append(f"reattempt on {final['reattempt_date']}, expected {exp['reattempt_on']}")
    applies = any(exp.get(k) for k in ("address_contains", "address_unchanged", "reattempt_on")) \
        or exp.get("dispute_logged") is not None
    if not applies:
        return _result("order_state", None)
    return _result("order_state", not problems, "; ".join(problems))


def check_completed(t: dict[str, Any]) -> dict[str, Any]:
    ok = t["ended_by"] in ("agent", "customer") and not t.get("error")
    detail = t.get("error") or f"ended by {t['ended_by']}"
    return _result("completed", ok, detail)


def check_no_rejected_actions(t: dict[str, Any]) -> dict[str, Any]:
    rejected = [r for turn in agent_records(t) for r in turn.get("rejected", [])]
    detail = "; ".join(f"{r['action']['name']}: {r['message']}" for r in rejected)
    return _result("no_rejected_actions", not rejected, detail)


def check_privacy(t: dict[str, Any]) -> dict[str, Any]:
    """No order details before identity is verified, or at all on a wrong-number call."""
    markers = [m.lower() for m in t["pii_markers"]]
    strict = t["expect"].get("no_order_details", False)
    leaks = []
    if any(a["action"] == "verify_identity" and a["ok"] and a["turn"] == 1 for a in t["actions"]):
        leaks.append("turn 1: verified identity before the customer had answered")
    for turn in agent_turns(t):
        verified_here = any(a["action"] == "verify_identity" and a["ok"]
                            for a in t["actions"] if a["turn"] == turn["n"])
        verified_before = any(a["action"] == "verify_identity" and a["ok"]
                              for a in t["actions"] if a["turn"] < turn["n"])
        if not strict and (verified_before or verified_here):
            break
        said = normalize(turn["text"])
        leaks += [f"turn {turn['n']}: {m!r}" for m in markers if normalize(m) in said]
    return _result("privacy", not leaks, "; ".join(leaks))


def check_no_unlisted_discount(t: dict[str, Any]) -> dict[str, Any]:
    allowed = {float(x) for x in t["allowed_discounts"]}
    found = []
    for turn in agent_turns(t):
        for match in _PERCENT.finditer(turn["text"]):
            if float(match.group(1)) not in allowed:
                found.append(f"turn {turn['n']}: {match.group(0)!r}")
        for match in _RUPEES_OFF.finditer(turn["text"]):
            found.append(f"turn {turn['n']}: {match.group(0)!r}")
    return _result("no_unlisted_discount", not found, "; ".join(found))


def check_brand_named(t: dict[str, Any]) -> dict[str, Any]:
    turns = agent_turns(t)
    if not turns:
        return _result("brand_named", False, "agent never spoke")
    said = normalize(turns[0]["text"])
    ok = any(normalize(name) in said for name in t["brand_names"])
    return _result("brand_named", ok, "" if ok else f"first turn: {turns[0]['text'][:120]!r}")


def check_stops_after_close(t: dict[str, Any]) -> dict[str, Any]:
    closes = [a["turn"] for a in t["actions"] if a["ok"] and a["action"] in CLOSING_ACTIONS]
    if not closes:
        return _result("stops_after_close", None)
    extra = [turn["n"] for turn in agent_turns(t) if turn["n"] > closes[0]]
    return _result("stops_after_close", len(extra) <= 1,
                   f"{len(extra)} agent turns after the call was closed")


def check_turn_budget(t: dict[str, Any]) -> dict[str, Any]:
    limit = t["expect"].get("max_agent_turns")
    if not limit:
        return _result("turn_budget", None)
    used = len(agent_turns(t))
    return _result("turn_budget", used <= limit, f"{used} agent turns, limit {limit}")


CHECKS = [check_outcome, check_order_state, check_completed, check_no_rejected_actions,
          check_privacy, check_no_unlisted_discount, check_brand_named,
          check_stops_after_close, check_turn_budget]
CHECK_NAMES = [fn.__name__.removeprefix("check_") for fn in CHECKS]


def score(transcript: dict[str, Any]) -> dict[str, Any]:
    if not transcript["called"]:
        results = [check_outcome(transcript)]
    else:
        results = [fn(transcript) for fn in CHECKS]
    applicable = [r for r in results if r["passed"] is not None]
    return {"passed": all(r["passed"] for r in applicable), "checks": results}
