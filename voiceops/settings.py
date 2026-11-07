"""Paths, environment settings and the project file."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BRANDS_DIR = ROOT / "brands"
PROMPTS_DIR = ROOT / "prompts"
SCENARIOS_DIR = ROOT / "scenarios"
EVALS_DIR = ROOT / "evals"
RUNS_DIR = EVALS_DIR / "runs"
SAMPLE_CALLS_DIR = EVALS_DIR / "sample_calls"
CACHE_PATH = EVALS_DIR / "cache.sqlite"
ACCEPTED_PATH = EVALS_DIR / "accepted.json"
PROJECT_FILE = ROOT / "voiceops.yaml"
README = ROOT / "README.md"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def load_dotenv(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env without overriding real environment variables."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class RoleSettings:
    model: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    agent: RoleSettings
    shopper: RoleSettings
    judge: RoleSettings
    reasoning_effort: str | None
    json_mode: bool
    seed: int | None


def from_env() -> Settings:
    load_dotenv()
    env = os.environ.get

    def role(prefix: str, model: str, temperature: float, max_tokens: int) -> RoleSettings:
        return RoleSettings(
            model=env(f"{prefix}_MODEL", model),
            temperature=float(env(f"{prefix}_TEMPERATURE", temperature)),
            max_tokens=int(env(f"{prefix}_MAX_TOKENS", max_tokens)),
        )

    effort = env("REASONING_EFFORT", "low").strip()
    return Settings(
        base_url=env("LLM_BASE_URL", GROQ_BASE_URL).rstrip("/"),
        api_key=env("GROQ_API_KEY", ""),
        # Separate models for agent and shopper keep their free-tier quotas apart.
        agent=role("AGENT", "openai/gpt-oss-20b", 0.3, 900),
        shopper=role("SHOPPER", "openai/gpt-oss-120b", 0.8, 600),
        judge=role("JUDGE", "openai/gpt-oss-120b", 0.0, 1500),
        reasoning_effort=effort or None,
        json_mode=env("LLM_JSON_MODE", "1") != "0",
        seed=int(env("LLM_SEED", "7")) if env("LLM_SEED", "7").strip() else None,
    )


@dataclass(frozen=True)
class Project:
    agent_prompt: str
    shopper_prompt: str
    judge_prompt: str
    today: str


def load_project(path: Path | None = None) -> Project:
    data = yaml.safe_load((path or PROJECT_FILE).read_text(encoding="utf-8"))
    return Project(
        agent_prompt=data["agent_prompt"],
        shopper_prompt=data["shopper_prompt"],
        judge_prompt=data["judge_prompt"],
        today=str(data["today"]),
    )


def set_agent_prompt(prompt_id: str, path: Path | None = None) -> None:
    """Promote a prompt version. Edits one line so the file's comments survive."""
    path = path or PROJECT_FILE
    text = path.read_text(encoding="utf-8")
    new, count = re.subn(r"(?m)^agent_prompt:.*$", f"agent_prompt: {prompt_id}", text)
    if count != 1:
        raise ValueError(f"expected one agent_prompt line in {path.name}")
    path.write_text(new, encoding="utf-8")
