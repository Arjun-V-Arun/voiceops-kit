"""Brand configuration: what a merchant tells us, as a validated file."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from voiceops import settings

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
Language = Literal["en", "hi"]
UseCase = Literal["cod", "ndr"]

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallingWindow(Strict):
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not _TIME.match(value):
            raise ValueError("use 24-hour HH:MM, for example 10:00")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> "CallingWindow":
        if self.start >= self.end:
            raise ValueError("calling window must start before it ends")
        return self

    def contains(self, hhmm: str) -> bool:
        return self.start <= hhmm < self.end


class PrepaidOffer(Strict):
    enabled: bool = False
    discount_percent: int = Field(default=0, ge=0, le=50)

    @model_validator(mode="after")
    def _consistent(self) -> "PrepaidOffer":
        if not self.enabled and self.discount_percent:
            raise ValueError("discount_percent must be 0 when the prepaid offer is disabled")
        return self


class CodPolicy(Strict):
    enabled: bool = True
    cod_fee: int = Field(ge=0)
    allow_cancel: bool = True
    allow_address_change: bool
    prepaid_offer: PrepaidOffer = PrepaidOffer()


class NdrPolicy(Strict):
    enabled: bool = True
    max_reattempt_days: int = Field(ge=1, le=10)
    allow_address_change: bool
    allow_cancel: bool = True


class Faq(Strict):
    topic: str
    answer: str


class BrandConfig(Strict):
    brand_id: str
    display_name: str
    category: str
    agent_name: str
    languages: list[Language] = Field(min_length=1)
    tone: str
    calling_window: CallingWindow
    no_delivery_days: list[Weekday] = []
    max_turns: int = Field(default=12, ge=4, le=20)
    cod: CodPolicy
    ndr: NdrPolicy
    handoff_topics: list[str] = Field(min_length=1)
    faqs: list[Faq] = []
    rules: list[str] = []

    @field_validator("brand_id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", value):
            raise ValueError("brand_id must be lowercase letters, digits and underscores")
        return value

    def allowed_discounts(self) -> set[int]:
        offer = self.cod.prepaid_offer
        return {offer.discount_percent} if offer.enabled and offer.discount_percent else set()

    def name_variants(self) -> list[str]:
        name = self.display_name.lower()
        return sorted({name, name.replace("&", "and"), name.replace(" & ", " n ")})

    def enabled(self, use_case: UseCase) -> bool:
        return (self.cod if use_case == "cod" else self.ndr).enabled


def brand_path(brand_id: str) -> Path:
    return settings.BRANDS_DIR / f"{brand_id}.yaml"


def load_brand(brand_id: str) -> BrandConfig:
    path = brand_path(brand_id)
    if not path.exists():
        raise FileNotFoundError(f"no brand config at brands/{brand_id}.yaml")
    brand = BrandConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if brand.brand_id != brand_id:
        raise ValueError(f"brands/{brand_id}.yaml declares brand_id {brand.brand_id!r}")
    return brand


def all_brands() -> dict[str, BrandConfig]:
    ids = sorted(p.stem for p in settings.BRANDS_DIR.glob("*.yaml") if not p.stem.startswith("_"))
    return {brand_id: load_brand(brand_id) for brand_id in ids}
