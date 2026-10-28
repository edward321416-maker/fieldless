"""The authoring agent (plan 5): Muse Spark tests the survey items it drafted from a rule by calling SAM 3.1 and
itself through two tools on labelled photos, revises them and calls finish. Code scores every test; the agent only
chooses what to try next. Limits keep it from running on: MAX_TOOL_CALLS tool calls, and the run's cost meter.

The same loop runs the inventory agent (run_inventory), which tunes one item that records where a thing is."""
import json

from assist.items import UNCLEAR, with_unclear
from pipeline.cost import CostCapReached

MAX_TOOL_CALLS = 20

TOOLS = [
    {"type": "function", "name": "score_find", "strict": True,
     "description": "Run SAM 3.1 with a text phrase on every test photo. Returns how many photos that show the "
                    "target it found, how many photos without the target it wrongly fired on, and the ids it missed.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["phrase"],
                    "properties": {"phrase": {"type": "string"}}}},
    {"type": "function", "name": "score_ask", "strict": True,
     "description": "Ask Muse Spark a yes/no question about the target on each test photo where SAM found it with "
                    "`find`, and score the answers against the labelled question for this item's own rule section. "
                    "polarity is 'same' if a yes to your question means a yes to that labelled question, otherwise "
                    "'opposite'. Fails if no labelled question covers the item's section.",
     "parameters": {"type": "object", "additionalProperties": False,
                    "required": ["item_id", "find", "ask", "polarity"],
                    "properties": {"item_id": {"type": "string"}, "find": {"type": "string"},
                                   "ask": {"type": "string"},
                                   "polarity": {"type": "string", "enum": ["same", "opposite"]}}}},
    {"type": "function", "name": "finish", "strict": True,
     "description": "Finish with the final version of every drafted item, in the drafted order, ids unchanged.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["items"],
                    "properties": {"items": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["id", "find", "ask", "polarity", "tested", "note"],
                        "properties": {"id": {"type": "string"}, "find": {"type": "string"},
                                       "ask": {"type": "string"},
                                       "polarity": {"type": "string", "enum": ["same", "opposite"]},
                                       "tested": {"type": "boolean"}, "note": {"type": "string"}}}}}}},
]

PROMPT = """You turn drafted survey items into items that work. Each item has `find`, a phrase SAM 3.1 segments in a street photo, and `ask`, a yes/no question Muse Spark answers about one photo of it.
Test set "{name}": {count} labelled street photos about the target "{target}". Labelled questions, each covering the rule sections listed:
{questions}
Drafted items:
{items}
For each drafted item whose rule section is covered by a labelled question:
1) score_find with its find phrase. If SAM misses many photos that show the target, try one or two other short phrases.
2) score_ask with the item id, the best phrase, its ask and the polarity.
3) If answers are wrong, reword the ask to say more clearly what to look at, and test again. Revise an item at most 3 times.
Do not change what an item checks: the reworded ask must still test the item's own requirement and section. An item whose section no labelled question covers cannot be tested here: return it as drafted with tested=false.
You have at most {max_calls} tool calls in total. Then call finish with every item."""


def _dispatch(bench, name: str, args: dict) -> dict:
    if name == "score_find":
        return bench.find(args["phrase"])
    if name == "score_ask":
        return bench.ask(args["item_id"], args["find"], args["ask"], args["polarity"])
    return {"error": f"unknown tool {name}"}


def _short(text: str, width: int = 70) -> str:
    return text if len(text) <= width else text[:width].rstrip() + "..."


def describe(step: int, name: str, args: dict, result: dict | None) -> str:
    """One line for a person watching the agent work: what a tool call tried and what code measured. It never raises,
    so showing a step cannot stop a paid run."""
    r = result or {}
    if name == "finish":
        if "item" in args:
            return f'Muse Spark finished after {step} tool calls: find "{(args["item"] or {}).get("find", "")}"'
        items = args.get("items") or []
        tested = [str(i.get("id")) for i in items if i.get("tested")]
        return (f"Muse Spark finished after {step} tool calls: {len(items)} items, {len(tested)} tested"
                + (f" ({', '.join(tested)})" if tested else ""))
    if "error" in r:
        return f"[{step}] {name}: {r['error']}"
    find, ask = args.get("find", ""), _short(str(args.get("ask", "")))
    if name == "score_find":
        return (f'[{step}] SAM 3.1 finds "{args.get("phrase", "")}": {r.get("found_visible", "?")} of '
                f'{r.get("total_visible", "?")} photos that show it, {r.get("false_on_negatives", "?")} of '
                f'{r.get("total_negatives", "?")} that do not')
    if name == "score_ask":
        return (f'[{step}] Muse Spark asks "{ask}" on "{find}": right on {r.get("right", "?")} of '
                f'{r.get("decidable", "?")}, wrong {len(r.get("wrong") or [])}, unclear {r.get("unclear", "?")}')
    if name == "score_confirm":
        return (f'[{step}] Muse Spark checks "{find}" with "{ask}": kept {r.get("kept_true", "?")} of '
                f'{r.get("found_visible", "?")} real ones, dropped {r.get("rejected_wrongly", "?")} wrongly, '
                f'kept {r.get("kept_false", "?")} that were not')
    return f"[{step}] {name}"


def _loop(respond, text: str, dispatch, max_calls: int, on_step=None) -> dict:
    """Turns until the agent calls finish: {"finished": bool, "args": finish's arguments ({} if it never finished),
    "calls": tool calls made, "stopped": why it stopped without finishing, or ""}. dispatch(name, args) runs a tool.
    on_step(step, name, args, result), if given, hears every tool call as it returns, then the finish (result None)."""
    history = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}]
    calls = 0
    for _ in range(max_calls + 5):  # turns; a turn may call several tools
        try:
            turn = respond(history)
        except CostCapReached as exc:
            return {"finished": False, "args": {}, "calls": calls, "stopped": f"cost cap: {exc}"}
        history += turn["output"]
        fcalls = [o for o in turn["output"] if o.get("type") == "function_call"]
        if not fcalls:
            return {"finished": False, "args": {}, "calls": calls, "stopped": "answered without calling finish"}
        for c in fcalls:
            args = json.loads(c.get("arguments") or "{}")
            if c["name"] == "finish":
                if on_step:
                    on_step(calls, "finish", args, None)
                return {"finished": True, "args": args, "calls": calls, "stopped": ""}
            calls += 1
            if calls > max_calls:
                result = {"error": "tool call limit reached: call finish now"}
            else:
                try:
                    result = dispatch(c["name"], args)
                except CostCapReached as exc:
                    return {"finished": False, "args": {}, "calls": calls, "stopped": f"cost cap: {exc}"}
                except (KeyError, ValueError) as exc:
                    result = {"error": str(exc)}
            if on_step:
                on_step(calls, c["name"], args, result)
            history.append({"type": "function_call_output", "call_id": c["call_id"], "output": json.dumps(result)})
    return {"finished": False, "args": {}, "calls": calls, "stopped": "turn limit"}


def run(respond, bench, drafts: list[dict], max_calls: int = MAX_TOOL_CALLS, on_step=None) -> dict:
    """{"finished": bool, "items": finish's items ([] if it never finished), "calls": tool calls made,
    "stopped": why it stopped without finishing, or ""}."""
    ts = bench.ts
    questions = "\n".join(f'- "{k}" (sections {", ".join(q.sections)}): a yes means: {q.means}'
                          for k, q in ts.questions.items())
    shown = [{k: d[k] for k in ("id", "section", "target", "find", "ask", "report_when")} for d in drafts]
    text = PROMPT.format(name=ts.name, count=len(ts.photos), target=ts.target, questions=questions,
                         items=json.dumps(shown, ensure_ascii=False, indent=1), max_calls=max_calls)
    r = _loop(respond, text, lambda name, args: _dispatch(bench, name, args), max_calls, on_step)
    return {"finished": r["finished"], "items": r["args"].get("items", []), "calls": r["calls"],
            "stopped": r["stopped"]}


# The inventory agent: one item that records where a thing is (a streetlight pole, a street tree). There is no defect
# question to score, so the agent picks the SAM phrase and tests that Spark confirms what SAM found. Its own tool
# list, so the check agent's tools and prompt stay as they are.

MAX_INVENTORY_CALLS = 12
MAX_REWORDS = 3  # rewordings of the drafted ask; code refuses a further one

INVENTORY_TOOLS = [
    TOOLS[0],  # score_find
    {"type": "function", "name": "score_confirm", "strict": True,
     "description": "Run SAM 3.1 with `find` on every test photo, then ask Muse Spark `ask` on each photo where SAM "
                    "found something, with and without the target. Returns, of the photos showing the target, how "
                    "many Spark confirmed (yes), rejected wrongly (no) or found unclear; of the photos without it "
                    "where SAM fired, how many Spark rejected (no, right) or kept (yes, wrong); and the wrong photo ids "
                    "with Spark's note.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["find", "ask"],
                    "properties": {"find": {"type": "string"}, "ask": {"type": "string"}}}},
    {"type": "function", "name": "finish", "strict": True,
     "description": "Finish with the find phrase and the ask to keep, and a one-sentence note on why.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["item"],
                    "properties": {"item": {
                        "type": "object", "additionalProperties": False, "required": ["find", "ask", "note"],
                        "properties": {"find": {"type": "string"}, "ask": {"type": "string"},
                                       "note": {"type": "string"}}}}}},
]

INVENTORY_PROMPT = """You make one inventory survey item work. A surveyor records where every {target} is. SAM 3.1 segments `find`, a short phrase, in each street photo; on each photo where it finds something, Muse Spark answers `ask` about that photo: "yes" records a {target}, "no" drops the find as something else, "unclear" sends it to a person.
Test set "{name}": {visible} labelled street photos that show a {target} and {negatives} that do not.
Drafted item:
{item}
Other phrases to try for find: {alternatives}
1) score_find with the drafted phrase and the other phrases. Keep the phrase with the highest found_visible minus false_on_negatives.
2) score_confirm with that phrase and the ask.
3) If Spark rejects real ones, keeps things that are not one, or answers unclear on clear photos, reword the ask to say better what a {target} is and which look-alikes are not, and test again. Keep it a definition naming the look-alikes, then the question, then: "{unclear}". Reword the ask at most {rewords} times.
You have at most {max_calls} tool calls in total. Then call finish with the find and the ask that worked best."""


def _inventory_dispatch(bench, item: dict, max_rewords: int):
    """dispatch for _loop. Every ask tested ends with the unclear sentence, and code refuses more than max_rewords
    new asks, so the limit holds whatever the agent does."""
    asks = [with_unclear(item["ask"])]

    def dispatch(name: str, args: dict) -> dict:
        if name == "score_find":
            return bench.find(args["phrase"])
        if name == "score_confirm":
            ask = with_unclear(args["ask"])
            if ask not in asks:
                if len(asks) > max_rewords:
                    return {"error": f"the ask was reworded {max_rewords} times already: call finish"}
                asks.append(ask)
            return bench.confirm(item["id"], args["find"], ask)
        return {"error": f"unknown tool {name}"}

    return dispatch


def run_inventory(respond, bench, item: dict, max_calls: int = MAX_INVENTORY_CALLS,
                  max_rewords: int = MAX_REWORDS, on_step=None) -> dict:
    """{"finished": bool, "item": finish's item ({} if it never finished), "calls", "stopped"} for one drafted
    inventory item (assist.items.validate_inventory)."""
    ts = bench.ts
    shown = {k: item[k] for k in ("id", "target", "find", "ask")}
    text = INVENTORY_PROMPT.format(target=item["target"], name=ts.name, visible=len(ts.visible()),
                                   negatives=len(ts.negatives()), item=json.dumps(shown, ensure_ascii=False, indent=1),
                                   alternatives=", ".join(f'"{a}"' for a in item.get("alternatives", [])) or "none",
                                   unclear=UNCLEAR, rewords=max_rewords, max_calls=max_calls)
    r = _loop(respond, text, _inventory_dispatch(bench, item, max_rewords), max_calls, on_step)
    return {"finished": r["finished"], "item": r["args"].get("item", {}), "calls": r["calls"],
            "stopped": r["stopped"]}
