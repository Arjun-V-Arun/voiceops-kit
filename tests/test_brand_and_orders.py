from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from voiceops.brand import BrandConfig, all_brands, load_brand
from voiceops.orders import ActionCall, CallState, allowed_actions, can_place_call, reattempt_window
from voiceops.scenarios import load_order
from tests.conftest import REPO

TODAY = date(2026, 11, 9)  # a Monday


def state(brand_id: str, use_case: str) -> CallState:
    brand = load_brand(brand_id)
    return CallState(brand=brand, use_case=use_case, order=load_order(brand, use_case), today=TODAY)


def act(s: CallState, name: str, turn: int = 2, **args):
    return s.apply(ActionCall(name=name, args=args), turn)


def test_shipped_brands_and_template_are_valid():
    assert set(all_brands()) == {"kesar_and_clay", "trailknot"}
    template = yaml.safe_load((REPO / "brands" / "_template.yaml").read_text(encoding="utf-8"))
    BrandConfig.model_validate(template)


@pytest.mark.parametrize("change, message", [
    ({"calling_window": {"start": "21:00", "end": "10:00"}}, "start before"),
    ({"calling_window": {"start": "9am", "end": "21:00"}}, "HH:MM"),
    ({"brand_id": "Bad Name"}, "lowercase"),
    ({"surprise_field": 1}, "Extra inputs"),
])
def test_bad_configs_are_rejected(change, message):
    data = yaml.safe_load((REPO / "brands" / "trailknot.yaml").read_text(encoding="utf-8"))
    data.update(change)
    with pytest.raises(ValidationError, match=message):
        BrandConfig.model_validate(data)


def test_discount_without_an_enabled_offer_is_rejected():
    data = yaml.safe_load((REPO / "brands" / "trailknot.yaml").read_text(encoding="utf-8"))
    data["cod"]["prepaid_offer"] = {"enabled": False, "discount_percent": 10}
    with pytest.raises(ValidationError, match="discount_percent must be 0"):
        BrandConfig.model_validate(data)


def test_actions_follow_brand_policy():
    kc = allowed_actions(load_brand("kesar_and_clay"), "cod")
    tk = allowed_actions(load_brand("trailknot"), "cod")
    assert "update_address" in kc and "send_prepaid_link" in kc
    assert "update_address" not in tk and "send_prepaid_link" not in tk


def test_identity_must_come_first():
    s = state("kesar_and_clay", "cod")
    result = act(s, "confirm_order")
    assert not result.ok and "identity" in result.message
    assert act(s, "verify_identity").ok
    assert act(s, "confirm_order").ok and s.status == "confirmed"


def test_backend_refuses_what_the_brand_does_not_allow():
    s = state("trailknot", "cod")
    act(s, "verify_identity")
    result = act(s, "update_address", new_address="No. 41, 2nd Cross, HSR Layout")
    assert not result.ok and "not allowed" in result.message
    assert s.address == s.order.address and not s.address_changed
    assert not act(s, "send_prepaid_link").ok


def test_missing_and_short_arguments():
    s = state("kesar_and_clay", "cod")
    act(s, "verify_identity")
    assert "needs new_address" in act(s, "update_address").message
    assert "too short" in act(s, "update_address", new_address="3B").message


def test_reattempt_dates_respect_the_window_and_sundays():
    brand = load_brand("trailknot")
    window = reattempt_window(brand, TODAY)
    assert date(2026, 11, 15) not in window  # a Sunday
    assert window[0] == date(2026, 11, 10) and window[-1] == date(2026, 11, 14)
    s = state("trailknot", "ndr")
    act(s, "verify_identity")
    assert "not a delivery day" in act(s, "schedule_reattempt", date="2026-11-19").message
    assert "YYYY-MM-DD" in act(s, "schedule_reattempt", date="next tuesday").message
    assert act(s, "schedule_reattempt", date="2026-11-14").ok
    assert s.status == "reattempt_scheduled" and s.reattempt_date == date(2026, 11, 14)


def test_nothing_happens_after_the_call_is_closed():
    s = state("kesar_and_clay", "cod")
    assert act(s, "opt_out", turn=1).ok
    assert not act(s, "verify_identity").ok
    assert s.status == "opted_out"


def test_wrong_number_cannot_follow_a_verified_identity():
    s = state("kesar_and_clay", "cod")
    act(s, "verify_identity")
    assert not act(s, "mark_wrong_number").ok


def test_every_action_is_logged():
    s = state("kesar_and_clay", "cod")
    act(s, "confirm_order", turn=1)
    act(s, "verify_identity", turn=2)
    assert [(e["action"], e["ok"]) for e in s.log] == [("confirm_order", False), ("verify_identity", True)]


def test_pre_call_guards():
    brand = load_brand("trailknot")
    assert can_place_call(brand, "+91 1", "11:00", set()).ok
    assert "calling window" in can_place_call(brand, "+91 1", "21:30", set()).reason
    assert "do-not-call" in can_place_call(brand, "+91 1", "11:00", {"+91 1"}).reason
