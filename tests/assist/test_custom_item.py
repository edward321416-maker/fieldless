import json

import pytest

from assist import custom_item
from pipeline import geo
from pipeline.item_runs import ScanPhotos
from pipeline.meta_api import image_part
from tests.pipeline import sam_text
from tests.pipeline.fakes import FakeApi, synthetic_photo

ORIGIN = (47.614, -122.32)
N = 12  # photos 20 m apart along a street, all looking north; a city bench stands 10 m ahead of photos 0-5
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


def scan(tmp_path, n=N):
    frames, paths = [], {}
    for i in range(n):
        lat, lng = geo.offset_point(ORIGIN[0], ORIGIN[1], 90.0, 20.0 * i)
        frames.append({"idx": i, "clip": 0, "sequence_id": "s1", "image_id": f"img{i}", "lat": lat, "lng": lng,
                       "compass": 0.0, "captured_at": "2022-08-30T00:00:00Z", "creator": "a"})
        paths[i] = synthetic_photo(tmp_path / f"{i}.jpg", seed=i + 1)
    return ScanPhotos(17, [0, 0, 1, 1], "US", frames, paths)


def city_layer(photos):
    feats = []
    for i in sorted(SHOWN):
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
    return tmp_path


def test_an_asset_the_city_publishes_is_tested_on_its_positions_without_asking_a_person(data, monkeypatch):
    photos = scan(data)
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: ({"name": "SDOT benches"}, city_layer(p)))
    lines = []

    out = custom_item.start(fake_api(photos), photos, "public bench", lines.append, respond=agent_turns())

    assert out["status"] == "ready"
    check, test = out["check"], out["test"]
    assert (check.id, check.find, check.target, check.inventory, check.find_by, check.group_m) == (
        "agent_public_bench", "bench", "public bench", True, "photos", custom_item.GROUP_M)
    assert check.label_yes == "Public bench" and check.ask.endswith("answer unclear.")
    assert test["labels"] == "city" and test["layer"] == "SDOT benches"
    assert test["kept"] == [6, 6] and test["false"][0] == 0 and test["phrase"] == "bench"
    assert any("SDOT benches" in line for line in lines) and lines[-1].startswith("Passed")
    assert list((data / "data" / "items" / "drafts").glob("agent-public-bench-*.toml"))


def test_without_a_city_list_a_person_answers_ten_photos_and_the_item_is_tested_on_them(data, monkeypatch):
    photos = scan(data)
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: None)
    api = fake_api(photos)

    first = custom_item.start(api, photos, "public bench", lambda line: None, respond=agent_turns())

    assert first["status"] == "labels_needed"
    asked = first["photos"]
    assert len(asked) == 10 and len(set(asked)) == 10
    assert len([i for i in asked if i in SHOWN | {7}]) == 6  # six SAM found, four it did not
    state = json.loads(json.dumps(first["state"]))  # the server keeps it as JSON
    labels = {i: "yes" if i in SHOWN else "no" for i in asked}

    out = custom_item.resume(api, photos, state, labels, lambda line: None, respond=agent_turns())

    assert out["status"] == "ready" and out["test"]["labels"] == "person" and out["test"]["layer"] is None
    assert out["check"].id == "agent_public_bench"


def test_too_few_photos_showing_it_is_not_tested(data, monkeypatch):
    photos = scan(data)
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: None)
    api = fake_api(photos)
    first = custom_item.start(api, photos, "public bench", lambda line: None, respond=agent_turns())
    labels = {i: "no" for i in first["photos"]}
    labels[first["photos"][0]] = "yes"

    out = custom_item.resume(api, photos, first["state"], labels, lambda line: None, respond=agent_turns())

    assert out["status"] == "failed" and "Too few photos show a public bench" in out["reason"]


def test_an_item_that_misses_what_people_see_is_not_put_on_the_map(data, monkeypatch):
    photos = scan(data)
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: ({"name": "SDOT benches"}, city_layer(p)))
    api = fake_api(photos, yes={0, 1})  # Spark denies four of the six benches

    out = custom_item.start(api, photos, "public bench", lambda line: None, respond=agent_turns())

    assert out["status"] == "failed" and out["reason"].startswith("No reliable wording found")
    assert out["test"]["kept"] == [2, 6]


def test_a_draft_code_cannot_use_stops_the_run(data, monkeypatch):
    photos = scan(data)
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: None)
    out = custom_item.start(fake_api(photos, draft={"id": "Bad Id"}), photos, "public bench", lambda line: None)
    assert out["status"] == "failed" and out["reason"].startswith("Muse Spark's draft was not usable")


def test_the_city_check_counts_the_items_rows_against_the_citys_positions(data, monkeypatch):
    photos = scan(data)
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: None)
    assert custom_item.city_check(photos, None, []) is None
    monkeypatch.setattr(custom_item, "layer_for", lambda p, target: ({"name": "SDOT benches"}, city_layer(p)))
    out = custom_item.start(fake_api(photos), photos, "public bench", lambda line: None, respond=agent_turns())
    rows = [{"id": "item1-1", "issue_type": out["check"].id, "frame_idx": 0, "object_label": "yes",
             "review_status": "auto", "model_outputs": {"check": {"answer": "yes", "box": [450, 300, 550, 700]}}}]
    assert custom_item.city_check(photos, out["check"], rows) == {"layer": "SDOT benches", "found": 1, "covered": 6}
