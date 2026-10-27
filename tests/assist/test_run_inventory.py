import json
import tomllib

from assist import run_inventory, testset
from assist.items import UNCLEAR
from assist.run_inventory import inventory
from pipeline.meta_api import image_part
from tests.pipeline import sam_text
from tests.pipeline.fakes import FakeApi, synthetic_photo

DRAFT = {"id": "streetlight_pole", "target": "streetlight pole", "find": "pole",
         "alternatives": ["utility pole", "lamp post", "street light"],
         "ask": "A streetlight pole carries a lamp head; plain utility poles and sign posts are not. Is this one?",
         "label_yes": "Streetlight pole", "label_no": "Not a streetlight pole"}
REWORDED = ("A streetlight pole is a pole with a lamp head or arm at the top; plain wooden utility poles, sign posts "
            "and trees are not. Is the pole in this photo a streetlight pole?")
FOUND = sam_text.text([sam_text.line(0, [sam_text.record("0", 10, 10, 50, 40, 160, 120, 0.9)])])


def call(name, args, cid):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": cid}


def scripted(turns):
    seen = []

    def respond(items):
        seen.append(items)
        return {"output": turns[len(seen) - 1], "text": ""}

    return respond, seen


def setup(tmp_path, draft=DRAFT):
    """Photos a and b show a streetlight pole, n does not. "pole" finds only a; "utility pole" finds all three.
    Spark confirms a and b and rejects n."""
    data = {"name": "poles", "target": "streetlight pole", "questions": {},
            "photos": [{"id": "a", "target_visible": "yes"}, {"id": "b", "target_visible": "yes"},
                       {"id": "n", "target_visible": "no"}]}
    (tmp_path / "poles.json").write_text(json.dumps(data), encoding="utf-8")
    ts = testset.load("poles", tmp_path)
    paths = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=n + 1) for n, i in enumerate("abn")}
    by_image = {image_part(p)["image_url"]: i for i, p in paths.items()}
    prompts = []

    def spark(name, parts):
        if name == "inventory_item_draft":
            prompts.append(parts[0]["text"])
            return draft
        said = "no" if by_image[parts[0]["image_url"]] == "n" else "yes"
        return {"visible": True, "answer": said, "box": [], "confidence": 0.9, "note": f"{said}, by the lamp"}

    def sam(prompt, media):
        return FOUND if prompt == "utility pole" or media.stem == "a" else ""

    return FakeApi(spark=spark, sam=sam), ts, paths, prompts


def run(tmp_path, api, ts, paths, turns):
    respond, seen = scripted(turns)
    out = inventory(api, respond, "streetlight pole", ts, tmp_path / "out", describe="poles that carry a street lamp",
                    photo=lambda i: paths[i], workers=1, stamp="test")
    return out, seen


def test_inventory_drafts_tests_and_writes_the_toml_the_report_and_the_history(tmp_path):
    api, ts, paths, prompts = setup(tmp_path)
    turns = [[call("score_find", {"phrase": "pole"}, "c1"), call("score_find", {"phrase": "utility pole"}, "c2")],
             [call("score_confirm", {"find": "utility pole", "ask": REWORDED}, "c3")],
             [call("finish", {"item": {"find": "utility pole", "ask": REWORDED,
                                       "note": "utility pole finds both lamps; Spark drops the plain pole"}}, "c4")]]
    paths_out, _ = run(tmp_path, api, ts, paths, turns)
    assert {k: p.name for k, p in paths_out.items()} == {"toml": "inventory-streetlight-pole-test.toml",
                                                         "md": "inventory-streetlight-pole-test.md",
                                                         "json": "inventory-streetlight-pole-test.json"}
    assert "poles that carry a street lamp" in prompts[0]

    (check,) = tomllib.loads(paths_out["toml"].read_text(encoding="utf-8"))["check"]
    assert check["id"] == "streetlight_pole" and check["inventory"] is True and "report_when" not in check
    assert check["find"] == "utility pole" and check["ask"] == f"{REWORDED} {UNCLEAR}"
    assert check["test_find"] == "SAM found 2 of 2 photos showing the target; fired on 1 of 1 without it"
    assert check["test_confirm"].startswith("kept 2 of 2 photos showing the target")
    assert "kept 0 of 1 photos without it (SAM fired on 1: Spark said no 1" in check["test_confirm"]

    md = paths_out["md"].read_text(encoding="utf-8")
    for text in ('# Inventory item for "streetlight pole"', "poles that carry a street lamp", "finished, 3 tool calls",
                 '| "utility pole" | 2 of 2 | 1 of 1 | 1 |', '| "pole" | 1 of 2 | 0 of 1 | 1 |',
                 f"> {REWORDED} {UNCLEAR}", "Spark drops the plain pole", "- in the TOML: yes",
                 '"utility pole", "lamp post", "street light"'):
        assert text in md
    saved = json.loads(paths_out["json"].read_text(encoding="utf-8"))
    assert saved["agent"]["finished"] is True and saved["final"]["tested"] is True
    assert [h["tool"] for h in saved["history"]] == ["score_find", "score_find", "score_find", "score_confirm"]


def test_an_item_whose_final_ask_was_never_tested_stays_out_of_the_toml(tmp_path):
    api, ts, paths, _ = setup(tmp_path)
    turns = [[call("score_confirm", {"find": "utility pole", "ask": DRAFT["ask"]}, "c1")],
             [call("finish", {"item": {"find": "utility pole", "ask": REWORDED, "note": "this one is better"}}, "c2")]]
    paths_out, _ = run(tmp_path, api, ts, paths, turns)
    toml = paths_out["toml"].read_text(encoding="utf-8")
    assert "no tested item" in toml and tomllib.loads(toml).get("check") is None
    md = paths_out["md"].read_text(encoding="utf-8")
    assert "- test, confirm: not tested" in md and "in the TOML: no" in md
    assert f"> {DRAFT['ask']} {UNCLEAR}" in md  # the ask it did test is still in the report


def test_a_draft_code_drops_writes_a_report_without_running_the_agent(tmp_path):
    api, ts, paths, _ = setup(tmp_path, draft={**DRAFT, "id": "Streetlight Pole"})
    paths_out, seen = run(tmp_path, api, ts, paths, [])
    assert seen == [] and [c for c in api.calls if c[0] != "spark"] == []
    md = paths_out["md"].read_text(encoding="utf-8")
    assert "No item" in md and "Streetlight Pole: id must be lower case" in md and "stopped: no valid draft" in md
    assert tomllib.loads(paths_out["toml"].read_text(encoding="utf-8")).get("check") is None


def test_main_fetches_the_photos_and_passes_the_options(tmp_path, monkeypatch):
    _, ts, _, _ = setup(tmp_path)
    fetched, seen = [], {}
    monkeypatch.setattr(run_inventory.testset, "load", lambda name: ts)
    monkeypatch.setattr(run_inventory.collect, "fetch_photo", lambda rec: fetched.append(rec["id"]) or (160, 120))

    def fake_inventory(api, respond, target, ts_, out_dir, describe=""):
        seen.update(cap=api.meter.cap_usd, target=target, describe=describe, out_dir=out_dir)
        return {}

    monkeypatch.setattr(run_inventory, "inventory", fake_inventory)
    run_inventory.main(["--target", "streetlight pole", "--testset", "poles", "--run", "inv-test",
                        "--describe", "lamp poles", "--cap", "0.5"])
    assert fetched == ["a", "b", "n"]
    assert seen["cap"] == 0.5 and seen["target"] == "streetlight pole" and seen["describe"] == "lamp poles"
    assert seen["out_dir"].parts[-2:] == ("items", "drafts")
