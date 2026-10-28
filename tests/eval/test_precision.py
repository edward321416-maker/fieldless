import json

import pytest

from eval import precision
from tests.pipeline.fakes import synthetic_photo


def issue(iid, item, image_id, box, status="auto"):
    return {"id": iid, "issue_type": item, "review_status": status,
            "evidence": {"image": f"scan4_issue{iid}.jpg", "image_id": image_id},
            "model_outputs": {"check": {"answer": "yes", "box": box}}}


def entrance(eid, name, image_id, status="auto"):
    return {"id": eid, "place_name": name, "step_count": 0, "review_status": status,
            "evidence": {"image": f"scan4_entrance{eid}.jpg", "image_id": image_id}}


def data(issues, entrances=()):
    return {"scan": {"id": 4}, "issues": list(issues), "entrances": list(entrances)}


def test_targets_are_the_findings_no_person_has_seen():
    d = data([issue(1, "pole", "a", [0, 0, 100, 500]), issue(2, "pole", "b", [0, 0, 1, 1], status="verified"),
              issue(3, "tree", "c", [5, 5, 50, 50], status="field_check")],
             [entrance(7, "JACK", "d"), entrance(8, "TIME", "e", status="field_check")])
    assert [(t["kind"], t["id"], t["item"], t["image_id"], t["box"]) for t in precision.targets(d)] == [
        ("issues", 1, "pole", "a", [0, 0, 100, 500]), ("entrances", 7, "entrance", "d", None)]


def test_targets_take_the_tracked_box_when_there_is_one():
    row = issue(1, "pole", "a", [0, 0, 100, 500])
    row["model_outputs"]["find"] = {"box": [600, 0, 700, 900]}
    assert precision.targets(data([row]))[0]["box"] == [600, 0, 700, 900]


def test_carry_keeps_a_judgement_for_the_same_object_on_the_same_photo():
    old = data([issue(1, "pole", "a", [100, 0, 200, 600]), issue(2, "pole", "a", [600, 0, 700, 600]),
                issue(3, "tree", "b", [0, 0, 500, 500])], [entrance(7, "JACK", "d")])
    labels = {"scan_id": 4,
              "issues": {"1": {"item": "pole", "value": "yes"}, "2": {"item": "pole", "value": "yes", "dup_of": 1},
                         "3": {"item": "tree", "value": "no", "note": "a bus"}},
              "entrances": {"7": {"value": "unclear"}}}
    new = data([issue(11, "pole", "a", [110, 0, 210, 610]),     # same pole, box moved a little
                issue(12, "pole", "a", [610, 0, 700, 600]),     # the repeat of 11
                issue(13, "tree", "b", [600, 600, 900, 900]),   # same photo, another tree
                issue(14, "pole", "z", [100, 0, 200, 600])],    # a new photo
               [entrance(17, "JACK", "d")])

    out = precision.carry(labels, old, new, scan_id=9)

    assert out["scan_id"] == 9
    assert out["issues"]["11"] == {"item": "pole", "value": "yes", "carried_from": 1}
    assert out["issues"]["12"] == {"item": "pole", "value": "yes", "dup_of": 11, "carried_from": 2}
    assert out["issues"]["13"] == {"item": "tree", "value": ""} and out["issues"]["14"]["value"] == ""
    assert out["entrances"]["17"] == {"item": "entrance", "value": "unclear", "carried_from": 7}


def test_summary_counts_values_and_repeats_per_item():
    labels = {"issues": {"1": {"item": "pole", "value": "yes"}, "2": {"item": "pole", "value": "yes", "dup_of": 1},
                         "3": {"item": "pole", "value": "unclear"}, "4": {"item": "tree", "value": "no"}},
              "entrances": {"7": {"value": "yes"}, "8": {"value": ""}}}
    rows = precision.summary(labels)
    assert rows == [{"item": "pole", "n": 3, "yes": 2, "no": 0, "unclear": 1, "dup": 1, "open": 0},
                    {"item": "tree", "n": 1, "yes": 0, "no": 1, "unclear": 0, "dup": 0, "open": 0},
                    {"item": "entrance", "n": 2, "yes": 1, "no": 0, "unclear": 0, "dup": 0, "open": 1},
                    {"item": "all", "n": 6, "yes": 3, "no": 1, "unclear": 1, "dup": 1, "open": 1}]
    md = precision.markdown(rows)
    assert md.splitlines()[0] == "| item | findings | right | wrong | unclear | repeats | not judged |"
    assert "| **all** | **6** | **3** | **1** | **1** | **1** | **1** |" in md


def test_sheets_draw_every_target_and_write_a_file_to_fill_in(tmp_path):
    ev = tmp_path / "evidence"
    ev.mkdir()
    for name in ("scan4_issue1.jpg", "scan4_issue2.jpg", "scan4_entrance7.jpg"):
        synthetic_photo(ev / name, seed=len(name))
    d = data([issue(1, "pole", "a", [100, 0, 200, 600]), issue(2, "pole", "b", [0, 0, 300, 300])],
             [entrance(7, "JACK", "d")])
    out = tmp_path / "out"

    labels = precision.sheets(d, ev, out, per_sheet=2)

    assert sorted(p.name for p in out.glob("*.jpg")) == ["entrance_01.jpg", "pole_01.jpg"]
    assert labels == {"scan_id": 4, "issues": {"1": {"item": "pole", "value": ""}, "2": {"item": "pole", "value": ""}},
                      "entrances": {"7": {"item": "entrance", "value": ""}}}


def test_main_refuses_to_overwrite_judgements(tmp_path, monkeypatch):
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"issues": {"1": {"item": "pole", "value": "yes"}}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="already"):
        precision.write_labels({"issues": {}}, labels)
    assert json.loads(labels.read_text(encoding="utf-8"))["issues"]["1"]["value"] == "yes"


def test_main_summary_prints_the_table_of_a_judged_scan(tmp_path, capsys):
    from pipeline import config

    run = config.run_dir("r")
    (run / "precision_scan4.json").write_text(json.dumps({"issues": {"1": {"item": "pole", "value": "yes"}},
                                                          "entrances": {}}), encoding="utf-8")
    precision.main(["summary", "--run", "r", "--scan", "4"])
    assert "| **all** | **1** | **1** | **0** | **0** | **0** | **0** |" in capsys.readouterr().out
