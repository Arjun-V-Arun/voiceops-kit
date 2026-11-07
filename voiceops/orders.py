"""The order backend the agent acts on.

The agent proposes actions; this module decides whether each one is allowed.
Brand policy is enforced here, in code, so a prompt mistake cannot change an
order in a way the merchant did not allow. The tests check whether the agent
even tries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from voiceops.brand import BrandConfig, UseCase

Status = Literal[
    "pending_confirmation",  # COD order waiting for this call
    "delivery_failed",  # NDR order waiting for this call
    "confirmed",
    "cancelled",
    "prepaid_link_sent",
    "reattempt_scheduled",
    "callback_scheduled",
    "handoff",
    "wrong_number",
    "opted_out",
    "no_response",
    "not_called",
]

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Actions allowed before the customer has confirmed who they are.
PRE_IDENTITY = {"verify_identity", "mark_wrong_number", "opt_out", "handoff",
                "schedule_callback", "mark_no_response"}


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    qty: int = Field(ge=1)
    price: int = Field(ge=0)


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    customer_name: str
    phone: str
    items: list[Item] = Field(min_length=1)
    address: str
    placed_on: date
    cod_fee: int = 0
    attempt_date: date | None = None
    ndr_reason: str | None = None
    # Strings that count as order details for the privacy checks.
    pii_markers: list[str] = Field(min_length=1)

    @property
    def item_total(self) -> int:
        return sum(item.qty * item.price for item in self.items)

    @property
    def total(self) -> int:
        return self.item_total + self.cod_fee


class ActionCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    args: dict[str, Any] = {}


ACTION_SPECS: dict[str, dict[str, str]] = {
    "verify_identity": {},
    "confirm_order": {},
    "cancel_order": {"reason": "short reason the customer gave"},
    "update_address": {"new_address": "full corrected address or landmark"},
    "send_prepaid_link": {},
    "schedule_reattempt": {"date": "YYYY-MM-DD"},
    "log_dispute": {"note": "what the customer said happened"},
    "schedule_callback": {"when": "time the customer asked for"},
    "mark_wrong_number": {},
    "opt_out": {},
    "handoff": {"reason": "why a human is needed"},
    "mark_no_response": {},
}

ACTION_HELP = {
    "verify_identity": "the customer confirmed they are the person on the order",
    "confirm_order": "the customer agreed to receive and pay for the order",
    "cancel_order": "the customer wants to cancel",
    "update_address": "correct the delivery address",
    "send_prepaid_link": "send a payment link on WhatsApp so they can pay online",
    "schedule_reattempt": "book the next delivery attempt",
    "log_dispute": "record that the customer says the courier never came",
    "schedule_callback": "the customer is busy and asked to be called later",
    "mark_wrong_number": "the person is not the customer on the order",
    "opt_out": "the customer asked not to be called again",
    "handoff": "pass the call to a human on the support team",
    "mark_no_response": "there was no answer twice in a row",
}


def allowed_actions(brand: BrandConfig, use_case: UseCase) -> list[str]:
    """Actions this brand allows for this kind of call, in prompt order."""
    common_tail = ["schedule_callback", "mark_wrong_number", "opt_out", "handoff", "mark_no_response"]
    if use_case == "cod":
        policy = brand.cod
        names = ["verify_identity", "confirm_order"]
        if policy.allow_cancel:
            names.append("cancel_order")
        if policy.allow_address_change:
            names.append("update_address")
        if policy.prepaid_offer.enabled:
            names.append("send_prepaid_link")
    else:
        policy = brand.ndr
        names = ["verify_identity", "schedule_reattempt", "log_dispute"]
        if policy.allow_address_change:
            names.append("update_address")
        if policy.allow_cancel:
            names.append("cancel_order")
    return names + common_tail


def reattempt_window(brand: BrandConfig, today: date) -> list[date]:
    days = [today + timedelta(days=n) for n in range(1, brand.ndr.max_reattempt_days + 1)]
    return [d for d in days if WEEKDAYS[d.weekday()] not in brand.no_delivery_days]


@dataclass
class ActionResult:
    ok: bool
    message: str


@dataclass
class CallState:
    brand: BrandConfig
    use_case: UseCase
    order: Order
    today: date
    status: Status = "pending_confirmation"
    identity_verified: bool = False
    address: str = ""
    address_changed: bool = False
    dispute_logged: bool = False
    reattempt_date: date | None = None
    callback_when: str | None = None
    log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.address = self.order.address
        self.status = "pending_confirmation" if self.use_case == "cod" else "delivery_failed"

    @property
    def closed(self) -> bool:
        """After these outcomes the agent should only say goodbye."""
        return self.status in ("wrong_number", "opted_out", "no_response")

    def apply(self, call: ActionCall, turn: int) -> ActionResult:
        result = self._apply(call)
        self.log.append({"turn": turn, "action": call.name, "args": call.args,
                         "ok": result.ok, "message": result.message})
        return result

    def _apply(self, call: ActionCall) -> ActionResult:
        name, args = call.name, call.args
        if name not in ACTION_SPECS:
            return ActionResult(False, f"unknown action {name!r}")
        if name not in allowed_actions(self.brand, self.use_case):
            return ActionResult(False, f"{name} is not allowed for {self.brand.display_name} "
                                       f"on this call")
        missing = [key for key in ACTION_SPECS[name] if not str(args.get(key, "")).strip()]
        if missing:
            return ActionResult(False, f"{name} needs {', '.join(missing)}")
        if self.closed:
            return ActionResult(False, f"the call outcome is already {self.status}; say goodbye")
        if name not in PRE_IDENTITY and not self.identity_verified:
            return ActionResult(False, "verify the customer's identity first")

        if name == "verify_identity":
            self.identity_verified = True
            return ActionResult(True, "identity verified")
        if name == "confirm_order":
            self.status = "confirmed"
            return ActionResult(True, "order confirmed for dispatch")
        if name == "cancel_order":
            self.status = "cancelled"
            return ActionResult(True, "order cancelled")
        if name == "update_address":
            new = str(args["new_address"]).strip()
            if len(new) < 8:
                return ActionResult(False, "new_address is too short to be an address")
            self.address, self.address_changed = new, True
            return ActionResult(True, "address updated")
        if name == "send_prepaid_link":
            self.status = "prepaid_link_sent"
            return ActionResult(True, "payment link sent on WhatsApp")
        if name == "schedule_reattempt":
            try:
                when = date.fromisoformat(str(args["date"]).strip())
            except ValueError:
                return ActionResult(False, "date must be YYYY-MM-DD")
            window = reattempt_window(self.brand, self.today)
            if when not in window:
                allowed = ", ".join(d.isoformat() for d in window)
                return ActionResult(False, f"{when.isoformat()} is not a delivery day this brand "
                                           f"allows; allowed dates: {allowed}")
            self.reattempt_date, self.status = when, "reattempt_scheduled"
            return ActionResult(True, f"delivery reattempt booked for {when.isoformat()}")
        if name == "log_dispute":
            self.dispute_logged = True
            return ActionResult(True, "dispute recorded for the courier team")
        if name == "schedule_callback":
            self.callback_when, self.status = str(args["when"]).strip(), "callback_scheduled"
            return ActionResult(True, "callback scheduled")
        if name == "mark_wrong_number":
            if self.identity_verified:
                return ActionResult(False, "identity was already verified on this call")
            self.status = "wrong_number"
            return ActionResult(True, "marked as wrong number")
        if name == "opt_out":
            self.status = "opted_out"
            return ActionResult(True, "number added to the do-not-call list")
        if name == "handoff":
            self.status = "handoff"
            return ActionResult(True, "a support agent will take over")
        if name == "mark_no_response":
            self.status = "no_response"
            return ActionResult(True, "marked as no response")
        return ActionResult(False, f"unhandled action {name!r}")  # pragma: no cover


@dataclass
class Guard:
    ok: bool
    reason: str


def can_place_call(brand: BrandConfig, phone: str, call_time: str,
                   do_not_call: set[str]) -> Guard:
    """Checks that run before dialling. No model is involved."""
    if phone in do_not_call:
        return Guard(False, "number is on the do-not-call list")
    if not brand.calling_window.contains(call_time):
        window = brand.calling_window
        return Guard(False, f"{call_time} is outside the calling window {window.start}-{window.end}")
    return Guard(True, "ok")
