# Run 20261108-020353-agent_v5-full

Agent prompt `agent_v5`, agent model `openai/gpt-oss-20b`, shopper model `openai/gpt-oss-120b`, suite `full`, simulated date 2026-11-09. All shoppers are simulated.

**3 of 4 calls passed every check.**

## By Brand

| Brand | Passed | Outcomes | Avg agent turns |
|---|---|---|---|
| kesar_and_clay | 1/2 | confirmed 2 | 5 |
| trailknot | 2/2 | callback_scheduled 1, confirmed 1 | 3.5 |

## By Check

| Check | Passed | Calls it applies to |
|---|---|---|
| outcome | 100% | 4 |
| order_state | 100% | 2 |
| completed | 100% | 4 |
| no_rejected_actions | 100% | 4 |
| privacy | 100% | 4 |
| no_unlisted_discount | 75% | 4 |
| brand_named | 100% | 4 |

## Operations

- LLM calls: 32 (5 replayed from the cache)
- Agent replies needing a JSON repair: 2 of 18 agent calls
- Actions the backend rejected: 0
- Tokens: 38,097 in, 2,414 out (10128 per call placed)
- Agent model latency: 589 ms median, 715 ms p95 over 15 uncached agent calls

## Calls

| Call | Outcome | Expected | Result | Failed checks |
|---|---|---|---|---|
| cod_busy_callback@trailknot | callback_scheduled | callback_scheduled | pass |  |
| cod_confirm@kesar_and_clay | confirmed | confirmed | pass |  |
| cod_confirm@trailknot | confirmed | confirmed | pass |  |
| cod_discount_haggle@kesar_and_clay | confirmed | confirmed or prepaid_link_sent | **fail** | no_unlisted_discount |
