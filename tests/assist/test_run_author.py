import json
import tomllib

from assist import testset
from assist.rules import Rule
from assist.run_author import author
from tests.pipeline import sam_text
from tests.pipeline.fakes import FakeApi, synthetic_photo

RULE_TEXT = ("405.2 Slope. Ramp runs shall have a running slope not steeper than 1:12.\n"
             "406.5 Location. Curb ramps shall be located so that they do not project into vehicular traffic lanes.\n"
             "705.1.3 Contrast. Detectable warning surfaces shall contrast visually with adjacent walking surfaces.")
ITEM = {"id": "warning_contrast", "target": "curb ramp", "find": "curb ramp",
        "ask": "Does the warning contrast with the sidewalk?", "report_when": "no", "label_yes": "Contrasts",
        "label_no": "No contrast", "section": "705.1.3", "page": 196,
        "quote": "Detectable warning surfaces shall contrast visually with adjacent walking surfaces"}
PROJECTION = {**ITEM, "id": "ramp_projection", "ask": "Does the ramp project into the traffic lane?",
              "report_when": "yes", "label_yes": "Projects", "label_no": "Clear", "section": "406.5",
              "quote": "Curb ramps shall be located so that they do not project into vehicular traffic lanes"}
BLEND = "Does the warning blend into the sidewalk with no visible difference?"


def call(name, args, cid):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": cid}


def test_author_tests_only_covered_items_and_derives_the_defect_answer_from_the_label(tmp_path):
    rule = Rule("r", "a test rule", "https://example.org/r.pdf", ((1, 1),), tmp_path / "r.pdf", tmp_path / "r.txt")
    rule.pdf.write_bytes(b"%PDF-1.4")
    rule.text.write_text(RULE_TEXT, encoding="utf-8")
    data = {"name": "t", "target": "curb ramp",
            "questions": {"warning_contrast": {"means": "contrasts", "sections": ["705.1.3"], "defect_when": "no"}},
            "photos": [{"id": "a", "target_visible": "yes", "answers": {"warning_contrast": "yes"}},
                       {"id": "n", "target_visible": "no", "answers": {}}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")
    ts = testset.load("t", tmp_path)
    synthetic_photo(tmp_path / "a.jpg", seed=1)
    synthetic_photo(tmp_path / "n.jpg", seed=2)
    draft = {"checkable": [ITEM, PROJECTION, {**ITEM, "id": "Bad Id"}],
             "not_checkable": [{"requirement": "running slope 1:12", "section": "405.2", "reason": "needs measuring"}]}
    answer = {"visible": True, "answer": "no", "box": [], "confidence": 0.9, "note": "yellow on gray, it stands out"}

    def spark(name, parts):
        return draft if name == "survey_items_draft" else answer

    def sam(prompt, media):
        if prompt != "curb cut" or media.stem != "a":
            return ""
        return sam_text.text([sam_text.line(0, [sam_text.record("0", 10, 10, 50, 40, 160, 120, 0.9)])])

    turns = [[call("score_find", {"phrase": "curb cut"}, "c1")],
             [call("score_ask", {"item_id": "warning_contrast", "find": "curb cut", "ask": BLEND,
                                 "polarity": "opposite"}, "c2")],
             [call("finish", {"items": [
                 {"id": "warning_contrast", "find": "curb cut", "ask": BLEND, "polarity": "opposite", "tested": True,
                  "note": "asked as blend-in, so yes is the defect"},
                 {"id": "ramp_projection", "find": "curb ramp", "ask": PROJECTION["ask"], "polarity": "same",
                  "tested": False, "note": "no label covers 406.5"}]}, "c3")]]
    seen = []

    def respond(items):
        seen.append(items)
        return {"output": turns[len(seen) - 1], "text": ""}

    api = FakeApi(spark=spark, sam=sam)
    paths = author(api, respond, rule, ts, tmp_path / "out", photo=lambda i: tmp_path / f"{i}.jpg", workers=1,
                   stamp="test")
    check = tomllib.loads(paths["toml"].read_text(encoding="utf-8"))["check"]
    assert [c["id"] for c in check] == ["warning_contrast"]  # only tested items go to the TOML
    assert check[0]["find"] == "curb cut" and check[0]["ask"] == BLEND
    assert check[0]["report_when"] == "yes"  # the label's defect answer is "no"; the ask is the opposite
    assert check[0]["test_find"].startswith("SAM found 1 of 1") and "right on 1 of 1" in check[0]["test_ask"]
    md = paths["md"].read_text(encoding="utf-8")
    for text in ("a test rule", "Bad Id", "needs measuring", "no label covers 406.5", "finished", "not tested"):
        assert text in md
    assert json.loads(paths["json"].read_text(encoding="utf-8"))["agent"]["finished"] is True
