"""The authoring agent (plan 5): Muse Spark tests the survey items it drafted from a rule by calling SAM 3.1 and
itself through two tools on labelled photos, revises them and calls finish. Code scores every test; the agent only
chooses what to try next. Limits keep it from running on: MAX_TOOL_CALLS tool calls, and the run's cost meter."""
import json

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


def run(respond, bench, drafts: list[dict], max_calls: int = MAX_TOOL_CALLS) -> dict:
    """{"finished": bool, "items": finish's items ([] if it never finished), "calls": tool calls made,
    "stopped": why it stopped without finishing, or ""}."""
    ts = bench.ts
    questions = "\n".join(f'- "{k}" (sections {", ".join(q.sections)}): a yes means: {q.means}'
                          for k, q in ts.questions.items())
    shown = [{k: d[k] for k in ("id", "section", "target", "find", "ask", "report_when")} for d in drafts]
    text = PROMPT.format(name=ts.name, count=len(ts.photos), target=ts.target, questions=questions,
                         items=json.dumps(shown, ensure_ascii=False, indent=1), max_calls=max_calls)
    history = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}]
    calls = 0
    for _ in range(max_calls + 5):  # turns; a turn may call several tools
        try:
            turn = respond(history)
        except CostCapReached as exc:
            return {"finished": False, "items": [], "calls": calls, "stopped": f"cost cap: {exc}"}
        history += turn["output"]
        fcalls = [o for o in turn["output"] if o.get("type") == "function_call"]
        if not fcalls:
            return {"finished": False, "items": [], "calls": calls, "stopped": "answered without calling finish"}
        for c in fcalls:
            args = json.loads(c.get("arguments") or "{}")
            if c["name"] == "finish":
                return {"finished": True, "items": args.get("items", []), "calls": calls, "stopped": ""}
            calls += 1
            if calls > max_calls:
                result = {"error": "tool call limit reached: call finish now"}
            else:
                try:
                    result = _dispatch(bench, c["name"], args)
                except CostCapReached as exc:
                    return {"finished": False, "items": [], "calls": calls, "stopped": f"cost cap: {exc}"}
                except (KeyError, ValueError) as exc:
                    result = {"error": str(exc)}
            history.append({"type": "function_call_output", "call_id": c["call_id"], "output": json.dumps(result)})
    return {"finished": False, "items": [], "calls": calls, "stopped": "turn limit"}
