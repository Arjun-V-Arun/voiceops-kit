import copy

from tests.fakes import ScriptedLLM, say
from voiceops import checks
from voiceops.scenarios import build_cases
from voiceops.settings import Project
from voiceops.simulate import asr, run_case

PROJECT = Project("agent_v1", "shopper_v1", "judge_v1", "2026-11-09")


def case(case_id: str):
    return next(c for c in build_cases("full") if c.case_id == case_id)


def test_asr_keeps_hindi_and_numbers():
    assert asr("Haan ji, ₹1,299 theek hai!") == "haan ji ₹1,299 theek hai"
    assert asr("नमस्ते, हाँ बोल रहा हूँ।") == "नमस्ते हाँ बोल रहा हूँ"
    assert asr("[silence]") == ""


def test_a_clean_confirmation_passes_every_check(cfg):
    llm = ScriptedLLM(
        agent=[say("Hello, this is Meera from Kesar and Clay. Am I speaking with Priya Sharma?"),
               say("Thanks Priya. Kumkumadi Face Oil and Ubtan Face Wash, 1,299 rupees cash on "
                   "delivery. Shall I confirm?", "verify_identity"),
               say("Confirmed! It will reach you in 3 to 5 days. Thank you, goodbye.", "confirm_order", True)],
        shopper=[{"say": "Yes, this is Priya."}, {"say": "Yes, confirm it."}])
    t = run_case(case("cod_confirm@kesar_and_clay"), llm, cfg, PROJECT, "agent_v1")
    t["score"] = checks.score(t)
    assert t["final"]["status"] == "confirmed" and t["ended_by"] == "agent"
    assert t["score"]["passed"], t["score"]
    # the agent hears an ASR-style transcript plus the result of its last action
    second_agent_request = [r for r in llm.requests if r["role"] == "agent"][1]
    assert second_agent_request["messages"][-1]["content"] == "Customer: yes this is priya"
    third = [r for r in llm.requests if r["role"] == "agent"][2]["messages"][-1]["content"]
    assert third.startswith("[system] Action verify_identity succeeded")


def test_rejected_action_is_retried_before_anything_is_spoken(cfg):
    llm = ScriptedLLM(
        agent=[say("Hi, Rohan from TrailKnot. Am I speaking with Rahul Nair?"),
               say("Thanks. Ridge Runner shoes, 2,899 rupees. Confirm?", "verify_identity"),
               say("Done, I've updated your address!", "update_address", new_address="No. 41, 2nd Cross"),
               say("I can't change the address on this call, so I'll pass you to our support team. Bye.",
                   "handoff", True, reason="address change")],
        shopper=[{"say": "Yes."}, {"say": "The house number should be 41, not 14."}])
    t = run_case(case("cod_address_change@trailknot"), llm, cfg, PROJECT, "agent_v1")
    agent_turn = [x for x in t["turns"] if x["speaker"] == "agent"][2]
    assert agent_turn["text"].startswith("I can't change")
    assert agent_turn["rejected"][0]["unspoken"] == "Done, I've updated your address!"
    assert t["final"]["status"] == "handoff" and not t["final"]["address_changed"]
    score = {c["name"]: c["passed"] for c in checks.score(t)["checks"]}
    assert score["outcome"] and score["order_state"] and score["no_rejected_actions"] is False


def test_scripted_silence_needs_no_shopper_model(cfg):
    llm = ScriptedLLM(agent=[say("Hello, this is Rohan from TrailKnot. Am I speaking with Rahul?"),
                             say("Hello? Can you hear me?"),
                             say("I can't hear you, we'll try later. Goodbye.", "mark_no_response", True)])
    t = run_case(case("cod_no_response@trailknot"), llm, cfg, PROJECT, "agent_v1")
    assert t["final"]["status"] == "no_response"
    assert all(r["role"] == "agent" for r in llm.requests)
    assert checks.score(t)["passed"]


def test_calls_outside_the_window_are_never_placed(cfg):
    llm = ScriptedLLM()
    t = run_case(case("cod_outside_window@trailknot"), llm, cfg, PROJECT, "agent_v1")
    assert not t["called"] and t["final"]["status"] == "not_called" and not llm.requests
    assert checks.score(t)["passed"]


def test_invalid_output_ends_the_call_as_an_error(cfg):
    llm = ScriptedLLM(agent=["oops", "still oops"])
    t = run_case(case("cod_confirm@kesar_and_clay"), llm, cfg, PROJECT, "agent_v1")
    assert t["ended_by"] == "error" and "invalid twice" in t["error"]
    assert not checks.score(t)["passed"]


# --- checks on hand-built transcripts ------------------------------------------------------

BASE = {
    "called": True, "ended_by": "agent", "error": None,
    "expect": {"status": ["confirmed"], "no_order_details": False},
    "final": {"status": "confirmed", "address": "Flat 3A", "address_changed": False,
              "dispute_logged": False, "reattempt_date": None},
    "pii_markers": ["kumkumadi", "1,299", "flat 3a"],
    "brand_names": ["kesar & clay", "kesar and clay"],
    "allowed_discounts": [5],
    "actions": [{"turn": 2, "action": "verify_identity", "ok": True, "message": ""}],
    "turns": [
        {"n": 1, "speaker": "agent", "text": "Hi, Meera from Kesar & Clay. Is this Priya?"},
        {"n": 1, "speaker": "customer", "text": "yes"},
        {"n": 2, "speaker": "agent", "text": "Kumkumadi oil, 1,299 rupees. Confirm?"},
    ],
}


def run_check(fn, **changes):
    t = copy.deepcopy(BASE)
    t.update(changes)
    return fn(t)


def test_privacy_allows_details_after_verification_only():
    assert run_check(checks.check_privacy)["passed"]
    leaked = copy.deepcopy(BASE["turns"])
    leaked[0]["text"] = "Hi Priya, calling about your Kumkumadi oil order."
    result = run_check(checks.check_privacy, turns=leaked)
    assert not result["passed"] and "kumkumadi" in result["detail"]


def test_privacy_flags_verifying_before_the_customer_spoke():
    early = [{"turn": 1, "action": "verify_identity", "ok": True, "message": ""}]
    assert not run_check(checks.check_privacy, actions=early)["passed"]


def test_wrong_number_calls_must_share_nothing():
    expect = {**BASE["expect"], "no_order_details": True}
    assert not run_check(checks.check_privacy, expect=expect)["passed"]


def test_only_the_configured_discount_may_be_mentioned():
    turns = copy.deepcopy(BASE["turns"])
    turns[2]["text"] = "Pay online and get 5% off."
    assert run_check(checks.check_no_unlisted_discount, turns=turns)["passed"]
    turns[2]["text"] = "Okay, I can give you 20 percent off."
    assert not run_check(checks.check_no_unlisted_discount, turns=turns)["passed"]
    turns[2]["text"] = "I'll take 100 rupees off the bill."
    assert not run_check(checks.check_no_unlisted_discount, turns=turns)["passed"]


def test_brand_name_matches_with_and_or_ampersand():
    turns = copy.deepcopy(BASE["turns"])
    turns[0]["text"] = "Hello, Meera here from Kesar and Clay!"
    assert run_check(checks.check_brand_named, turns=turns)["passed"]
    turns[0]["text"] = "Hello, Meera here. Is this Priya?"
    assert not run_check(checks.check_brand_named, turns=turns)["passed"]


def test_agent_must_stop_after_an_opt_out():
    actions = [{"turn": 1, "action": "opt_out", "ok": True, "message": ""}]
    turns = copy.deepcopy(BASE["turns"]) + [{"n": 3, "speaker": "agent", "text": "But wait!"}]
    assert not run_check(checks.check_stops_after_close, actions=actions, turns=turns)["passed"]
    assert run_check(checks.check_stops_after_close, actions=actions, turns=BASE["turns"][:2])["passed"]


def test_checks_that_do_not_apply_are_skipped():
    assert run_check(checks.check_turn_budget)["passed"] is None
    assert run_check(checks.check_order_state)["passed"] is None


# --- hang-ups and rejected generations ----------------------------------------------------

def test_last_words_before_a_hang_up_are_still_acted_on(cfg):
    llm = ScriptedLLM(
        agent=[say("Hello, this is Meera from Kesar and Clay. Am I speaking with Priya Sharma?"),
               say("Thanks. Kumkumadi oil and Ubtan wash, 1,299 rupees, to Flat 3A, Lotus Residency. "
                   "Is everything correct, and shall I confirm your order?", "verify_identity"),
               say("Confirmed.", "confirm_order", True)],
        shopper=[{"say": "Yes, this is Priya."}, {"say": "Yes, confirm it. Bye!", "hang_up": True}])
    t = run_case(case("cod_confirm@kesar_and_clay"), llm, cfg, PROJECT, "agent_v1")
    wrap = t["turns"][-1]
    assert t["final"]["status"] == "confirmed" and t["ended_by"] == "customer"
    assert wrap["wrap_up"] and wrap["text"] == "" and wrap["unspoken"] == "Confirmed."
    last_request = [r for r in llm.requests if r["role"] == "agent"][-1]["messages"][-1]["content"]
    assert "hung up" in last_request and last_request.endswith("Customer: yes confirm it bye")
    assert len(checks.agent_turns(t)) == 2 and len(checks.agent_records(t)) == 3
    assert checks.score(t)["passed"]


def test_no_wrap_up_after_a_closed_call_or_a_silent_hang_up(cfg):
    llm = ScriptedLLM(
        agent=[say("Hello, Meera from Kesar and Clay. Am I speaking with Priya Sharma?"),
               say("Sorry, we won't call again.", "opt_out")],
        shopper=[{"say": "Stop calling this number."}, {"say": "Good.", "hang_up": True}])
    t = run_case(case("cod_opt_out@kesar_and_clay"), llm, cfg, PROJECT, "agent_v1")
    assert t["final"]["status"] == "opted_out" and not any(x.get("wrap_up") for x in t["turns"])
    llm = ScriptedLLM(agent=[say("Hello, Meera from Kesar and Clay. Am I speaking with Priya Sharma?")],
                      shopper=[{"say": "", "hang_up": True}])
    t = run_case(case("cod_confirm@kesar_and_clay"), llm, cfg, PROJECT, "agent_v1")
    assert len(llm.requests) == 2 and not any(x.get("wrap_up") for x in t["turns"])


def test_a_rejected_generation_gets_one_repair(cfg):
    from voiceops.llm import GenerationError
    rejected = GenerationError("rejected", "tool_use_failed",
                               '{"name": "assistant", "arguments": {"say": "hi"}}')
    llm = ScriptedLLM(
        agent=[say("Hello, Meera from Kesar and Clay. Am I speaking with Priya Sharma?"),
               rejected,
               say("Thanks. Shall I confirm your order?", "verify_identity"),
               say("Confirmed, goodbye.", "confirm_order", True)],
        shopper=[{"say": "Yes."}, {"say": "Yes, confirm."}])
    t = run_case(case("cod_confirm@kesar_and_clay"), llm, cfg, PROJECT, "agent_v1")
    assert t["error"] is None and t["final"]["status"] == "confirmed"
    assert [x["repairs"] for x in checks.agent_turns(t)] == [0, 1, 0]
    repaired = [r for r in llm.requests if r["role"] == "agent"][2]["messages"]
    assert repaired[-2]["role"] == "assistant" and "tool_use_failed" in repaired[-1]["content"]


def test_two_rejected_generations_end_the_call_as_an_error(cfg):
    from voiceops.llm import GenerationError
    llm = ScriptedLLM(agent=[GenerationError("x", "tool_use_failed", ""),
                             GenerationError("x", "json_validate_failed", "")])
    t = run_case(case("cod_confirm@kesar_and_clay"), llm, cfg, PROJECT, "agent_v1")
    assert t["ended_by"] == "error" and "invalid twice" in t["error"]
