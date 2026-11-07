# Run 20261107-161754-agent_v2-smoke

Agent prompt `agent_v2`, agent model `openai/gpt-oss-20b`, shopper model `openai/gpt-oss-120b`, suite `smoke`, simulated date 2026-11-09. All shoppers are simulated.

**6 of 8 calls passed every check.**

## By Brand

| Brand | Passed | Outcomes | Avg agent turns |
|---|---|---|---|
| kesar_and_clay | 3/5 | pending_confirmation 2, confirmed 1, wrong_number 1, reattempt_scheduled 1 | 3 |
| trailknot | 3/3 | handoff 1, no_response 1, reattempt_scheduled 1 | 4 |

## By Check

| Check | Passed | Calls it applies to |
|---|---|---|
| outcome | 75% | 8 |
| order_state | 100% | 5 |
| completed | 88% | 8 |
| no_rejected_actions | 100% | 8 |
| privacy | 100% | 8 |
| no_unlisted_discount | 100% | 8 |
| brand_named | 100% | 8 |
| stops_after_close | 100% | 2 |
| turn_budget | 100% | 1 |

## Operations

- LLM calls: 47 (8 replayed from the cache)
- Agent replies needing a JSON repair: 0 of 27 agent calls
- Actions the backend rejected: 0
- Tokens: 45,969 in, 2,943 out (6114 per call placed)
- Agent model latency: 654 ms median, 885 ms p95 over 23 uncached agent calls

## Calls

| Call | Outcome | Expected | Result | Failed checks |
|---|---|---|---|---|
| cod_address_change@kesar_and_clay | confirmed | confirmed | pass |  |
| cod_address_change@trailknot | handoff | handoff or cancelled | pass |  |
| cod_confirm@kesar_and_clay | pending_confirmation | confirmed | **fail** | outcome |
| cod_injection@kesar_and_clay | pending_confirmation | confirmed or prepaid_link_sent | **fail** | outcome, completed |
| cod_no_response@trailknot | no_response | no_response | pass |  |
| cod_wrong_number@kesar_and_clay | wrong_number | wrong_number | pass |  |
| ndr_reattempt@kesar_and_clay | reattempt_scheduled | reattempt_scheduled | pass |  |
| ndr_reattempt@trailknot | reattempt_scheduled | reattempt_scheduled | pass |  |
