import cv2
import numpy as np
import pytest

from pipeline import collect, config

M = 1 / 111_320  # degrees of latitude per metre (near enough for tests)


def rec(i, lat_m, seq="s", lng=127.0):
    return {"id": f"{seq}{i}", "sequence": seq, "lat": 37.5 + lat_m * M, "lng": lng, "compass": 0.0,
            "captured_at": f"2025-01-01T00:00:{i % 60:02d}Z", "creator": "c", "thumb_2048_url": f"u{i}"}


def street(n, step_m, seq="s", lng=127.0):
    return [rec(i, i * step_m, seq, lng) for i in range(n)]


def test_in_bbox():
    assert collect.in_bbox({"lat": 37.5, "lng": 127.0}, (126.9, 37.4, 127.1, 37.6))
    assert not collect.in_bbox({"lat": 37.7, "lng": 127.0}, (126.9, 37.4, 127.1, 37.6))
    assert not collect.in_bbox({"lat": None, "lng": None}, (126.9, 37.4, 127.1, 37.6))


def test_thin_keeps_a_photo_every_two_metres():
    kept = collect.thin(street(9, 0.6))  # photos at 0, 0.6, ..., 4.8 m
    assert [k["id"] for k in kept] == ["s0", "s4", "s8"]  # 0, 2.4 and 4.8 m


def test_thin_keeps_close_photos_where_the_walker_turns():
    frames = street(4, 0.5)
    for f, heading in zip(frames, (80.0, 84.0, 116.0, 132.0)):  # the D1 crossing: slow, then turning
        f["compass"] = heading
    assert [k["id"] for k in collect.thin(frames)] == ["s0", "s2", "s3"]  # turns of 36 and 16 degrees
    assert collect._turn(350.0, 5.0) == 15.0 and collect._turn(None, 90.0) == 0.0


def test_view_cells_are_the_ground_ahead_of_a_photo():
    north = collect.view_cells([rec(0, 0.0)], 37.5)
    south = collect.view_cells([{**rec(0, 0.0), "compass": 180.0}], 37.5)
    assert north and not north & south  # ahead and behind are different ground
    assert collect.view_cells([{**rec(0, 0.0), "compass": None}], 37.5) == set()
    ys = sorted({y for _, y in north})
    assert (ys[-1] - ys[0]) * collect.VIEW_CELL_M <= collect.VIEW_REACH_M  # no further than the reach


def test_plan_clips_skips_repeats_and_short_sequences_and_respects_the_budget():
    a = street(30, 5.0, "a")
    repeat = street(30, 5.0, "r")  # walks the same street as a
    short = street(5, 5.0, "x", lng=127.01)
    b = street(30, 5.0, "b", lng=127.02)
    clips = collect.plan_clips([a, repeat, short, b], max_photos=45)
    assert [c[0]["sequence"] for c in clips] == ["a", "b"]  # rank order
    assert sorted(len(c) for c in clips) == [15, 30]  # the one taken second is cut to the budget


def test_plan_clips_takes_new_ground_from_any_walk_before_walking_the_same_street_again():
    a = street(40, 5.0, "a")  # 200 m north
    again = street(40, 5.0, "r")  # the same 200 m, walked again; ranked above the other street
    other = street(40, 5.0, "c", lng=127.01)  # another street
    clips = collect.plan_clips([a, again, other], max_photos=80)
    assert [(c[0]["sequence"], len(c)) for c in clips] == [("a", 40), ("c", 40)]


def test_plan_clips_joins_the_chosen_photos_of_one_walk_that_follow_each_other():
    clips = collect.plan_clips([street(130, 5.0, "a")], max_photos=400)
    assert [[f["id"] for f in c] for c in clips] == [[f"a{i}" for i in range(130)]]  # windows of 60, one clip


def test_plan_clips_starts_a_section_where_the_new_ground_starts():
    a = street(40, 5.0, "a")
    b = street(60, 5.0, "b")  # its first 100 m re-walk a's first 100 m; its last 200 m are new
    for f in b:
        f["lat"] += 100 * M
    clips = collect.plan_clips([a, b], max_photos=400)
    taken_b = [f["id"] for c in clips if c[0]["sequence"] == "b" for f in c]
    assert taken_b and "b0" not in taken_b and "b59" in taken_b  # b's part on a's ground is left out


def test_preview_counts_without_downloading(monkeypatch):
    monkeypatch.setattr(collect, "candidate_sequences", lambda bbox: [street(20, 5.0, "a")])
    monkeypatch.setattr(collect, "fetch_photo", lambda r: pytest.fail("preview must not download"))
    p = collect.preview((126.9, 37.4, 127.1, 37.6), max_photos=400)
    assert p["photos"] == 20 and p["sequences"] == 1
    assert len(p["walks"]) == 1 and len(p["walks"][0]) == 20 and p["walks"][0][0] == [127.0, 37.5]
    assert p["captured_from"] == "2025-01-01T00:00:00Z" and p["captured_to"] == "2025-01-01T00:00:19Z"
    assert p["est_cost_usd"] == pytest.approx(0.2 * config.EST_USD_PER_100_PHOTOS, abs=0.01)
    with_checks = collect.preview((126.9, 37.4, 127.1, 37.6), max_photos=400, checks=5)
    assert with_checks["est_cost_usd"] == pytest.approx(0.2 * config.est_usd_per_100_photos(5), abs=0.01)


def test_fetch_photo_uses_the_cache_and_asks_for_a_fresh_url_once(monkeypatch):
    urls = []

    def fake_download(url, dst):
        urls.append(url)
        if url != "fresh":
            return False
        cv2.imwrite(str(dst), np.zeros((30, 40, 3), np.uint8))
        return True

    monkeypatch.setattr(collect.mapillary, "download", fake_download)
    monkeypatch.setattr(collect.mapillary, "thumb_url", lambda image_id: "fresh")
    assert collect.fetch_photo(rec(1, 0)) == (40, 30)
    assert urls == ["u1", "fresh"]
    assert collect.fetch_photo(rec(1, 0)) == (40, 30)  # cached: no new download
    assert urls == ["u1", "fresh"]


def test_fetch_photo_gives_up_after_the_fresh_url_fails(monkeypatch):
    monkeypatch.setattr(collect.mapillary, "download", lambda url, dst: False)
    monkeypatch.setattr(collect.mapillary, "thumb_url", lambda image_id: "fresh")
    assert collect.fetch_photo(rec(2, 0)) is None
    assert not collect.photo_path("s2").exists()


def test_collect_numbers_frames_and_clips_and_drops_missing_photos(monkeypatch):
    monkeypatch.setattr(collect, "candidate_sequences",
                        lambda bbox: [street(12, 5.0, "a"), street(12, 5.0, "b", lng=127.02)])
    monkeypatch.setattr(collect, "fetch_photo", lambda r: None if r["id"] == "a3" else (2048, 1536))
    frames = collect.collect((126.9, 37.4, 127.1, 37.6), max_photos=400)
    assert len(frames) == 23
    assert [f["idx"] for f in frames] == list(range(23))
    assert {f["clip"] for f in frames} == {0, 1}
    assert frames[3]["image_id"] == "a4"
    assert frames[0] == {"idx": 0, "clip": 0, "image_id": "a0", "sequence_id": "a", "lat": 37.5, "lng": 127.0,
                         "compass": 0.0, "captured_at": "2025-01-01T00:00:00Z", "creator": "c", "width": 2048,
                         "height": 1536}


def test_candidate_sequences_rank_by_each_sequences_own_frames_not_by_what_the_search_returned(monkeypatch):
    def frames(seq, n, on_foot, inside=True):
        return [{"captured_at": 10**13, "on_foot": on_foot, "rec": {**r, "inside": inside}}
                for r in street(n, 5.0, seq)]

    whole = {"a": frames("a", 12, None),  # the search saw most of "a"
             "b": frames("b", 15, True),  # and little of "b", which has the most pedestrian photos
             "c": frames("c", 30, True, inside=False),  # passes by outside the area
             "d": frames("d", 15, True)}  # ties with "b": the sequence id settles it, the same every time
    monkeypatch.setattr(collect.mapillary, "search_grid", lambda bbox: ([], None))
    monkeypatch.setattr(collect.mapillary, "rank_sequences", lambda images, since: ["a", "b", "c", "d"])
    monkeypatch.setattr(collect.mapillary, "sequence_frames", lambda seq: whole[seq])
    monkeypatch.setattr(collect.mapillary, "frame_record", lambda img: img["rec"])
    monkeypatch.setattr(collect, "in_bbox", lambda rec, bbox: rec["inside"])
    ranked = collect.candidate_sequences((0, 0, 1, 1))
    assert [recs[0]["sequence"] for recs in ranked] == ["d", "b", "a"]
