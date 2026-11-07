"""Turn a brand config and an order into prompts. Also fingerprints for the release gate."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import yaml

from voiceops import settings
from voiceops.brand import BrandConfig, UseCase, brand_path
from voiceops.orders import ACTION_HELP, ACTION_SPECS, Order, allowed_actions, reattempt_window

_VAR = re.compile(r"\{\{(\w+)\}\}")
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]
_SHORT_DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
               "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}


def spoken_date(d: date) -> str:
    return f"{_DAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year}"


def inr(amount: int) -> str:
    """Indian digit grouping: 1,299 and 1,00,000."""
    digits = str(amount)
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def load_template(prompt_id: str) -> str:
    path = settings.PROMPTS_DIR / f"{prompt_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt template at prompts/{prompt_id}.md")
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def render(template: str, values: dict[str, str]) -> str:
    missing = sorted(set(_VAR.findall(template)) - set(values))
    if missing:
        raise KeyError(f"template needs values for: {', '.join(missing)}")
    return _VAR.sub(lambda m: str(values[m.group(1)]), template)


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def order_lines(order: Order, use_case: UseCase) -> list[str]:
    items = ", ".join(f"{i.name} x{i.qty} ({inr(i.price * i.qty)} rupees)" for i in order.items)
    fee = (f", including a {inr(order.cod_fee)} rupees cash-on-delivery fee"
           if order.cod_fee else "")
    lines = [
        f"Order {order.order_id}, placed on {spoken_date(order.placed_on)}",
        f"Items: {items}",
        f"Total to pay in cash on delivery: {inr(order.total)} rupees{fee}",
        f"Delivery address: {order.address}",
    ]
    if use_case == "ndr" and order.attempt_date:
        lines.append(f"Delivery was attempted on {spoken_date(order.attempt_date)}; "
                     f"courier's reason: {order.ndr_reason or 'not given'}")
    return lines


def _language_rule(brand: BrandConfig) -> str:
    if brand.languages == ["en"]:
        return "Speak English."
    if brand.languages == ["hi"]:
        return "Speak Hindi."
    return ("Start in English. If the customer speaks Hindi or Hinglish, reply in the same "
            "language and keep doing so.")


def _flow(brand: BrandConfig, use_case: UseCase, order: Order, today: date) -> str:
    name, who = brand.display_name, order.customer_name
    opening = (f'1. Greet the customer, say you are calling from {name} about '
               f'{"their recent order" if use_case == "cod" else "a delivery"}, '
               f'and ask if you are speaking with {who}.')
    wrong = ('If they say it is the wrong person, take "mark_wrong_number", apologise and end '
             'the call without sharing any order details.')
    closing = [
        "If the customer asks not to be called again, take \"opt_out\", apologise and end the call.",
        "If there is no response twice in a row, take \"mark_no_response\" and end the call.",
    ]
    if use_case == "cod":
        options = ['"confirm_order" when they agree']
        if brand.cod.allow_cancel:
            options.append('"cancel_order" if they want to cancel')
        if brand.cod.allow_address_change:
            options.append('"update_address" if the address is wrong')
        if brand.cod.prepaid_offer.enabled:
            options.append('"send_prepaid_link" if they want to pay online')
        options.append('"schedule_callback" if they are busy')
        steps = [
            opening,
            f'2. When they confirm, take "verify_identity" and, in the same turn, read the items '
            f'and the total and ask them to confirm the order. {wrong}',
            "3. Handle their answer: " + "; ".join(options) + ".",
            "4. After the outcome, say what happens next, thank them and end the call.",
        ]
    else:
        window = reattempt_window(brand, today)
        days = ", ".join(spoken_date(d) for d in window)
        skip = ", ".join(_SHORT_DAYS[d] for d in brand.no_delivery_days)
        fix = ('If the courier could not find the address, take "update_address" with the '
               'corrected address or landmark before booking. '
               if brand.ndr.allow_address_change else "")
        cancel = ('If they no longer want the order, take "cancel_order". '
                  if brand.ndr.allow_cancel else "")
        steps = [
            opening,
            f'2. When they confirm, take "verify_identity" and, in the same turn, say the courier '
            f'could not deliver the order and ask what happened. {wrong}',
            f'3. Book the next attempt with "schedule_reattempt". Allowed dates: {days}'
            + (f" (no deliveries on {skip})" if skip else "") + ". "
            + fix
            + 'If they say the courier never came, take "log_dispute", apologise, then book the '
              'reattempt. ' + cancel,
            "4. Confirm what was arranged, thank them and end the call.",
        ]
    return "\n".join(steps + closing)


def _policy(brand: BrandConfig, use_case: UseCase) -> str:
    lines: list[str] = []
    if use_case == "cod":
        cod = brand.cod
        lines.append(f"The cash-on-delivery fee is {inr(cod.cod_fee)} rupees and is already in the "
                     "total." if cod.cod_fee else "There is no cash-on-delivery fee.")
        offer = cod.prepaid_offer
        if offer.enabled:
            lines.append(f'If the customer prefers to pay online, take "send_prepaid_link": they '
                         f"get a payment link on WhatsApp, {offer.discount_percent}% off the item "
                         "price and no cash-on-delivery fee. This is the only offer. Never promise "
                         "any other discount.")
        else:
            lines.append("There are no discounts or offers. Never promise one.")
        lines.append("The customer may cancel the order." if cod.allow_cancel
                     else "Orders cannot be cancelled on this call; hand off if they insist.")
        lines.append("The delivery address can be corrected on this call." if cod.allow_address_change
                     else "The delivery address cannot be changed on this call.")
    else:
        ndr = brand.ndr
        lines.append(f"A reattempt can be booked up to {ndr.max_reattempt_days} days ahead.")
        lines.append("The address can be corrected or given a landmark on this call."
                     if ndr.allow_address_change else
                     "The delivery address cannot be changed on this call.")
        lines.append("The customer may cancel the order." if ndr.allow_cancel
                     else "Orders cannot be cancelled on this call; hand off if they insist.")
    lines.extend(brand.rules)
    return _bullets(lines)


def _actions(brand: BrandConfig, use_case: UseCase) -> str:
    lines = []
    for name in allowed_actions(brand, use_case):
        args = ACTION_SPECS[name]
        shape = json.dumps({k: f"<{v}>" for k, v in args.items()}) if args else "{}"
        lines.append(f"{name}: {ACTION_HELP[name]}. args: {shape}")
    return _bullets(lines)


def _join_topics(topics: list[str]) -> str:
    """Semicolons, because the topics themselves contain commas."""
    if len(topics) == 1:
        return topics[0]
    return "; ".join(topics[:-1]) + "; or " + topics[-1]


def compile_agent_prompt(prompt_id: str, brand: BrandConfig, use_case: UseCase,
                         order: Order, today: date) -> str:
    purpose = ("confirm a cash-on-delivery order before it ships." if use_case == "cod" else
               "the courier could not deliver an order. Find out what happened and arrange "
               "the next delivery attempt.")
    values = {
        "agent_name": brand.agent_name,
        "brand_name": brand.display_name,
        "category": brand.category,
        "purpose": purpose,
        "today": spoken_date(today),
        "customer_name": order.customer_name,
        "order_block": _bullets(order_lines(order, use_case)),
        "tone": brand.tone,
        "language_rule": _language_rule(brand),
        "flow": _flow(brand, use_case, order, today),
        "policy": _policy(brand, use_case),
        "faqs": _bullets([f"{f.topic}: {f.answer}" for f in brand.faqs]) or "- none",
        "handoff_topics": _join_topics(brand.handoff_topics),
        "actions": _actions(brand, use_case),
    }
    return render(load_template(prompt_id), values)


def policy_summary(brand: BrandConfig, use_case: UseCase, order: Order, today: date) -> str:
    """What the agent was allowed to say, for the judge."""
    faqs = _bullets([f"{f.topic}: {f.answer}" for f in brand.faqs]) or "- none"
    return "\n".join([
        f"Brand: {brand.display_name} ({brand.category}). Today: {spoken_date(today)}.",
        "Order:", _bullets(order_lines(order, use_case)),
        "Policy:", _policy(brand, use_case),
        "FAQ:", faqs,
        f"Must hand off for: {_join_topics(brand.handoff_topics)}.",
    ])


def compile_shopper_prompt(prompt_id: str, order: Order, persona: str, goal: str,
                           facts: dict[str, str], style: str) -> str:
    summary = ", ".join(f"{i.name} x{i.qty}" for i in order.items)
    values = {
        "customer_name": order.customer_name,
        "persona": persona,
        "order_summary": f"{summary}, cash on delivery, {inr(order.total)} rupees in total",
        "address": order.address,
        "goal": goal,
        "facts": "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in facts.items()) or "none",
        "style": style,
    }
    return render(load_template(prompt_id), values)


# --- Fingerprints -------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def prompt_fingerprint(prompt_id: str) -> str:
    return _sha(load_template(prompt_id))


def brand_fingerprint(brand_id: str) -> str:
    """Hash of the validated config, so comments and formatting do not count as changes."""
    data = yaml.safe_load(brand_path(brand_id).read_text(encoding="utf-8"))
    return _sha(json.dumps(data, sort_keys=True, default=str))


HARNESS_FILES = ("agent.py", "llm.py", "orders.py", "prompts.py", "simulate.py")


def harness_fingerprint() -> str:
    """The code that decides what the agent sees and what its actions do."""
    here = Path(__file__).resolve().parent
    return _sha("\n".join((here / name).read_text(encoding="utf-8").replace("\r\n", "\n")
                          for name in HARNESS_FILES))


def suite_fingerprint(shopper_prompt: str, today: str) -> str:
    """Scenarios, orders, the shopper prompt and the simulation code: change any of them and
    old runs stop counting."""
    parts = []
    for path in sorted(settings.SCENARIOS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        parts.append(f"{path.name}:{json.dumps(data, sort_keys=True, default=str)}")
    parts.append(load_template(shopper_prompt))
    parts.append(today)
    parts.append(harness_fingerprint())
    return _sha("\n".join(parts))
