# Design Notes

## Config, Not Prompt Edits, per Brand

A merchant's requirements change often: a new COD fee, an offer switched off, a stricter address policy. If each brand had a hand-written prompt, every change would be a prompt edit, and two brands would drift apart in ways nobody reviewed. Here the merchant's decisions live in `brands/<brand>.yaml`, validated by Pydantic, and one versioned template turns them into instructions. A brand change is a config diff; a behaviour change for every brand is a template diff. The gate treats both as changes that need a new evaluated run.

## Policy Is Enforced Twice

The prompt lists only the actions a brand allows, which guides the model. The backend in `voiceops/orders.py` checks every action again, which is what actually protects the order. If the prompt fails, the backend still refuses to change an address the merchant said cannot change. The `no_rejected_actions` check then reports that the agent tried, so prompt quality is still measured.

## Nothing Is Spoken Before the Action Succeeds

An agent turn is a reply plus at most one action. The engine applies the action first. If the backend rejects it, the reply is discarded, the agent sees the reason, and it gets one retry. Without this, the customer would hear "I've updated your address" for an update that never happened. The discarded reply is kept in the transcript as `unspoken`, so it can still be reviewed.

## Identity Before Details

The agent may not read items, amounts or the address until the customer confirms who they are, and it shares nothing on a wrong-number call. The `privacy` check uses explicit markers per test order (`pii_markers` in `scenarios/orders.yaml`), which keeps the rule reviewable instead of guessed. It also fails an agent that marks identity as verified in its first turn, before the customer has said anything.

## Deterministic Checks First, a Judge Second

Everything that can be checked from the order state and the action log is checked in code: outcome, address, dates, rejected actions, privacy, unlisted discounts, the opening line, stopping after an opt-out. These checks give the same answer every time and are what the gate relies on. Language, brevity and grounding need judgement, so a separate LLM judge scores them. The judge is not part of the gate until hand labels show it agrees with a person.

## Last Words Still Count

A customer who says "yes, confirm it" and hangs up has confirmed, and one who says "stop calling" and hangs up has opted out. A real call is transcribed up to the moment it drops, so the simulation does the same: after a hang-up the agent gets one unspoken turn to act on the customer's last words. The transcript marks it `wrap_up`, the checks treat it as an action but not as speech, and the judge never sees it as spoken text. There is no wrap-up after a silent hang-up or after the call was already closed.

## Simulated Shoppers

An LLM plays the customer from a persona, a goal and a few facts, and its replies are converted to look like a speech-to-text transcript before the agent sees them. Silence is scripted instead, since a model asked to stay silent tends to speak. The shopper uses a different model from the agent, which keeps their free-tier quotas apart and avoids a model grading its own style.

## Reproducible Runs

Every request is cached by its exact content, including model, temperature and seed. Rerunning a suite replays the same conversations, so a diff between two runs shows the effect of the change, not sampling noise. When a prompt changes, only the calls it affects are sent again. A run stopped by the daily limit is resumed by running the same command again.

## What the Gate Fingerprints

- The live agent prompt's text, with line endings normalized so a Windows checkout does not count as a change.
- Each brand config after parsing, so a new comment is not a change but a new fee is.
- The suite: scenarios, test orders, the shopper prompt, the simulated date and the simulation code (`agent.py`, `llm.py`, `orders.py`, `prompts.py`, `simulate.py`), since changing any of them changes what a pass means. `diff` notes when two runs differ here.

`accept` refuses a partial or filtered run, a run whose files no longer match, and a run that breaks calls the previous baseline passed, unless regressions are acknowledged with a note.
