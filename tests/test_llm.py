import json

import httpx
import pytest

from voiceops.llm import LLM, DailyLimitError, LLMError, SetupError, TruncatedError, parse_duration


def ok_body(text="{}", finish="stop"):
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10}}


def make(tmp_path, responses, **kwargs):
    sent, slept = [], []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return queue.pop(0)

    llm = LLM("http://fake/v1", "key", cache_path=tmp_path / "cache.sqlite",
              trace_path=tmp_path / "trace.jsonl", transport=httpx.MockTransport(handler),
              sleep=slept.append, **kwargs)
    return llm, sent, slept


def call(llm, content="hi", model="openai/gpt-oss-20b"):
    return llm.complete(role="agent", prompt_id="p", model=model,
                        messages=[{"role": "user", "content": content}], temperature=0.0,
                        max_tokens=100, run_tag="t")


def test_retries_after_a_rate_limit_using_retry_after(tmp_path):
    llm, sent, slept = make(tmp_path, [
        httpx.Response(429, headers={"retry-after": "2"}, text="rate limit: tokens per minute"),
        httpx.Response(200, json=ok_body('{"say": "hi"}')),
    ])
    result = call(llm)
    assert result.text == '{"say": "hi"}' and result.attempts == 2 and slept == [2.0]


def test_daily_limit_stops_immediately(tmp_path):
    llm, _, slept = make(tmp_path, [httpx.Response(429, text="Limit reached: tokens per day (TPD)")])
    with pytest.raises(DailyLimitError):
        call(llm)
    assert slept == []


def test_cache_replays_identical_requests_without_http(tmp_path):
    llm, sent, _ = make(tmp_path, [httpx.Response(200, json=ok_body('{"a": 1}'))])
    first, second = call(llm), call(llm)
    assert len(sent) == 1 and second.cached and second.text == first.text
    trace = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert [t["cached"] for t in trace] == [False, True] and trace[0]["input_tokens"] == 50


def test_truncated_reply_raises_with_a_hint(tmp_path):
    llm, _, _ = make(tmp_path, [httpx.Response(200, json=ok_body('{"say": "hel', finish="length"))])
    with pytest.raises(TruncatedError, match="MAX_TOKENS"):
        call(llm)


def test_bad_request_mentions_the_setting_to_change(tmp_path):
    llm, _, _ = make(tmp_path, [httpx.Response(400, text="unsupported parameter: reasoning_effort")])
    with pytest.raises(LLMError, match="REASONING_EFFORT"):
        call(llm)


def test_server_errors_are_retried(tmp_path):
    llm, _, slept = make(tmp_path, [httpx.Response(503, text="busy"),
                                    httpx.Response(200, json=ok_body())])
    assert call(llm).attempts == 2 and len(slept) == 1


def test_reasoning_and_json_options_are_sent_to_gpt_oss_only(tmp_path):
    llm, sent, _ = make(tmp_path, [httpx.Response(200, json=ok_body()),
                                   httpx.Response(200, json=ok_body())],
                        reasoning_effort="low", seed=3)
    call(llm, model="openai/gpt-oss-20b")
    call(llm, model="other-model")
    assert sent[0]["reasoning_effort"] == "low" and sent[0]["include_reasoning"] is False
    assert "reasoning_effort" not in sent[1]
    assert sent[0]["response_format"] == {"type": "json_object"} and sent[0]["seed"] == 3


def test_missing_key_fails_clearly(tmp_path):
    llm = LLM("http://fake/v1", "", cache_path=None)
    with pytest.raises(SetupError, match="GROQ_API_KEY"):
        call(llm)


@pytest.mark.parametrize("status, message", [(401, "key was rejected"), (404, "unknown model")])
def test_setup_errors_stop_the_run(tmp_path, status, message):
    llm, _, slept = make(tmp_path, [httpx.Response(status, text="nope")])
    with pytest.raises(SetupError, match=message):
        call(llm)
    assert slept == []


def test_low_remaining_tokens_paces_the_next_request(tmp_path):
    headers = {"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "7.5s"}
    llm, _, slept = make(tmp_path, [httpx.Response(200, json=ok_body(), headers=headers),
                                    httpx.Response(200, json=ok_body())])
    call(llm, content="one")
    call(llm, content="two")
    assert len(slept) == 1 and 0 < slept[0] <= 7.5


@pytest.mark.parametrize("value, seconds", [("2", 2), ("7.5s", 7.5), ("250ms", 0.25),
                                            ("1m30s", 90), (None, 0)])
def test_parse_duration(value, seconds):
    assert parse_duration(value) == pytest.approx(seconds)


def test_rejected_generation_is_reported_with_its_text(tmp_path):
    from voiceops.llm import GenerationError
    body = {"error": {"message": "Tool choice is none, but model called a tool",
                      "code": "tool_use_failed", "failed_generation": '{"name": "assistant"}'}}
    llm, _, slept = make(tmp_path, [httpx.Response(400, json=body)])
    with pytest.raises(GenerationError) as caught:
        call(llm)
    assert caught.value.code == "tool_use_failed"
    assert caught.value.failed_generation == '{"name": "assistant"}' and slept == []
    trace = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert "tool_use_failed" in trace[0]["error"]
