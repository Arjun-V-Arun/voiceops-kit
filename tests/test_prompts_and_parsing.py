import json
import re
from datetime import date

import pytest

from tests.fakes import ScriptedLLM
from voiceops import prompts
from voiceops.agent import AgentReply, InvalidOutput, ShopperReply, ask, parse_json_object
from voiceops.brand import all_brands
from voiceops.scenarios import build_cases, load_order
from voiceops.settings import RoleSettings

TODAY = date(2026, 11, 9)
ROLE = RoleSettings("fake", 0.0, 100)


def test_every_brand_and_call_type_compiles_without_leftover_placeholders():
    for brand in all_brands().values():
        for use_case in ("cod", "ndr"):
            text = prompts.compile_agent_prompt("agent_v1", brand, use_case,
                                                load_order(brand, use_case), TODAY)
            assert not re.search(r"\{\{\w+\}\}", text)
            assert brand.display_name in text


def test_prompt_lists_only_allowed_actions():
    brands = all_brands()
    tk = prompts.compile_agent_prompt("agent_v1", brands["trailknot"], "cod",
                                      load_order(brands["trailknot"], "cod"), TODAY)
    assert "update_address:" not in tk and "send_prepaid_link" not in tk
    assert "no discounts or offers" in tk


def test_ndr_prompt_offers_only_valid_dates():
    brand = all_brands()["kesar_and_clay"]
    text = prompts.compile_agent_prompt("agent_v1", brand, "ndr", load_order(brand, "ndr"), TODAY)
    assert "Tuesday, 10 November 2026" in text and "Thursday, 12 November 2026" in text
    assert "Friday, 13 November 2026" not in text  # beyond the 3-day window


def test_render_complains_about_missing_values():
    with pytest.raises(KeyError, match="needs values for: b"):
        prompts.render("{{a}} {{b}}", {"a": "x"})


def test_shopper_prompts_compile_for_every_case():
    for case in build_cases("full"):
        if case.scenario.script:
            continue
        text = prompts.compile_shopper_prompt("shopper_v1", case.order, case.scenario.persona,
                                              case.scenario.goal, case.facts, case.scenario.style)
        assert case.order.customer_name in text


@pytest.mark.parametrize("amount, text", [(899, "899"), (1299, "1,299"), (123456, "1,23,456")])
def test_indian_number_format(amount, text):
    assert prompts.inr(amount) == text


def test_fingerprints_ignore_line_endings_and_comments(project_copy):
    template = project_copy / "prompts" / "agent_v1.md"
    before = prompts.prompt_fingerprint("agent_v1")
    template.write_bytes(template.read_bytes().replace(b"\n", b"\r\n"))
    assert prompts.prompt_fingerprint("agent_v1") == before
    brand = project_copy / "brands" / "trailknot.yaml"
    brand_before = prompts.brand_fingerprint("trailknot")
    brand.write_text("# a new comment\n" + brand.read_text(encoding="utf-8"), encoding="utf-8")
    assert prompts.brand_fingerprint("trailknot") == brand_before
    brand.write_text(brand.read_text(encoding="utf-8").replace("cod_fee: 0", "cod_fee: 10"),
                     encoding="utf-8")
    assert prompts.brand_fingerprint("trailknot") != brand_before


def test_parse_tolerates_fences_and_stray_text():
    assert parse_json_object('```json\n{"say": "hi"}\n```') == {"say": "hi"}
    assert parse_json_object('Sure! {"say": "hi"} hope that helps') == {"say": "hi"}
    with pytest.raises(ValueError):
        parse_json_object("no json here")


def test_agent_reply_normalises_loose_action_shapes():
    assert AgentReply.model_validate({"say": "x", "action": "confirm_order"}).action.name == "confirm_order"
    assert AgentReply.model_validate({"say": "x", "action": {"name": "opt_out", "args": None}}).action.args == {}
    assert AgentReply.model_validate({"say": "x", "action": {"name": ""}}).action is None
    assert AgentReply.model_validate({"say": None}).say == ""


def test_ask_repairs_once_without_polluting_history():
    llm = ScriptedLLM(agent=["not json", {"say": "hello"}])
    history = [{"role": "system", "content": "s"}]
    asked = ask(llm, "agent", "p", ROLE, history, AgentReply, "t")
    assert asked.parsed.say == "hello" and asked.repairs == 1
    assert len(history) == 1
    assert "[system] Your reply was not valid" in llm.requests[1]["messages"][-1]["content"]


def test_ask_gives_up_after_two_invalid_replies():
    llm = ScriptedLLM(shopper=["[1, 2]", "still not an object"])
    with pytest.raises(InvalidOutput):
        ask(llm, "shopper", "p", ROLE, [], ShopperReply, "t")


def test_schema_errors_count_as_invalid():
    llm = ScriptedLLM(agent=[json.dumps({"say": "x", "end_call": "maybe"}), {"say": "ok"}])
    asked = ask(llm, "agent", "p", ROLE, [], AgentReply, "t")
    assert asked.repairs == 1


def test_simulation_code_is_part_of_the_suite_fingerprint(monkeypatch):
    before = prompts.suite_fingerprint("shopper_v1", "2026-11-09")
    monkeypatch.setattr(prompts, "harness_fingerprint", lambda: "different code")
    assert prompts.suite_fingerprint("shopper_v1", "2026-11-09") != before


def test_v3_asks_one_question_and_compiles_for_every_brand():
    for brand in all_brands().values():
        for use_case in ("cod", "ndr"):
            text = prompts.compile_agent_prompt("agent_v3", brand, use_case,
                                                load_order(brand, use_case), TODAY)
            assert "shall I confirm your order?" in text and not re.search(r"\{\{\w+\}\}", text)
