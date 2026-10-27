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


def test_overlap_share():
    a = street(10, 5.0, "a")
    assert collect.overlap_share(a, a) == 1.0
    assert collect.overlap_share(a, []) == 0.0
    far = street(10, 5.0, "b", lng=127.01)  # about 880 m east
    assert collect.overlap_share(far, a) == 0.0


def test_plan_clips_skips_repeats_and_short_sequences_and_respects_the_budget():
    a = street(30, 5.0, "a")
    repeat = street(30, 5.0, "r")  # walks the same street as a
    short = street(5, 5.0, "x", lng=127.01)
    b = street(30, 5.0, "b", lng=127.02)
    clips = collect.plan_clips([a, repeat, short, b], max_photos=45)
    assert [c[0]["sequence"] for c in clips] == ["a", "b"]
    assert [len(c) for c in clips] == [30, 15]


def test_preview_counts_without_downloading(monkeypatch):
    monkeypatch.setattr(collect, "candidate_sequences", lambda bbox: [street(20, 5.0, "a")])
    monkeypatch.setattr(collect, "fetch_photo", lambda r: pytest.fail("preview must not download"))
    p = collect.preview((126.9, 37.4, 127.1, 37.6), max_photos=400)
    assert p["photos"] == 20 and p["sequences"] == 1
    assert p["captured_from"] == "2025-01-01T00:00:00Z" and p["captured_to"] == "2025-01-01T00:00:19Z"
    assert p["est_cost_usd"] == pytest.approx(0.2 * config.EST_USD_PER_100_PHOTOS, abs=0.01)


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


def test_candidate_sequences_fetch_frames_only_while_plan_clips_needs_them(monkeypatch):
    fetched = []

    def sequence_frames(seq):
        fetched.append(seq)
        return [{"captured_at": 10**13, "rec": r} for r in street(20, 5.0, seq)]

    monkeypatch.setattr(collect.mapillary, "search_area", lambda bbox: ([], None))
    monkeypatch.setattr(collect.mapillary, "rank_sequences", lambda images, since: ["a", "b", "c"])
    monkeypatch.setattr(collect.mapillary, "sequence_frames", sequence_frames)
    monkeypatch.setattr(collect.mapillary, "frame_record", lambda img: img["rec"])
    monkeypatch.setattr(collect, "in_bbox", lambda rec, bbox: True)
    clips = collect.plan_clips(collect.candidate_sequences((0, 0, 1, 1)), max_photos=20)
    assert len(clips) == 1 and fetched == ["a", "b"]  # "b" shows the budget is spent; "c" is never fetched
