import json

import pytest

from eval.truth import FORMAT, load_truth, parse_truth


def good():
    return {"format": FORMAT, "run": "r", "scan_id": 2, "labeled_by": "human", "labeling_seconds": 30,
            "photos": {"img7": {"idx": 7, "defects": [{"type": "cover_cut"}], "obstructions": [],
                                "entrances": [{"steps": 2, "sill_unsure": False}]}}}


def test_parse_keeps_the_fields(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(good()), encoding="utf-8")
    t = load_truth(path)
    assert (t["run"], t["scan_id"], t["labeling_seconds"]) == ("r", 2, 30)
    assert t["photos"]["img7"]["defects"] == [{"type": "cover_cut"}]


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(format="x"), "not a"),
    (lambda d: d.pop("scan_id"), "scan_id"),
    (lambda d: d.update(labeling_seconds=-1), "labeling_seconds"),
    (lambda d: d["photos"]["img7"].pop("idx"), "idx"),
    (lambda d: d["photos"]["img7"]["defects"].append({"type": "worn"}), "unknown defect"),
])
def test_parse_refuses_bad_files(change, message):
    data = good()
    change(data)
    with pytest.raises(ValueError, match=message):
        parse_truth(data)
