"""Play simulated calls: the agent under test against a simulated shopper."""
from __future__ import annotations

import json
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from voiceops import checks, prompts
from voiceops.agent import AgentReply, InvalidOutput, ShopperReply, ask
from voiceops.llm import LLM, Completion, FatalLLMError, LLMError
from voiceops.orders import CallState, can_place_call
from voiceops.scenarios import Case
from voiceops.settings import Project, Settings

SILENCE = "[silence]"

# A real call is transcribed up to the moment it drops, so a customer who says "yes, confirm
# it" and hangs up has still confirmed. After a hang-up the agent gets one silent turn to
# record what the customer asked for. Whatever it says is not heard.
HANG_UP_NOTE = ("[system] The customer hung up right after the line below. Nothing you say now "
                "will be heard. If their last words clearly ask for or agree to an outcome, take "
                "that action now. Otherwise take no action. Set end_call to true.")


def asr(text: str) -> str:
    """Make typed text look like a speech-to-text transcript: lowercase, little punctuation."""
    if text.strip().lower() == SILENCE:
        return ""
    return checks.strip_punctuation(unicodedata.normalize("NFKC", text).lower())


class Usage:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {"agent": 0, "shopper": 0}
        self.input_tokens = 0
        self.output_tokens = 0
        self.agent_latency_ms: list[int] = []
        self.cached_calls = 0

    def add(self, role: str, completions: list[Completion]) -> None:
        for c in completions:
            self.calls[role] = self.calls.get(role, 0) + 1
            self.input_tokens += c.input_tokens
            self.output_tokens += c.output_tokens
            if c.cached:
                self.cached_calls += 1
            elif role == "agent":
                self.agent_latency_ms.append(c.latency_ms)

    def as_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cached_calls": self.cached_calls,
                "agent_latency_ms": self.agent_latency_ms}


def _agent_json(reply: AgentReply) -> str:
    return json.dumps(reply.model_dump(mode="json"), ensure_ascii=False)


def agent_turn(llm: LLM, cfg: Settings, prompt_id: str, messages: list[dict[str, str]],
               state: CallState, n: int, tag: str, usage: Usage) -> tuple[AgentReply, dict[str, Any], str | None]:
    """One agent turn. A rejected action gets one retry before anything is spoken."""
    asked = ask(llm, "agent", prompt_id, cfg.agent, messages, AgentReply, tag)
    usage.add("agent", asked.completions)
    reply: AgentReply = asked.parsed
    repairs = asked.repairs
    messages.append({"role": "assistant", "content": _agent_json(reply)})
    rejected: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    note: str | None = None

    if reply.action:
        outcome = state.apply(reply.action, n)
        if not outcome.ok:
            rejected.append({"action": reply.action.model_dump(), "message": outcome.message,
                             "unspoken": reply.say})
            messages.append({"role": "user", "content":
                             f"[system] Action {reply.action.name} failed: {outcome.message}. "
                             "The customer did not hear your last reply. Reply again."})
            asked = ask(llm, "agent", prompt_id, cfg.agent, messages, AgentReply, tag + ":retry")
            usage.add("agent", asked.completions)
            reply, repairs = asked.parsed, repairs + asked.repairs
            messages.append({"role": "assistant", "content": _agent_json(reply)})
            if reply.action:
                outcome = state.apply(reply.action, n)
                if not outcome.ok:
                    rejected.append({"action": reply.action.model_dump(),
                                     "message": outcome.message, "unspoken": None})
        if reply.action:
            result = {"ok": outcome.ok, "message": outcome.message}
            word = "succeeded" if outcome.ok else "failed"
            note = f"[system] Action {reply.action.name} {word}: {outcome.message}."

    record = {"n": n, "speaker": "agent", "text": reply.say,
              "action": reply.action.model_dump() if reply.action else None,
              "action_result": result, "rejected": rejected, "repairs": repairs,
              "end_call": reply.end_call}
    return reply, record, note


def wrap_up(llm: LLM, cfg: Settings, prompt_id: str, messages: list[dict[str, str]],
            state: CallState, n: int, note: str | None, transcribed: str, tag: str,
            usage: Usage, turns: list[dict[str, Any]]) -> None:
    """One unspoken agent turn after a hang-up, so the last thing said can still be acted on."""
    parts = [p for p in (note, HANG_UP_NOTE, f"Customer: {transcribed}") if p]
    messages.append({"role": "user", "content": "\n".join(parts)})
    reply, record, _ = agent_turn(llm, cfg, prompt_id, messages, state, n, tag, usage)
    record.update({"wrap_up": True, "unspoken": reply.say, "text": ""})
    turns.append(record)


def shopper_turn(llm: LLM, cfg: Settings, prompt_id: str, system: str,
                 heard: list[tuple[str, str]], tag: str, usage: Usage) -> ShopperReply:
    lines = "\n".join(f"{'Caller' if who == 'agent' else 'You'}: {text}" for who, text in heard)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"The call so far:\n{lines}\n\nReply as the customer."}]
    asked = ask(llm, "shopper", prompt_id, cfg.shopper, messages, ShopperReply, tag)
    usage.add("shopper", asked.completions)
    return asked.parsed


def run_case(case: Case, llm: LLM, cfg: Settings, project: Project, prompt_id: str) -> dict[str, Any]:
    brand, order, scenario = case.brand, case.order, case.scenario
    today = date.fromisoformat(project.today)
    base: dict[str, Any] = {
        "case_id": case.case_id, "scenario": scenario.id, "title": scenario.title,
        "brand": brand.brand_id, "use_case": scenario.use_case,
        "agent_prompt": prompt_id, "shopper_prompt": project.shopper_prompt,
        "models": {"agent": cfg.agent.model, "shopper": cfg.shopper.model},
        "today": project.today, "call_time": scenario.call_time,
        "expect": case.expect.model_dump(mode="json"),
        "pii_markers": order.pii_markers,
        "brand_names": brand.name_variants(),
        "allowed_discounts": sorted(brand.allowed_discounts()),
    }
    state = CallState(brand=brand, use_case=scenario.use_case, order=order, today=today)
    usage = Usage()
    guard = can_place_call(brand, order.phone, scenario.call_time, do_not_call=set())
    if not guard.ok:
        state.status = "not_called"
        return {**base, "called": False, "not_called_reason": guard.reason, "turns": [],
                "ended_by": "guard", "error": None, "final": _final(state), "actions": [],
                "usage": usage.as_dict()}

    messages = [{"role": "system", "content": prompts.compile_agent_prompt(
        prompt_id, brand, scenario.use_case, order, today)}]
    shopper_system = None
    if not scenario.script:
        shopper_system = prompts.compile_shopper_prompt(
            project.shopper_prompt, order, scenario.persona, scenario.goal, case.facts, scenario.style)
    turns: list[dict[str, Any]] = []
    heard: list[tuple[str, str]] = []
    ended_by, error = "max_turns", None

    try:
        for n in range(1, brand.max_turns + 1):
            reply, record, note = agent_turn(llm, cfg, prompt_id, messages, state, n,
                                             f"{case.case_id}#{n}", usage)
            turns.append(record)
            heard.append(("agent", reply.say))
            if reply.end_call:
                ended_by = "agent"
                break
            if scenario.script is not None:
                line = scenario.script[n - 1] if n - 1 < len(scenario.script) else None
                said, hang_up = (line or "", line is None)
            else:
                shopper = shopper_turn(llm, cfg, project.shopper_prompt, shopper_system, heard,
                                       f"{case.case_id}#{n}:shopper", usage)
                said, hang_up = shopper.say, shopper.hang_up
            transcribed = asr(said)
            turns.append({"n": n, "speaker": "customer", "text": said, "heard_as": transcribed,
                          "hang_up": hang_up})
            heard.append(("customer", said))
            if hang_up:
                ended_by = "customer"
                if transcribed and not state.closed:
                    wrap_up(llm, cfg, prompt_id, messages, state, n + 1, note, transcribed,
                            f"{case.case_id}#{n + 1}:wrap_up", usage, turns)
                break
            content = f"Customer: {transcribed}" if transcribed else "Customer: (no response)"
            messages.append({"role": "user", "content": f"{note}\n{content}" if note else content})
    except InvalidOutput as exc:
        ended_by, error = "error", str(exc)
    except FatalLLMError:
        raise
    except LLMError as exc:
        ended_by, error = "error", str(exc)

    return {**base, "called": True, "not_called_reason": None, "turns": turns,
            "ended_by": ended_by, "error": error, "final": _final(state),
            "actions": state.log, "usage": usage.as_dict()}


def _final(state: CallState) -> dict[str, Any]:
    return {"status": state.status, "identity_verified": state.identity_verified,
            "address": state.address, "address_changed": state.address_changed,
            "dispute_logged": state.dispute_logged,
            "reattempt_date": state.reattempt_date.isoformat() if state.reattempt_date else None,
            "callback_when": state.callback_when}


def safe_name(case_id: str) -> str:
    return case_id.replace("@", "__")


def run_suite(cases: list[Case], llm: LLM, cfg: Settings, project: Project, prompt_id: str,
              run_dir: Path, meta: dict[str, Any],
              llm_for: Callable[[Case], LLM] | None = None,
              progress: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """Run every case, writing each transcript as soon as it finishes."""
    out = run_dir / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    meta = {**meta, "started": datetime.now().isoformat(timespec="seconds"), "complete": False,
            "cases": [c.case_id for c in cases]}
    _write_json(run_dir / "meta.json", meta)
    transcripts = []
    for i, case in enumerate(cases, 1):
        started = time.monotonic()
        transcript = run_case(case, (llm_for or (lambda _c: llm))(case), cfg, project, prompt_id)
        transcript["score"] = checks.score(transcript)
        _write_json(out / f"{safe_name(case.case_id)}.json", transcript)
        transcripts.append(transcript)
        mark = "PASS" if transcript["score"]["passed"] else "FAIL"
        progress(f"[{i}/{len(cases)}] {mark} {case.case_id}: {transcript['final']['status']} "
                 f"({time.monotonic() - started:.0f}s)")
    meta["complete"] = True
    meta["finished"] = datetime.now().isoformat(timespec="seconds")
    _write_json(run_dir / "meta.json", meta)
    return transcripts


def load_transcripts(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((run_dir / "transcripts").glob("*.json"))]


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
