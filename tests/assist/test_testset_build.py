import itertools
import json

import pytest

from assist import testset, testset_build as tb
from pipeline import geo

POINT = (47.6150, -122.3200)


def layer(*points):
    """points: (OBJECTID, lat, lng, other properties)."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"OBJECTID": pid, **props},
         "geometry": {"type": "Point", "coordinates": [lng, lat]}} for pid, lat, lng, props in points]}


def seeing(pid, dist, turn, compass=90.0, at=POINT, on_foot=True):
    """A photo heading `compass` with the position `at` dist metres away, turn degrees right of its heading."""
    lat, lng = geo.offset_point(*at, compass + turn + 180, dist)
    return {"id": pid, "sequence": "s", "lat": lat, "lng": lng, "compass": compass,
            "captured_at": "2022-08-30T23:17:58Z", "on_foot": on_foot}


def street():
    """Five poles 30 m apart, 3 m north of an east-west street; a walk east along it, a walk back west, and a walk on
    a street 200 m north with no poles. Walking photos are 5 m apart."""
    poles = layer(*[(100 + i, *geo.offset_point(*geo.offset_point(*POINT, 90, 30 * i), 0, 3), {"IS_STREETLIGHT": "YES"})
                    for i in range(5)])
    quiet = geo.offset_point(*POINT, 0, 200)
    photos = []
    for k in range(25):
        walks = (("east", POINT, 5 * k, 90.0, 10), ("west", POINT, 120 - 5 * k, 270.0, 11),
                 ("quiet", quiet, 5 * k, 90.0, 12))
        for seq, start, east, compass, hour in walks:
            lat, lng = geo.offset_point(*start, 90, east)
            photos.append({"id": f"{seq}{k:02d}", "sequence": seq, "lat": lat, "lng": lng, "compass": compass,
                           "captured_at": f"2022-08-30T{hour}:{k:02d}:00Z", "on_foot": True})
    return photos, poles


def lab(pid, seq, east_m, north_m=0.0, cls="positive"):
    lat, lng = geo.offset_point(*geo.offset_point(*POINT, 90, east_m), 0, north_m)
    return {"id": pid, "sequence": seq, "lat": lat, "lng": lng, "captured_at": pid, "class": cls}


def min_gap(photos):
    return min(geo.haversine_m(a["lat"], a["lng"], b["lat"], b["lng"]) for a, b in itertools.combinations(photos, 2))


def test_a_point_a_few_metres_ahead_is_positive_and_one_behind_is_not():
    pole = layer((1234567, *POINT, {}))
    photos = [seeing("ahead", 9.8, 6), seeing("left", 15, -18), seeing("behind", 10, 180), seeing("too_close", 2, 0),
              seeing("too_far", 30, 0), seeing("edge", 10, 35), seeing("side", 10, 60), seeing("away", 40, 0)]
    got = {p["id"]: p for p in tb.label_photos(photos, pole)}
    assert {i: p["class"] for i, p in got.items()} == {
        "ahead": "positive", "left": "positive", "behind": "negative", "too_close": "unclear",
        "too_far": "unclear", "edge": "unclear", "side": "negative", "away": "negative"}
    assert got["ahead"]["note"] == "city point 1234567 at 9.8 m, 6° right of heading"
    assert got["left"]["note"] == "city point 1234567 at 15.0 m, 18° left of heading"
    assert (got["ahead"]["city_point"], got["ahead"]["distance_m"], got["ahead"]["angle_deg"]) == ("1234567", 9.8, 6.0)
    assert got["behind"]["city_point"] is None and "nearest is city point 1234567 at 10.0 m" in got["behind"]["note"]
    assert got["away"]["note"] == "no city point within 35 m inside ±45° of heading"
    assert got["edge"]["note"].startswith("city point 1234567 at 10.0 m, 35° right of heading: outside")


def test_wider_settings_move_the_lines():
    pole = layer((1, *POINT, {}))
    photos = [seeing("edge", 10, 35), seeing("far", 28, 0)]
    got = tb.label_photos(photos, pole, max_m=30.0, pos_deg=40.0, neg_deg=60.0)
    assert [p["class"] for p in got] == ["positive", "positive"]
    with pytest.raises(ValueError, match="max_m <= neg_m"):
        tb.label_photos(photos, pole, max_m=40.0)
    with pytest.raises(ValueError, match="pos_deg <= neg_deg"):
        tb.label_photos(photos, pole, pos_deg=50.0)


def test_photos_without_a_compass_are_skipped_and_on_foot_only_keeps_photos_marked_on_foot():
    pole = layer((1, *POINT, {}))
    photos = [seeing("a", 10, 0), {**seeing("b", 10, 0), "compass": None}, seeing("c", 10, 0, on_foot=None),
              seeing("d", 10, 0, on_foot=False)]
    assert [p["id"] for p in tb.label_photos(photos, pole)] == ["a", "c", "d"]
    assert [p["id"] for p in tb.label_photos(photos, pole, on_foot_only=True)] == ["a"]


def test_the_where_filter_keeps_only_layer_points_matching_every_pair():
    behind = geo.offset_point(*POINT, 270, 20)  # 10 m behind a photo 10 m west of POINT heading east
    city = layer((1, *POINT, {"IS_STREETLIGHT": "NO", "OWNER": "SCL"}), (2, *behind, {"IS_STREETLIGHT": "YES"}),
                 (3, *POINT, {}))
    photo = [seeing("p", 10, 0)]
    assert tb.label_photos(photo, city)[0]["class"] == "positive"
    assert tb.label_photos(photo, city, where={"IS_STREETLIGHT": "YES"})[0]["class"] == "negative"
    assert tb.label_photos(photo, city, where={"IS_STREETLIGHT": "NO", "OWNER": "SCL"})[0]["city_point"] == "1"
    assert tb.label_photos(photo, city, where={"IS_STREETLIGHT": "yes"})[0]["class"] == "negative"  # exact text
    assert [p.id for p in tb.layer_points(city, {"OWNER": "SCL"})] == ["1"]
    assert tb.parse_where(["IS_STREETLIGHT=YES", " OWNER = SCL "]) == {"IS_STREETLIGHT": "YES", "OWNER": "SCL"}
    with pytest.raises(ValueError, match="KEY=VALUE"):
        tb.parse_where(["IS_STREETLIGHT"])


def test_point_ids_come_from_the_feature_then_its_properties_then_its_position():
    point = {"type": "Point", "coordinates": [1, 2]}
    features = [{"type": "Feature", "id": "f1", "properties": {"OBJECTID": 9}, "geometry": point},
                {"type": "Feature", "properties": {"OBJECTID": 9}, "geometry": point},
                {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[1, 2]]}},
                {"type": "Feature", "properties": None, "geometry": point}]
    assert [(p.id, p.lat, p.lng) for p in tb.layer_points(features)] == [("f1", 2.0, 1.0), ("9", 2.0, 1.0),
                                                                         ("#3", 2.0, 1.0)]


@pytest.mark.parametrize("at", [POINT, (64.1, -21.9), (-54.8, -68.3), (0.0, 30.0)])
def test_a_point_near_the_search_edge_is_seen_in_every_direction_and_latitude(at):
    # The search uses a grid; a point 34 m ahead must still count as in the wide cone, so the photo is not a negative.
    pole = layer((1, *at, {}))
    photos = [seeing(str(c), 34, 0, compass=float(c), at=at) for c in range(0, 360, 15)]
    assert {p["class"] for p in tb.label_photos(photos, pole)} == {"unclear"}


def test_sampling_takes_sequences_in_turn_and_keeps_photos_8_m_apart():
    labelled = ([lab(f"a{i}", "A", 3 * i) for i in range(10)] + [lab(f"b{i}", "B", 3 * i, 100) for i in range(10)]
                + [lab("c0", "C", 0, 200)])
    three = tb.sample(labelled, 3, 0)
    assert sorted(p["sequence"] for p in three) == ["A", "B", "C"]
    assert [p["sequence"] for p in tb.sample(labelled, 2, 0)] == ["A", "B"]
    many = tb.sample(labelled, 100, 0)
    assert len(many) < 21 and {p["sequence"] for p in many} == {"A", "B", "C"} and min_gap(many) >= 8.0
    assert len({p["id"] for p in many}) == len(many)


def test_the_spacing_holds_across_sequences_and_classes():
    same_spot = [lab("x0", "X", 0), lab("y0", "Y", 0)]
    assert len(tb.sample(same_spot, 2, 0)) == 1
    labelled = [lab("p", "A", 0), lab("n_near", "A", 3, cls="negative"), lab("n_far", "B", 50, cls="negative"),
                lab("u", "B", 80, cls="unclear")]
    assert [p["id"] for p in tb.sample(labelled, 1, 5)] == ["p", "n_far"]
    assert [p["id"] for p in tb.sample(labelled, 1, 5, n_unclear=1)] == ["p", "n_far", "u"]


def test_build_spreads_the_set_and_is_the_same_whatever_the_input_order():
    photos, poles = street()
    classes = {p["id"]: p["class"] for p in tb.label_photos(photos, poles)}
    data = tb.build("t_poles", "streetlight pole", photos, poles, n_pos=4, n_neg=2)
    assert data == tb.build("t_poles", "streetlight pole", list(reversed(photos)), poles, n_pos=4, n_neg=2)
    chosen = data["photos"]
    assert [p["target_visible"] for p in chosen] == ["yes"] * 4 + ["no"] * 2
    assert all(classes[p["id"]] == {"yes": "positive", "no": "negative"}[p["target_visible"]] for p in chosen)
    assert {p["sequence"] for p in chosen[:4]} == {"east", "west"} and min_gap(chosen) >= 8.0
    assert data["counts"] == {"photos": 75, "usable": 75, "layer_points": 5, "positive": 16, "negative": 27,
                              "unclear": 32, "chosen_positive": 4, "chosen_negative": 2, "chosen_unclear": 0}
    with pytest.raises(ValueError, match="name"):
        tb.build("../escape", "streetlight pole", photos, poles)


def test_the_written_file_loads_as_a_test_set(tmp_path):
    photos, poles = street()
    data = tb.build("t_poles", "streetlight pole", photos, poles, source_layer="Street_Lights.geojson", n_pos=4,
                    n_neg=2, where={"IS_STREETLIGHT": "YES"})
    path = tb.write(data, tmp_path)
    ts = testset.load("t_poles", tmp_path)
    assert (ts.name, ts.target, ts.questions) == ("t_poles", "streetlight pole", {})
    assert [p.id for p in ts.visible()] == [p["id"] for p in data["photos"][:4]]
    assert [p.id for p in ts.negatives()] == [p["id"] for p in data["photos"][4:]]
    s = testset.score_find(ts, {p.id: True for p in ts.visible()})
    assert (s["found_visible"], s["total_visible"], s["false_on_negatives"], s["total_negatives"]) == (4, 4, 0, 2)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["labelled_by"] == "auto from Street_Lights.geojson point positions (not checked by eye)"
    assert raw["source_layer"] == "Street_Lights.geojson"
    assert raw["params"] == {"min_m": 4.0, "max_m": 20.0, "pos_deg": 20.0, "neg_m": 35.0, "neg_deg": 45.0,
                             "where": {"IS_STREETLIGHT": "YES"}, "on_foot_only": False, "n_pos": 4, "n_neg": 2,
                             "n_unclear": 0, "spacing_m": 8.0}
    assert all(p["answers"] == {} and p["note"].startswith("city point ") for p in raw["photos"][:4])


def test_the_command_writes_the_set_and_prints_counts(tmp_path, capsys):
    photos, poles = street()
    (tmp_path / "poles.geojson").write_text(json.dumps(poles), encoding="utf-8")
    (tmp_path / "photos.json").write_text(json.dumps(photos), encoding="utf-8")
    args = ["--name", "cli_poles", "--target", "streetlight pole", "--layer", str(tmp_path / "poles.geojson"),
            "--photos", str(tmp_path / "photos.json"), "--out", str(tmp_path / "sets")]
    path = tb.main(args + ["--where", "IS_STREETLIGHT=YES", "--n-pos", "3", "--n-neg", "2"])
    out = capsys.readouterr().out
    assert "labelled: 16 positive, 27 negative, 32 unclear" in out and "chosen: 3 positive, 2 negative" in out
    assert path == tmp_path / "sets" / "cli_poles.json"
    ts = testset.load("cli_poles", tmp_path / "sets")
    assert (len(ts.visible()), len(ts.negatives())) == (3, 2)
    assert json.loads(path.read_text(encoding="utf-8"))["source_layer"] == "poles.geojson"
    with pytest.raises(SystemExit, match="KEY=VALUE"):
        tb.main(args + ["--where", "IS_STREETLIGHT"])
