# Brand Onboarding

A checklist for taking a new brand from first conversation to live calls. Copy the tracker at the bottom into the brand's ticket and keep it updated.

## 1. Merchant Intake

Ask these, and write the answers straight into `brands/<brand>.yaml` (`python -m voiceops new-brand <brand>` creates it from the template).

| Question | Config field |
|---|---|
| What should the agent call itself, and how should it sound? | `agent_name`, `tone` |
| Which languages do your customers speak? | `languages` |
| What hours may we call? | `calling_window` |
| Which days does your courier not deliver? | `no_delivery_days` |
| Is there a cash-on-delivery fee? How much? | `cod.cod_fee` |
| Can the agent offer a pay-online link? With what discount? | `cod.prepaid_offer` |
| Can customers cancel on the call? | `cod.allow_cancel`, `ndr.allow_cancel` |
| Can the agent correct an address before dispatch? After a failed delivery? | `cod.allow_address_change`, `ndr.allow_address_change` |
| How many days ahead can a failed delivery be rebooked? | `ndr.max_reattempt_days` |
| What must always go to a human? | `handoff_topics` |
| Which questions should the agent answer, and with what exact answer? | `faqs` |
| Anything the agent must never say? | `rules` |

Anything the merchant cannot answer yet stays at the template's safe default: no address changes, no offers, hand off when unsure.

## 2. Configure and Validate

- [ ] Fill the config and run `python -m voiceops validate`. Done when it reports no problems.
- [ ] Read the compiled prompt: `python -m voiceops show-prompt <brand> cod` and `... ndr`. Done when every policy line matches what the merchant said.
- [ ] Add one COD and one NDR test order to `scenarios/orders.yaml`, with `pii_markers` for the item names, amounts and the distinctive part of the address.
- [ ] Add the brand to every scenario in `scenarios/*.yaml` that applies, with its own `expect` where its policy changes the right outcome.

## 3. Sample Calls

Run `python -m voiceops chat <brand> cod` and play the customer for each of these. Transcripts are saved in `evals/sample_calls/`.

- [ ] Confirms straight away
- [ ] Asks about the fee or the delivery time
- [ ] Wants to change the address
- [ ] Wants to cancel
- [ ] Says it is the wrong number
- [ ] Asks for a discount
- [ ] Raises one of the hand-off topics
- [ ] Speaks Hindi or Hinglish, if the brand allows it

Share two or three transcripts with the merchant and get their sign-off on wording before going further.

## 4. Evaluate and Accept

- [ ] `python -m voiceops run --suite full`. Done when the report shows no failures you cannot explain.
- [ ] Log every failure and its fix in `docs/FAILURES.md`.
- [ ] `python -m voiceops accept <run>`. Done when it prints "Accepted".
- [ ] `python -m voiceops gate` passes locally, then push. Done when CI is green.

## 5. Go Live and Monitor

- [ ] Start with a share of the brand's orders, not all of them.
- [ ] Review outcomes daily for the first week: confirmation, cancellation, hand-off and no-response rates, plus any call the backend rejected an action on.
- [ ] Every complaint or odd call becomes a scenario, so it is tested on every later change.

## Tracker

| Step | Owner | Status | Date | Notes |
|---|---|---|---|---|
| Intake call |  |  |  |  |
| Config validated |  |  |  |  |
| Test orders and scenarios added |  |  |  |  |
| Sample calls reviewed with merchant |  |  |  |  |
| Full run accepted |  |  |  |  |
| CI green |  |  |  |  |
| Live on a share of orders |  |  |  |  |
| Week-one review |  |  |  |  |
