"""OpenAI-compatible chat client with retries, pacing, a response cache and a call log.

Every request is cached by its full content, so rerunning a suite replays earlier answers
for free and a run stopped by the daily limit resumes where it stopped. Every call, cached
or not, is written to a JSONL log with tokens and latency.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx


class LLMError(RuntimeError):
    pass


class FatalLLMError(LLMError):
    """Retrying or moving to the next call cannot help, so the whole run stops."""


class DailyLimitError(FatalLLMError):
    """The provider's daily quota is used up. Rerun later: cached calls replay instantly."""


class GenerationError(LLMError):
    """The provider refused the model's own output, e.g. a reply sent as a tool call.

    The caller treats this like invalid JSON and asks the model once more.
    """

    def __init__(self, message: str, code: str, failed_generation: str):
        super().__init__(message)
        self.code = code
        self.failed_generation = failed_generation


REJECTED_GENERATION_CODES = {"tool_use_failed", "json_validate_failed"}


class SetupError(FatalLLMError):
    """Missing or rejected key, unknown model: fix .env and run again."""


class TruncatedError(LLMError):
    """The reply hit max_tokens before finishing."""


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cached: bool
    attempts: int


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")


def parse_duration(value: str | None) -> float:
    """'1m2.5s' -> 62.5 seconds; '250ms' -> 0.25. Bare numbers are seconds."""
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        pass
    scale = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(n) * scale[unit] for n, unit in _DURATION.findall(value))


def _rejected_generation(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return "", ""
    return str(error.get("code") or ""), str(error.get("failed_generation") or "")


class Cache:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS responses "
                           "(key TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)")

    def get(self, key: str) -> dict[str, Any] | None:
        if self.path is None:
            return None
        with self._lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT body FROM responses WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, body: dict[str, Any]) -> None:
        if self.path is None:
            return
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
                       (key, json.dumps(body), time.time()))


class LLM:
    def __init__(self, base_url: str, api_key: str, *, cache_path: Path | None = None,
                 trace_path: Path | None = None, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_attempts: int = 6,
                 timeout: float = 60.0, reasoning_effort: str | None = None,
                 json_mode: bool = True, seed: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.cache = Cache(cache_path)
        self.trace_path = trace_path
        self.sleep = sleep
        self.max_attempts = max_attempts
        self.reasoning_effort = reasoning_effort
        self.json_mode = json_mode
        self.seed = seed
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._pace: dict[str, float] = {}  # model -> earliest time to send the next request

    # --- public --------------------------------------------------------------------------

    def complete(self, *, role: str, prompt_id: str, model: str, messages: list[dict[str, str]],
                 temperature: float, max_tokens: int, run_tag: str = "") -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.reasoning_effort and "gpt-oss" in model:
            payload["reasoning_effort"] = self.reasoning_effort
            payload["include_reasoning"] = False
        if self.seed is not None:
            payload["seed"] = self.seed
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

        cached = self.cache.get(key)
        if cached is not None:
            result = Completion(cached["text"], model, cached["input_tokens"],
                                cached["output_tokens"], 0, True, 0)
            self._trace(role, prompt_id, run_tag, result, None)
            return result

        if not self.api_key:
            raise SetupError("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
        try:
            result = self._send(payload)
        except LLMError as exc:
            self._trace(role, prompt_id, run_tag, None, str(exc), model=model)
            raise
        self.cache.put(key, {"text": result.text, "input_tokens": result.input_tokens,
                             "output_tokens": result.output_tokens})
        self._trace(role, prompt_id, run_tag, result, None)
        return result

    def close(self) -> None:
        self._client.close()

    # --- internals -----------------------------------------------------------------------

    def _send(self, payload: dict[str, Any]) -> Completion:
        model = payload["model"]
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error = "no attempt made"
        for attempt in range(1, self.max_attempts + 1):
            wait = self._pace.get(model, 0.0) - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            started = time.monotonic()
            try:
                response = self._client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
                self.sleep(min(2 ** attempt, 30))
                continue
            latency_ms = int((time.monotonic() - started) * 1000)
            self._update_pace(model, response.headers)

            if response.status_code == 200:
                body = response.json()
                choice = body["choices"][0]
                text = choice["message"].get("content") or ""
                if choice.get("finish_reason") == "length":
                    raise TruncatedError(
                        f"{model} hit max_completion_tokens={payload['max_completion_tokens']} "
                        "before finishing. Reasoning tokens count too: raise the matching "
                        "*_MAX_TOKENS in .env.")
                usage = body.get("usage") or {}
                return Completion(text, model, int(usage.get("prompt_tokens", 0)),
                                  int(usage.get("completion_tokens", 0)), latency_ms, False, attempt)

            detail = response.text[:500]
            if response.status_code == 429:
                if re.search(r"per day|\bTPD\b|\bRPD\b", detail, re.IGNORECASE):
                    raise DailyLimitError(f"{model}: daily limit reached. Rerun the same command "
                                          f"later; finished calls are cached. Provider said: {detail}")
                delay = parse_duration(response.headers.get("retry-after")) or min(2 ** attempt, 60)
                last_error = f"rate limited: {detail}"
                self.sleep(delay)
                continue
            if response.status_code >= 500:
                last_error = f"server error {response.status_code}: {detail}"
                self.sleep(min(2 ** attempt, 30))
                continue
            if response.status_code in (401, 403):
                raise SetupError(f"{model}: HTTP {response.status_code}: the API key was rejected. "
                                 f"Check GROQ_API_KEY in .env. Provider said: {detail}")
            if response.status_code == 404:
                raise SetupError(f"{model}: HTTP 404: unknown model or endpoint. Check the *_MODEL "
                                 f"and LLM_BASE_URL settings in .env. Provider said: {detail}")
            if response.status_code == 400:
                code, generation = _rejected_generation(response)
                if code in REJECTED_GENERATION_CODES:
                    raise GenerationError(f"{model}: provider rejected the reply ({code})",
                                          code, generation)
            hint = ""
            if response.status_code == 400 and any(word in detail for word in
                                                     ("reasoning", "response_format", "seed")):
                hint = (" If the model does not accept that parameter, leave REASONING_EFFORT or "
                        "LLM_SEED empty, or set LLM_JSON_MODE=0, in .env.")
            raise LLMError(f"{model}: HTTP {response.status_code}: {detail}{hint}")
        raise LLMError(f"{model}: gave up after {self.max_attempts} attempts ({last_error})")

    def _update_pace(self, model: str, headers: httpx.Headers) -> None:
        """Wait out the per-minute token window instead of collecting 429s."""
        remaining = headers.get("x-ratelimit-remaining-tokens")
        reset = parse_duration(headers.get("x-ratelimit-reset-tokens"))
        if remaining is not None and reset:
            try:
                if int(float(remaining)) < 2500:
                    self._pace[model] = time.monotonic() + reset
            except ValueError:
                pass

    def _trace(self, role: str, prompt_id: str, run_tag: str, result: Completion | None,
               error: str | None, model: str | None = None) -> None:
        if self.trace_path is None:
            return
        record = {
            "ts": round(time.time(), 3), "role": role, "prompt_id": prompt_id, "tag": run_tag,
            "model": result.model if result else model,
            "input_tokens": result.input_tokens if result else 0,
            "output_tokens": result.output_tokens if result else 0,
            "latency_ms": result.latency_ms if result else None,
            "cached": result.cached if result else False,
            "attempts": result.attempts if result else None,
            "error": error,
        }
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
