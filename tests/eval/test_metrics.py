import pytest

from eval.metrics import evaluate, track_km


def frame(i, clip=0):
    return {"idx": i, "clip": clip, "image_id": f"img{i}", "lat": 37.5 + i * 0.0001, "lng": 127.0,
            "captured_at": "2023-02-08T00:00:00Z", "creator": "u"}


def issue(kind, start, end=None, status="needs_review", reasons=("possible_defect",), overlap=None, corrections=()):
    return {"issue_type": kind, "frame_start": start, "frame_end": start if end is None else end, "frame_idx": start,
            "object_label": None, "overlap_ratio": overlap, "review_status": status, "review_reasons": list(reasons),
            "corrections": list(corrections)}


def data(issues=(), entrances=(), processing=100.0, review=20.0, cost=0.5):
    return {"scan": {"id": 1, "processing_seconds": processing, "review_seconds": review, "cost_usd": cost},
            "frames": [frame(i) for i in range(10)], "issues": list(issues), "entrances": list(entrances)}


def truth(photos, seconds=600):
    return {"run": "r", "scan_id": 1, "labeled_by": "human", "labeling_seconds": seconds, "photos": photos}


def photo(idx, defects=(), obstructions=(), entrances=()):
    return {"idx": idx, "defects": list(defects), "obstructions": list(obstructions), "entrances": list(entrances)}


def test_track_km_sums_steps_within_each_clip_only():
    frames = [frame(0), frame(1), frame(5, clip=1), frame(6, clip=1)]
    assert track_km(frames) == pytest.approx(2 * 0.0111, rel=0.01)


def test_defect_spots_and_obstructions_are_scored_per_class():
    d = data(issues=[issue("missing_blocks", 3), issue("cover_cut", 8), issue("obstruction", 5, 6)])
    t = truth({"img3": photo(3, [{"type": "missing_blocks"}]), "img4": photo(4, [{"type": "missing_blocks"}]),
               "img1": photo(1, [{"type": "cover_cut"}]), "img6": photo(6, obstructions=[{"label": "bicycle"}])})
    r = evaluate(d, t)
    assert (r["defects"]["missing_blocks"]["hits"], r["defects"]["missing_blocks"]["found"]) == (1, 1)
    assert r["defects"]["cover_cut"]["false"] == [(8, 8)] and r["defects"]["cover_cut"]["missed"] == [(1, 1)]
    assert r["defects"]["fixed_obstruction"]["truth"] == 0
    assert (r["obstructions"]["hits"], r["obstructions"]["found"]) == (1, 1)


def test_entrances_match_within_two_photos_and_score_steps():
    d = data(entrances=[{"frame_idx": 2, "step_count": 2, "sill_possible": False, "review_reasons": []},
                        {"frame_idx": 9, "step_count": 0, "sill_possible": True, "review_reasons": ["sill"]}])
    t = truth({"img4": photo(4, entrances=[{"steps": 3, "sill_unsure": False}])})
    e = evaluate(d, t)["entrances"]
    assert (e["predicted"], e["truth"], e["matched"]) == (2, 1, 1)
    assert (e["steps_exact"], e["steps_within_one"], e["sill_agree"]) == (0, 1, 1)


def test_review_summary_counts_flags_decisions_and_seconds():
    d = data(issues=[issue("missing_blocks", 1, status="verified", corrections=[{"seconds": 4.0}]),
                     issue("cover_cut", 2, status="rejected", corrections=[{"seconds": 2.0}]),
                     issue("obstruction", 3, status="auto", reasons=())])
    r = evaluate(d, truth({}))["review"]
    assert (r["items"], r["flagged"], r["flagged_share"]) == (3, 2, 0.667)
    assert (r["defects_verified"], r["defects_rejected"], r["defects_confirmed_share"]) == (1, 1, 0.5)
    assert (r["reviewed_actions"], r["review_seconds_median"]) == (2, 3.0)


def test_speed_compares_person_seconds_with_processing_plus_review():
    s = evaluate(data(processing=100.0, review=20.0, cost=0.5), truth({}, seconds=600))["speed"]
    assert s["photos"] == 10 and s["reviewed"] is True
    assert s["human_seconds_per_km"] == pytest.approx(600 / s["km"], rel=0.01)
    assert s["threshold_seconds_per_km"] == pytest.approx(120 / s["km"], rel=0.01)
    assert s["usd_per_km"] == pytest.approx(0.5 / s["km"], rel=0.01)


def test_sweep_shows_precision_and_recall_when_overlap_threshold_rises():
    d = data(issues=[issue("obstruction", 2, overlap=0.25), issue("obstruction", 6, overlap=0.6)])
    t = truth({"img6": photo(6, obstructions=[{"label": "bicycle"}])})
    rows = {row["min_overlap"]: row for row in evaluate(d, t)["sweep"]["obstruction"]}
    assert (rows[0.2]["predicted"], rows[0.2]["hits"], rows[0.2]["found"]) == (2, 1, 1)
    assert (rows[0.3]["predicted"], rows[0.3]["hits"], rows[0.3]["found"]) == (1, 1, 1)


def test_truth_for_another_scan_or_unknown_photos_is_refused():
    with pytest.raises(ValueError, match="another scan"):
        evaluate(data(), {**truth({}), "scan_id": 9})
    with pytest.raises(ValueError, match="not in the scan"):
        evaluate(data(), truth({"imgX": photo(99)}))
