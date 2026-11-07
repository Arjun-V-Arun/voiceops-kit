# Runbook

What to do when something goes wrong. Find the symptom, confirm the cause with the command or file given, then apply the fix. Add anything new to the table.

## Call Quality

| Symptom | How to confirm | Likely cause | Fix |
|---|---|---|---|
| `privacy` fails | Read the transcript's first agent turns | Agent mentions the order before the customer confirms identity, or marks identity verified in its first turn | Tighten step 1 and 2 of the call flow in the template; add an example of the opening line |
| `no_rejected_actions` fails | The turn's `rejected` list in the transcript | Agent tried something the brand forbids, often an address change | Check `show-prompt` lists the right actions; make the policy line explicit about what to offer instead |
| Wrong `outcome` | Final status and action log in the transcript | Agent took an action without the customer agreeing, or never took one | Look for the turn where the customer agreed; reinforce "act only after the customer agrees" or the step that was skipped |
| `order_state` fails on a reattempt date | `reattempt_date` and the backend's rejection message | Date outside the window, a no-delivery day, or the wrong year | Make sure the compiled prompt lists the allowed dates; check `today` in `voiceops.yaml` |
| `no_unlisted_discount` fails | Search the transcript for "%" or "off" | Agent improvised a discount, often under pressure or a prompt injection | Strengthen the "only offer" policy line; add the phrasing that broke it as a scenario |
| `stops_after_close` fails | Turns after the opt-out or wrong-number action | Agent kept selling after the customer said stop | Say explicitly that the call ends in the same turn |
| Judge says `language_match` fails | Judge evidence in `judge.json` | Agent switched back to English or ignored Hinglish | Strengthen the language rule; check the brand allows Hindi |
| Same scenario passes and fails across runs | `diff` between two runs with the same prompt | Shopper randomness at temperature 0.8 | Lower `SHOPPER_TEMPERATURE` for that investigation, or make the persona's goal more specific |

## Model and Provider

| Symptom | How to confirm | Likely cause | Fix |
|---|---|---|---|
| `Stopped: ... daily limit reached` | The message names the model | Free-tier daily tokens or requests used up | Run the same command after the reset; finished calls replay from the cache |
| Many `rate limited` retries, slow runs | `attempts` above 1 in `trace.jsonl` | Tokens-per-minute limit | Nothing; the client waits. Shorter prompts reduce it |
| `hit max_completion_tokens` | The error text | Reasoning tokens used up the reply budget | Raise `AGENT_MAX_TOKENS` or `SHOPPER_MAX_TOKENS` in `.env`, or set `REASONING_EFFORT=low` |
| `HTTP 400` mentioning reasoning or response_format | The error text | Model does not accept the parameter | Set `REASONING_EFFORT=` (empty) or `LLM_JSON_MODE=0` |
| `HTTP 400` with `tool_use_failed` or `json_validate_failed` in `trace.jsonl` | The `error` field of the trace line | The model sent its reply as a tool call, or as invalid JSON in JSON mode | Handled automatically as one repair. If a call still ends in an error, read `failed_generation` in the transcript's `error` |
| JSON repairs rising in the report | `Agent replies needing a JSON repair` | Model changed, or the template's JSON instructions were edited | Compare with the last accepted run; restore the reply-format section |
| `completed` fails with "invalid twice" | The transcript's `error` | Two invalid replies in a row | As above; if one scenario triggers it, read what the model sent in the run's trace |

## Gate and CI

| Symptom | How to confirm | Likely cause | Fix |
|---|---|---|---|
| CI gate fails: "changed after the run" | `python -m voiceops gate` locally | A prompt, brand config or scenario was edited without a new evaluated run | Run the full suite, review the diff, `accept` it, commit the run folder |
| `accept` refuses: "calls passed ... and fail now" | `python -m voiceops diff <accepted> <run>` | The change fixed some calls and broke others | Fix the regressions, or accept with `--allow-regressions --note "<why>"` |
| `diff` prints "these runs used different scenarios, shopper prompt or simulation code" | `meta.json` of both runs | Something other than the agent prompt changed between the runs | Rerun the older prompt on the current code before comparing |
| `accept` refuses: "partial suite" | `meta.json` in the run folder | The run used `--suite smoke`, `--brand` or `--scenario` | Run `--suite full` without filters |
| CI gate fails: "README results do not match" | `gate` output | README edited by hand, or accept run on another branch | `python -m voiceops publish <accepted run>` |

## Calls That Should Never Happen

The pre-call guard refuses calls outside the brand's `calling_window` and to numbers on the do-not-call list. If a call outside hours ever appears, check the brand's window first, then `can_place_call` in `voiceops/orders.py`.
