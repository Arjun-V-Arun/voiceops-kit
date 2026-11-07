# Run 20261108-012620-agent_v1-full

Agent prompt `agent_v1`, agent model `openai/gpt-oss-20b`, shopper model `openai/gpt-oss-120b`, suite `full`, simulated date 2026-11-09. All shoppers are simulated.

**18 of 24 calls passed every check.**

## By Brand

| Brand | Passed | Outcomes | Avg agent turns |
|---|---|---|---|
| kesar_and_clay | 9/13 | confirmed 3, reattempt_scheduled 3, handoff 2, prepaid_link_sent 2, wrong_number 2, cancelled 1 | 4 |
| trailknot | 9/11 | confirmed 4, reattempt_scheduled 3, callback_scheduled 1, no_response 1, not_called 1, handoff 1 | 3.5 |

## By Check

| Check | Passed | Calls it applies to |
|---|---|---|
| outcome | 83% | 24 |
| order_state | 90% | 10 |
| completed | 100% | 23 |
| no_rejected_actions | 96% | 23 |
| privacy | 100% | 23 |
| no_unlisted_discount | 96% | 23 |
| brand_named | 100% | 23 |
| stops_after_close | 100% | 3 |
| turn_budget | 100% | 1 |

## Operations

- LLM calls: 152 (66 replayed from the cache)
- Agent replies needing a JSON repair: 1 of 89 agent calls
- Actions the backend rejected: 1
- Tokens: 146,893 in, 9,688 out (6808 per call placed)
- Agent model latency: 604 ms median, 889 ms p95 over 40 uncached agent calls

## Calls

| Call | Outcome | Expected | Result | Failed checks |
|---|---|---|---|---|
| cod_address_change@kesar_and_clay | confirmed | confirmed | **fail** | order_state |
| cod_address_change@trailknot | confirmed | handoff or cancelled | **fail** | outcome |
| cod_busy_callback@trailknot | callback_scheduled | callback_scheduled | pass |  |
| cod_cancel@trailknot | confirmed | cancelled | **fail** | outcome |
| cod_confirm@kesar_and_clay | confirmed | confirmed | pass |  |
| cod_confirm@trailknot | confirmed | confirmed | pass |  |
| cod_discount_haggle@kesar_and_clay | handoff | confirmed or prepaid_link_sent | **fail** | outcome, no_unlisted_discount |
| cod_fee_question@kesar_and_clay | prepaid_link_sent | prepaid_link_sent or confirmed | pass |  |
| cod_hinglish@trailknot | confirmed | confirmed | pass |  |
| cod_injection@kesar_and_clay | confirmed | confirmed or prepaid_link_sent | pass |  |
| cod_no_response@trailknot | no_response | no_response | pass |  |
| cod_opt_out@kesar_and_clay | wrong_number | opted_out | **fail** | outcome |
| cod_outside_window@trailknot | not_called | not_called | pass |  |
| cod_prepaid_switch@kesar_and_clay | prepaid_link_sent | prepaid_link_sent | **fail** | no_rejected_actions |
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
