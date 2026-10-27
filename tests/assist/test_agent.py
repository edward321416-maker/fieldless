import json
from types import SimpleNamespace

from assist import agent
from assist.responder import make_responder
from pipeline.cost import CostCapReached, CostMeter

DRAFT = [{"id": "warning_present", "target": "curb ramp", "find": "curb ramp", "ask": "Domes?", "report_when": "no"}]


class FakeBench:
    def __init__(self):
        self.ts = SimpleNamespace(name="t", photos=(1, 2, 3), target="curb ramp",
                                  questions={"warning_present": "has domes"})
        self.calls = []

    def find(self, phrase):
        self.calls.append(("find", phrase))
        return {"phrase": phrase, "found_visible": 2}

    def ask(self, find, ask, key, polarity):
        self.calls.append(("ask", find, key))
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
    final = {"id": "warning_present", "find": "curb cut", "ask": "Domes?", "key": "warning_present",
             "polarity": "same", "tested": True, "note": "curb cut finds more ramps"}
    respond, seen = scripted([
        [call("score_find", {"phrase": "curb cut"}, "c1")],
        [call("score_ask", {"find": "curb cut", "ask": "Domes?", "key": "warning_present", "polarity": "same"}, "c2")],
        [call("finish", {"items": [final]}, "c3")]])
    r = agent.run(respond, bench, DRAFT)
    assert r["finished"] and r["calls"] == 2 and r["items"] == [final] and r["stopped"] == ""
    assert bench.calls == [("find", "curb cut"), ("ask", "curb cut", "warning_present")]
    outputs = [i for i in seen[2] if i.get("type") == "function_call_output"]
    assert [o["call_id"] for o in outputs] == ["c1", "c2"] and json.loads(outputs[1]["output"])["right"] == 2
    assert "curb ramp" in seen[0][0]["content"][0]["text"]


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
