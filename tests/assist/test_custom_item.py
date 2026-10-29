import json
from pathlib import Path

import pytest

from assist import custom_item
from pipeline import geo
from pipeline.item_runs import ScanPhotos
from pipeline.meta_api import image_part
from tests.pipeline import sam_text
from tests.pipeline.fakes import FakeApi, synthetic_photo

ORIGIN = (47.614, -122.32)
N = 24  # photos 20 m apart along a street, all looking north; a bench stands 10 m ahead of photos 0-5
SHOWN = set(range(6))
DRAFT = {"id": "bench", "target": "public bench", "find": "bench", "alternatives": ["park bench", "seat"],
         "ask": "A public bench is a seat fixed to the sidewalk; a cafe chair is not. Does the photo show one?",
         "label_yes": "Public bench", "label_no": "Not a public bench"}
FOUND = sam_text.text([sam_text.line(0, [sam_text.record("0", 10, 10, 50, 40, 160, 120, 0.9)])])


def call(name, args, cid):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": cid}


def agent_turns(find="bench", ask=DRAFT["ask"]):
    """The inventory agent tries the drafted phrase, confirms the drafted ask and finishes."""
    turns = [[call("score_find", {"phrase": find}, "c1")], [call("score_confirm", {"find": find, "ask": ask}, "c2")],
             [call("finish", {"item": {"find": find, "ask": ask, "note": "the drafted phrase finds them"}}, "c3")]]
    seen = []

    def respond(items):
        seen.append(items)
        return {"output": turns[len(seen) - 1], "text": ""}

    return respond


class FakeJudge:
    """Stands in for Muse Spark Open: says a photo shows the target when it is in `sees`."""
    def __init__(self, sees=SHOWN):
        self.sees, self.asked = set(sees), []

    def answer(self, check, photo, box=None):
        idx = int(Path(photo).stem)
        self.asked.append(idx)
        return {"visible": idx in self.sees, "answer": "yes" if idx in self.sees else "no", "box": [],
                "confidence": 0.8, "note": ""}


def scan(tmp_path, n=N):
    frames, paths = [], {}
    for i in range(n):
        lat, lng = geo.offset_point(ORIGIN[0], ORIGIN[1], 90.0, 20.0 * i)
        frames.append({"idx": i, "clip": 0, "sequence_id": "s1", "image_id": f"img{i}", "lat": lat, "lng": lng,
                       "compass": 0.0, "captured_at": "2022-08-30T00:00:00Z", "creator": "a"})
        paths[i] = synthetic_photo(tmp_path / f"{i}.jpg", seed=i + 1)
    return ScanPhotos(17, [0, 0, 1, 1], "US", frames, paths)


def city_layer(photos, near=SHOWN):
    feats = []
    for i in sorted(near):
        f = photos.frames[i]
        lat, lng = geo.offset_point(f["lat"], f["lng"], 0.0, 10.0)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
                      "properties": {"OBJECTID": 100 + i, "UNITID": f"BKR-{i}"}})
    return {"type": "FeatureCollection", "features": feats}


def fake_api(photos, draft=DRAFT, hits=SHOWN | {7}, yes=SHOWN):
    """SAM finds the target in `hits`; Muse Spark drafts `draft` and says yes on the photos in `yes`, no elsewhere."""
    by_image = {image_part(p)["image_url"]: i for i, p in photos.paths.items()}

    def spark(schema_name, parts):
        if schema_name == "inventory_item_draft":
            return draft
        i = by_image[parts[0]["image_url"]]
        return {"visible": True, "answer": "yes" if i in yes else "no", "box": [], "confidence": 0.8, "note": f"p{i}"}

    def sam(prompt, media):
        return FOUND if int(media.stem) in hits else ""

    return FakeApi(spark=spark, sam=sam)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: None)
    return tmp_path


def test_the_item_is_tested_on_another_models_answers_without_asking_a_person(data):
    photos = scan(data)
    judge, lines = FakeJudge(), []

    out = custom_item.start(fake_api(photos), photos, "public bench", lines.append, respond=agent_turns(), judge=judge)

    assert out["status"] == "ready"
    check, test = out["check"], out["test"]
    assert (check.id, check.find, check.target, check.inventory, check.find_by, check.group_m) == (
        "agent_public_bench", "bench", "public bench", True, "photos", custom_item.GROUP_M)
    assert check.label_yes == "Public bench" and check.ask.endswith("answer unclear.")
    assert test["labels"] == "model" and test["judge"] == "Muse Spark Open" and test["layer"] is None
    assert test["kept"] == [6, 6] and test["false"][0] == 0 and test["phrase"] == "bench"
    asked = set(judge.asked)
    assert len(asked) == custom_item.KEY_PHOTOS and SHOWN | {7} <= asked  # every SAM find is among the answered
    assert any("Muse Spark Open" in line for line in lines) and lines[-1].startswith("Passed")
    assert list((data / "data" / "items" / "drafts").glob("agent-public-bench-*.toml"))


def test_the_citys_list_adds_the_photos_it_says_should_show_one(data, monkeypatch):
    photos = scan(data)
    monkeypatch.setattr(custom_item, "layer_for",
                        lambda p, target: ({"name": "SDOT benches"}, city_layer(p, near={20, 21, 22})))
    judge = FakeJudge(sees=SHOWN | {20, 21})

    out = custom_item.start(fake_api(photos, yes=SHOWN | {20, 21}), photos, "public bench", lambda line: None,
                            respond=agent_turns(), judge=judge)

    assert {20, 21, 22} <= set(judge.asked)  # the city's photos are answered too, not taken as true
    assert out["test"]["layer"] == "SDOT benches" and out["test"]["labels"] == "model"


def test_too_few_photos_showing_it_is_not_tested(data):
    photos = scan(data)
    out = custom_item.start(fake_api(photos), photos, "public bench", lambda line: None, respond=agent_turns(),
                            judge=FakeJudge(sees={0, 1}))
    assert out["status"] == "failed" and "Too few photos show a public bench" in out["reason"]


def test_an_item_that_misses_what_the_other_model_sees_is_not_put_on_the_map(data):
    photos = scan(data)
    out = custom_item.start(fake_api(photos, yes={0, 1}), photos, "public bench", lambda line: None,
                            respond=agent_turns(), judge=FakeJudge())  # Spark denies four of the six benches
    assert out["status"] == "failed" and out["reason"].startswith("No reliable wording found")
    assert out["test"]["kept"] == [2, 6]


def test_without_a_second_model_nothing_is_tested(data, monkeypatch):
    photos = scan(data)
    monkeypatch.setattr(custom_item, "second_model", lambda api: None)
    out = custom_item.start(fake_api(photos), photos, "public bench", lambda line: None)
    assert out["status"] == "failed" and "second model" in out["reason"]


def test_a_draft_code_cannot_use_stops_the_run(data):
    photos = scan(data)
    out = custom_item.start(fake_api(photos, draft={"id": "Bad Id"}), photos, "public bench", lambda line: None,
                            judge=FakeJudge())
    assert out["status"] == "failed" and out["reason"].startswith("Muse Spark's draft was not usable")


def test_a_person_is_never_asked(data):
    photos = scan(data)
    out = custom_item.resume(fake_api(photos), photos, {"item": {}}, {1: "yes"}, lambda line: None)
    assert out["status"] == "failed" and "does not ask a person" in out["reason"]


def test_the_city_check_counts_the_items_rows_against_the_citys_positions(data, monkeypatch):
    photos = scan(data)
    assert custom_item.city_check(photos, None, []) is None
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: ({"name": "SDOT benches"}, city_layer(p)))
    out = custom_item.start(fake_api(photos), photos, "public bench", lambda line: None, respond=agent_turns(),
                            judge=FakeJudge())
    rows = [{"id": "item1-1", "issue_type": out["check"].id, "frame_idx": 0, "object_label": "yes",
             "review_status": "auto", "model_outputs": {"check": {"answer": "yes", "box": [450, 300, 550, 700]}}}]
    assert custom_item.city_check(photos, out["check"], rows) == {"layer": "SDOT benches", "found": 1, "covered": 6}
