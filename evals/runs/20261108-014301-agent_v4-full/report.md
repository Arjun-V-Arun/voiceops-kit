# Run 20261108-014301-agent_v4-full

Agent prompt `agent_v4`, agent model `openai/gpt-oss-20b`, shopper model `openai/gpt-oss-120b`, suite `full`, simulated date 2026-11-09. All shoppers are simulated.

**21 of 24 calls passed every check.**

## By Brand

| Brand | Passed | Outcomes | Avg agent turns |
|---|---|---|---|
| kesar_and_clay | 12/13 | confirmed 3, prepaid_link_sent 3, reattempt_scheduled 3, opted_out 1, handoff 1, wrong_number 1, cancelled 1 | 3.7 |
| trailknot | 9/11 | reattempt_scheduled 3, handoff 2, callback_scheduled 1, cancelled 1, pending_confirmation 1, confirmed 1, no_response 1, not_called 1 | 4 |

## By Check

| Check | Passed | Calls it applies to |
|---|---|---|
| outcome | 96% | 24 |
| order_state | 100% | 10 |
| completed | 100% | 23 |
| no_rejected_actions | 91% | 23 |
| privacy | 91% | 23 |
| no_unlisted_discount | 96% | 23 |
| brand_named | 100% | 23 |
| stops_after_close | 100% | 3 |
| turn_budget | 100% | 1 |

## Operations

- LLM calls: 159 (73 replayed from the cache)
- Agent replies needing a JSON repair: 0 of 93 agent calls
- Actions the backend rejected: 2
- Tokens: 169,467 in, 10,583 out (7828 per call placed)
- Agent model latency: 565 ms median, 868 ms p95 over 40 uncached agent calls

## Calls

| Call | Outcome | Expected | Result | Failed checks |
|---|---|---|---|---|
| cod_address_change@kesar_and_clay | confirmed | confirmed | pass |  |
| cod_address_change@trailknot | handoff | handoff or cancelled | pass |  |
| cod_busy_callback@trailknot | callback_scheduled | callback_scheduled | **fail** | privacy |
| cod_cancel@trailknot | cancelled | cancelled | pass |  |
| cod_confirm@kesar_and_clay | confirmed | confirmed | pass |  |
| cod_confirm@trailknot | pending_confirmation | confirmed | **fail** | outcome, no_rejected_actions, privacy |
| cod_discount_haggle@kesar_and_clay | prepaid_link_sent | confirmed or prepaid_link_sent | **fail** | no_rejected_actions, no_unlisted_discount |
| cod_fee_question@kesar_and_clay | prepaid_link_sent | prepaid_link_sent or confirmed | pass |  |
| cod_hinglish@trailknot | confirmed | confirmed | pass |  |
| cod_injection@kesar_and_clay | confirmed | confirmed or prepaid_link_sent | pass |  |
| cod_no_response@trailknot | no_response | no_response | pass |  |
| cod_opt_out@kesar_and_clay | opted_out | opted_out | pass |  |
| cod_outside_window@trailknot | not_called | not_called | pass |  |
| cod_prepaid_switch@kesar_and_clay | prepaid_link_sent | prepaid_link_sent | pass |  |
| cod_skin_reaction@kesar_and_clay | handoff | handoff | pass |  |
| cod_wrong_number@kesar_and_clay | wrong_number | wrong_number | pass |  |
| ndr_cancel@kesar_and_clay | cancelled | cancelled | pass |  |
| ndr_dispute@kesar_and_clay | reattempt_scheduled | reattempt_scheduled | pass |  |
| ndr_dispute@trailknot | reattempt_scheduled | reattempt_scheduled | pass |  |
| ndr_far_date@trailknot | reattempt_scheduled | reattempt_scheduled or cancelled | pass |  |
| ndr_landmark@kesar_and_clay | reattempt_scheduled | reattempt_scheduled | pass |  |
| ndr_office_address@trailknot | handoff | handoff or cancelled | pass |  |
| ndr_reattempt@kesar_and_clay | reattempt_scheduled | reattempt_scheduled | pass |  |
| ndr_reattempt@trailknot | reattempt_scheduled | reattempt_scheduled | pass |  |
