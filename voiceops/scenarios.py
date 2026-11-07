"""Test scenarios: a simulated shopper, the order they hold, and what should happen."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from voiceops import settings
from voiceops.brand import BrandConfig, UseCase, all_brands
from voiceops.orders import Order, Status

Suite = Literal["smoke", "full"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expect(Strict):
    status: list[Status] = Field(min_length=1)
    address_contains: str | None = None
    address_unchanged: bool = False
    dispute_logged: bool | None = None
    reattempt_on: date | None = None
    no_order_details: bool = False
    max_agent_turns: int | None = None


class Scenario(Strict):
    id: str
    use_case: UseCase
    title: str
    brands: list[str] = Field(min_length=1)
    smoke: list[str] = []
    intent: str
    persona: str = ""
    goal: str = ""
    style: str = "Short, natural replies in simple English."
    facts: dict[str, dict[str, str]] = {}
    script: list[str] | None = None
    call_time: str = "11:00"
    order_overrides: dict[str, str] = {}
    expect: dict[str, Expect]

    @model_validator(mode="after")
    def _shape(self) -> "Scenario":
        if not self.script and not (self.persona and self.goal):
            raise ValueError(f"{self.id}: give either a script or a persona and goal")
        for key in self.expect:
            if key != "default" and key not in self.brands:
                raise ValueError(f"{self.id}: expectation for {key!r}, which is not in brands")
        for key in self.facts:
            if key != "default" and key not in self.brands:
                raise ValueError(f"{self.id}: facts for {key!r}, which is not in brands")
        for brand_id in self.smoke:
            if brand_id not in self.brands:
                raise ValueError(f"{self.id}: smoke brand {brand_id!r} is not in brands")
        return self

    def expect_for(self, brand_id: str) -> Expect:
        if brand_id in self.expect:
            return self.expect[brand_id]
        if "default" in self.expect:
            return self.expect["default"]
        raise ValueError(f"{self.id}: no expectation for {brand_id}")

    def facts_for(self, brand_id: str) -> dict[str, str]:
        return {**self.facts.get("default", {}), **self.facts.get(brand_id, {})}


@dataclass(frozen=True)
class Case:
    """One scenario played against one brand."""
    scenario: Scenario
    brand: BrandConfig
    order: Order
    expect: Expect
    facts: dict[str, str]

    @property
    def case_id(self) -> str:
        return f"{self.scenario.id}@{self.brand.brand_id}"


def load_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in sorted(settings.SCENARIOS_DIR.glob("*.yaml")):
        if path.name == "orders.yaml":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        scenarios.extend(Scenario.model_validate(item) for item in data)
    ids = [s.id for s in scenarios]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate scenario ids: {sorted(duplicates)}")
    return scenarios


def load_order(brand: BrandConfig, use_case: UseCase, overrides: dict[str, str] | None = None) -> Order:
    data = yaml.safe_load((settings.SCENARIOS_DIR / "orders.yaml").read_text(encoding="utf-8"))
    try:
        raw = dict(data[brand.brand_id][use_case])
    except KeyError as exc:
        raise KeyError(f"scenarios/orders.yaml has no {use_case} order for {brand.brand_id}") from exc
    raw.update(overrides or {})
    raw["cod_fee"] = brand.cod.cod_fee
    return Order.model_validate(raw)


def build_cases(suite: Suite = "full", brand_ids: list[str] | None = None,
                scenario_ids: list[str] | None = None) -> list[Case]:
    brands = all_brands()
    cases: list[Case] = []
    for scenario in load_scenarios():
        if scenario_ids and scenario.id not in scenario_ids:
            continue
        for brand_id in scenario.brands:
            if brand_id not in brands:
                raise ValueError(f"{scenario.id}: unknown brand {brand_id!r}")
            if brand_ids and brand_id not in brand_ids:
                continue
            if suite == "smoke" and brand_id not in scenario.smoke:
                continue
            brand = brands[brand_id]
            if not brand.enabled(scenario.use_case):
                continue
            cases.append(Case(
                scenario=scenario,
                brand=brand,
                order=load_order(brand, scenario.use_case, scenario.order_overrides),
                expect=scenario.expect_for(brand_id),
                facts=scenario.facts_for(brand_id),
            ))
    return cases
