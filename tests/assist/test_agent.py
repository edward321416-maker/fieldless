import json
from types import SimpleNamespace

from assist import agent
from assist.responder import make_responder
from pipeline.cost import CostCapReached, CostMeter

DRAFT = [{"id": "warning_present", "target": "curb ramp", "find": "curb ramp", "ask": "Domes?", "report_when": "no",
          "section": "705.1"}]


class FakeBench:
    def __init__(self):
        self.ts = SimpleNamespace(name="t", photos=(1, 2, 3), target="curb ramp", questions={
            "warning_present": SimpleNamespace(means="has domes", sections=("705.1",), defect_when="no")})
        self.calls = []

    def find(self, phrase):
        self.calls.append(("find", phrase))
        return {"phrase": phrase, "found_visible": 2}

    def ask(self, item_id, find, ask, polarity):
        self.calls.append(("ask", item_id, find))
        return {"right": 2, "decidable": 2}


def call(name, args, cid):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": cid}


def scripted(turns):
    seen = []

    def respond(items):
        seen.append(list(items))
        return {"output": turns[len(seen) - 1], "text": ""}

    return respond, seen


def test_agent_calls_tools_sends_results_back_and_stops_at_finish():
    bench = FakeBench()
    final = {"id": "warning_present", "find": "curb cut", "ask": "Domes?", "polarity": "same", "tested": True,
             "note": "curb cut finds more ramps"}
    respond, seen = scripted([
        [call("score_find", {"phrase": "curb cut"}, "c1")],
        [call("score_ask", {"item_id": "warning_present", "find": "curb cut", "ask": "Domes?", "polarity": "same"},
              "c2")],
        [call("finish", {"items": [final]}, "c3")]])
    r = agent.run(respond, bench, DRAFT)
    assert r["finished"] and r["calls"] == 2 and r["items"] == [final] and r["stopped"] == ""
    assert bench.calls == [("find", "curb cut"), ("ask", "warning_present", "curb cut")]
    outputs = [i for i in seen[2] if i.get("type") == "function_call_output"]
    assert [o["call_id"] for o in outputs] == ["c1", "c2"] and json.loads(outputs[1]["output"])["right"] == 2
    prompt = seen[0][0]["content"][0]["text"]
    assert "curb ramp" in prompt and "Do not change what an item checks" in prompt
    assert '"warning_present" (sections 705.1): a yes means: has domes' in prompt and '"section": "705.1"' in prompt


def test_agent_refuses_tools_past_the_limit_and_reports_a_missing_finish():
    bench = FakeBench()
    respond, seen = scripted([[call("score_find", {"phrase": f"p{i}"}, f"c{i}")] for i in range(3)] + [[]])
    r = agent.run(respond, bench, DRAFT, max_calls=2)
    assert not r["finished"] and r["stopped"] == "answered without calling finish"
    assert bench.calls == [("find", "p0"), ("find", "p1")]
    assert "limit" in json.loads(seen[3][-1]["output"])["error"]


def test_agent_stops_cleanly_at_the_cost_cap():
    def respond(items):
        raise CostCapReached("cost cap $1.50")

    r = agent.run(respond, FakeBench(), DRAFT)
    assert not r["finished"] and r["stopped"].startswith("cost cap")


POLE = {"id": "streetlight_pole", "inventory": True, "target": "streetlight pole", "find": "utility pole",
        "ask": "A streetlight pole carries a lamp; plain poles are not. Is this one?",
        "alternatives": ["pole", "lamp post", "street light"]}
UNCLEAR = "If it is too far away or too small to see, answer unclear."


class FakeInventoryBench:
    def __init__(self, cap_on_confirm=False):
        self.ts = SimpleNamespace(name="poles", visible=lambda: [1, 2, 3], negatives=lambda: [4, 5])
        self.cap_on_confirm = cap_on_confirm
        self.calls = []

    def find(self, phrase):
        self.calls.append(("find", phrase))
        return {"phrase": phrase, "found_visible": 3, "false_on_negatives": 1}

    def confirm(self, item_id, find, ask):
        if self.cap_on_confirm:
            raise CostCapReached("cost cap $1.00")
        self.calls.append(("confirm", item_id, find, ask))
        return {"kept_true": 3, "kept_false": 0}


def test_inventory_agent_tries_phrases_confirms_and_finishes():
    bench = FakeInventoryBench()
    final = {"find": "utility pole", "ask": POLE["ask"], "note": "utility pole found every pole"}
    respond, seen = scripted([
        [call("score_find", {"phrase": "utility pole"}, "c1"), call("score_find", {"phrase": "pole"}, "c2")],
        [call("score_confirm", {"find": "utility pole", "ask": POLE["ask"]}, "c3")],
        [call("finish", {"item": final}, "c4")]])
    r = agent.run_inventory(respond, bench, POLE)
    assert r == {"finished": True, "item": final, "calls": 3, "stopped": ""}
    assert bench.calls == [("find", "utility pole"), ("find", "pole"),
                           ("confirm", "streetlight_pole", "utility pole", f"{POLE['ask']} {UNCLEAR}")]
    outputs = [i for i in seen[2] if i.get("type") == "function_call_output"]
    assert [o["call_id"] for o in outputs] == ["c1", "c2", "c3"] and json.loads(outputs[2]["output"])["kept_true"] == 3
    prompt = seen[0][0]["content"][0]["text"]
    assert "every streetlight pole" in prompt and '"id": "streetlight_pole"' in prompt
    assert '"poles": 3 labelled street photos that show a streetlight pole and 2 that do not' in prompt
    assert '"pole", "lamp post", "street light"' in prompt and "found_visible minus false_on_negatives" in prompt
    assert "at most 3 times" in prompt and "at most 12 tool calls" in prompt and UNCLEAR in prompt
    assert [t["name"] for t in agent.INVENTORY_TOOLS] == ["score_find", "score_confirm", "finish"]
    assert [t["name"] for t in agent.TOOLS] == ["score_find", "score_ask", "finish"]  # the check agent's, unchanged


def test_inventory_agent_refuses_a_fourth_rewording_of_the_ask():
    bench = FakeInventoryBench()
    asks = ["Lamp?", "Lamp head?", "Light on top?", "Any lamp at all?", POLE["ask"]]
    respond, seen = scripted([[call("score_confirm", {"find": "pole", "ask": a}, f"c{n}")]
                              for n, a in enumerate(asks)] + [[call("finish", {"item": {}}, "end")]])
    r = agent.run_inventory(respond, bench, POLE)
    assert r["finished"] and r["calls"] == 5
    assert [c[3] for c in bench.calls] == [f"{a} {UNCLEAR}" for a in asks[:3] + asks[4:]]  # the draft's ask is free
    assert "reworded 3 times" in json.loads(seen[4][-1]["output"])["error"]


def test_inventory_agent_stops_at_the_tool_call_limit_and_the_cost_cap():
    bench = FakeInventoryBench()
    respond, seen = scripted([[call("score_find", {"phrase": f"p{i}"}, f"c{i}")] for i in range(3)] + [[]])
    r = agent.run_inventory(respond, bench, POLE, max_calls=2)
    assert not r["finished"] and r["stopped"] == "answered without calling finish" and r["item"] == {}
    assert bench.calls == [("find", "p0"), ("find", "p1")]
    assert "limit" in json.loads(seen[3][-1]["output"])["error"]

    def broke(items):
        raise CostCapReached("cost cap $1.00")

    assert agent.run_inventory(broke, FakeInventoryBench(), POLE)["stopped"].startswith("cost cap")
    respond, _ = scripted([[call("score_confirm", {"find": "pole", "ask": "Lamp?"}, "c1")]])
    r = agent.run_inventory(respond, FakeInventoryBench(cap_on_confirm=True), POLE)
    assert not r["finished"] and r["calls"] == 1 and r["stopped"].startswith("cost cap")


def test_responder_reserves_logs_cost_and_returns_plain_output():
    logged = []

    class Item:
        def model_dump(self):
            return {"type": "function_call", "name": "finish", "arguments": "{}", "call_id": "c1"}

    resp = SimpleNamespace(output=[Item()], output_text="",
                           usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 1000, "output_tokens": 100}))
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: resp))
    api = SimpleNamespace(client=client, meter=CostMeter(1.0), log=lambda kind, **f: logged.append((kind, f)))
    out = make_responder(api, agent.TOOLS)([{"type": "message"}])
    assert out == {"output": [Item().model_dump()], "text": ""}
    assert api.meter.spent_usd > 0 and api.meter.reserved_usd == 0
    assert logged[0][0] == "spark_agent" and logged[0][1]["cost_usd"] == api.meter.spent_usd
