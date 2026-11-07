"""Sample-call testing: you play the customer and type your replies."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable

from voiceops import prompts, settings
from voiceops.brand import UseCase, load_brand
from voiceops.llm import LLM
from voiceops.orders import CallState
from voiceops.scenarios import load_order
from voiceops.simulate import Usage, agent_turn, asr

HELP = "Type what the customer says. Press Enter on an empty line for silence; type /quit to hang up."


def sample_call(brand_id: str, use_case: UseCase, prompt_id: str, llm: LLM,
                cfg: settings.Settings, project: settings.Project,
                input_fn: Callable[[str], str] = input,
                print_fn: Callable[[str], None] = print) -> dict[str, Any]:
    brand = load_brand(brand_id)
    order = load_order(brand, use_case)
    today = date.fromisoformat(project.today)
    state = CallState(brand=brand, use_case=use_case, order=order, today=today)
    messages = [{"role": "system", "content": prompts.compile_agent_prompt(
        prompt_id, brand, use_case, order, today)}]
    usage, turns = Usage(), []
    print_fn(f"Sample call: {brand.display_name}, {use_case}, prompt {prompt_id}. "
             f"You are {order.customer_name}.\n{HELP}\n")
    for n in range(1, brand.max_turns + 1):
        reply, record, note = agent_turn(llm, cfg, prompt_id, messages, state, n, f"chat#{n}", usage)
        turns.append(record)
        print_fn(f"{brand.agent_name}: {reply.say}")
        for r in record["rejected"]:
            print_fn(f"   [backend rejected {r['action']['name']}: {r['message']}]")
        if record["action"]:
            ok = "ok" if record["action_result"]["ok"] else "rejected"
            args = record["action"]["args"]
            shown = f" {json.dumps(args, ensure_ascii=False)}" if args else ""
            print_fn(f"   [action {record['action']['name']}{shown}: {ok}]")
        if reply.end_call:
            break
        said = input_fn("You: ")
        if said.strip() == "/quit":
            turns.append({"n": n, "speaker": "customer", "text": "", "heard_as": "", "hang_up": True})
            break
        heard = asr(said)
        turns.append({"n": n, "speaker": "customer", "text": said, "heard_as": heard, "hang_up": False})
        content = f"Customer: {heard}" if heard else "Customer: (no response)"
        messages.append({"role": "user", "content": f"{note}\n{content}" if note else content})
    call = {"brand": brand_id, "use_case": use_case, "agent_prompt": prompt_id,
            "model": cfg.agent.model, "today": project.today, "turns": turns,
            "final_status": state.status, "actions": state.log, "usage": usage.as_dict()}
    settings.SAMPLE_CALLS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = settings.SAMPLE_CALLS_DIR / f"{stamp}_{brand_id}_{use_case}.json"
    path.write_text(json.dumps(call, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_fn(f"\nOutcome: {state.status}. Saved to {path.relative_to(settings.ROOT)}")
    return call
