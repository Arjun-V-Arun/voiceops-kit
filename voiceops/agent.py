"""Model replies for the agent and the simulated shopper, parsed into validated objects."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from voiceops.llm import LLM, Completion, GenerationError
from voiceops.orders import ActionCall
from voiceops.settings import RoleSettings


class InvalidOutput(RuntimeError):
    """The model did not return valid JSON for its schema, even after one correction."""


class AgentReply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    say: str
    action: ActionCall | None = None
    end_call: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        action = data.get("action")
        if isinstance(action, str):
            data["action"] = {"name": action, "args": {}} if action.strip() else None
        elif isinstance(action, dict):
            if not action.get("name"):
                data["action"] = None
            elif action.get("args") is None:
                data["action"] = {**action, "args": {}}
        if data.get("say") is None:
            data["say"] = ""
        return data


class ShopperReply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    say: str = ""
    hang_up: bool = False


Model = TypeVar("Model", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_json_object(text: str) -> dict[str, Any]:
    """Accept a bare object, a fenced block, or an object with stray text around it."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object found") from None
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


@dataclass
class Asked:
    parsed: Any
    raw: str
    repairs: int
    completions: list[Completion] = field(default_factory=list)


def ask(llm: LLM, role: str, prompt_id: str, role_settings: RoleSettings,
        messages: list[dict[str, str]], schema: type[Model], tag: str) -> Asked:
    """Call the model; if the reply is invalid, say why and ask once more.

    The correction exchange is not kept in the caller's history, so a repaired turn
    costs no extra context on later turns.
    """
    attempt_messages = list(messages)
    completions: list[Completion] = []
    error = ""
    for repair in range(2):
        try:
            completion = llm.complete(role=role, prompt_id=prompt_id, model=role_settings.model,
                                      messages=attempt_messages, temperature=role_settings.temperature,
                                      max_tokens=role_settings.max_tokens, run_tag=tag)
        except GenerationError as exc:
            error = str(exc)
            rejected = [{"role": "assistant", "content": exc.failed_generation}] if exc.failed_generation else []
            attempt_messages = attempt_messages + rejected + [
                {"role": "user", "content": f"[system] Your reply was rejected ({exc.code}). Reply "
                                            "with plain text containing only the JSON object described "
                                            "above, not a tool call."},
            ]
            continue
        completions.append(completion)
        try:
            parsed = schema.model_validate(parse_json_object(completion.text))
            return Asked(parsed, completion.text, repair, completions)
        except (ValueError, ValidationError) as exc:
            error = str(exc).splitlines()[0][:300]
            attempt_messages = attempt_messages + [
                {"role": "assistant", "content": completion.text},
                {"role": "user", "content": f"[system] Your reply was not valid ({error}). "
                                            "Reply again with only the JSON object described above."},
            ]
    raise InvalidOutput(f"{role} output invalid twice: {error}")
