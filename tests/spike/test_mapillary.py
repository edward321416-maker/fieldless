import pytest

from spike import mapillary as mly

IMAGES = [
    {"id": "1", "sequence": "A", "captured_at": 1700000000000, "is_pano": False, "on_foot": True},
    {"id": "2", "sequence": "A", "captured_at": 1700000001000, "is_pano": False, "on_foot": True},
    {"id": "3", "sequence": "B", "captured_at": 1500000000000, "is_pano": False, "on_foot": True},
    {"id": "4", "sequence": "C", "captured_at": 1700000000000, "is_pano": True, "on_foot": True},
    {"id": "5", "sequence": "D", "captured_at": 1700000000000, "is_pano": False, "on_foot": None},
    {"id": "6", "sequence": "E", "captured_at": 1700000000000, "is_pano": False, "on_foot": False},
]
SINCE = mly.ms("2020-01-01")


def test_bbox_area_and_limit():
    assert abs(mly.bbox_area((127.0, 37.0, 127.01, 37.008)) - 0.00008) < 1e-12
    mly.check_bbox((127.0, 37.0, 127.01, 37.008))
    with pytest.raises(ValueError):
        mly.check_bbox((127.0, 37.0, 127.2, 37.1))


def test_summarize_counts_recent_pedestrian_flat_photos():
    s = mly.summarize(IMAGES, SINCE)
    assert s["total"] == 6 and s["recent"] == 5 and s["capped"] is False
    assert s["recent_on_foot_flat"] == 2
    assert s["recent_unclassified_flat"] == 1
    assert abs(s["pano_ratio"] - 1 / 6) < 1e-9
    assert s["by_year"] == {"2017": 1, "2023": 5}


def test_rank_sequences_prefers_recent_pedestrian_flat():
    ranked = mly.rank_sequences(IMAGES, SINCE)
    assert [r["sequence"] for r in ranked] == ["A", "D"]
    assert ranked[0]["recent_on_foot_flat"] == 2 and ranked[0]["latest"] == "2023-11-14"


def test_frame_record_prefers_computed_values():
    img = {"id": "9", "sequence": "A", "captured_at": 1700000000000,
           "geometry": {"type": "Point", "coordinates": [127.0, 37.0]},
           "computed_geometry": {"type": "Point", "coordinates": [127.0001, 37.0002]},
           "compass_angle": 10.0, "computed_compass_angle": 12.5,
           "creator": {"username": "walker", "id": "u1"}}
    assert mly.frame_record(img) == {"id": "9", "sequence": "A", "lat": 37.0002, "lng": 127.0001,
                                     "compass": 12.5, "captured_at": "2023-11-14T22:13:20Z", "creator": "walker"}


def test_frame_record_tolerates_missing_fields():
    rec = mly.frame_record({"id": "9", "sequence": "A", "captured_at": 1700000000000})
    assert rec["lat"] is None and rec["compass"] is None and rec["creator"] is None


def test_track_km_skips_missing_positions():
    frames = [{"lat": 37.0, "lng": 127.0}, {"lat": 37.001, "lng": 127.0},
              {"lat": None, "lng": None}, {"lat": 37.002, "lng": 127.0}]
    assert abs(mly.track_km(frames) - 0.2224) < 0.002


# --- offline tests for the fix round: retries, token scrubbing, quadrant split, fail-loud build ---

import json
from pathlib import Path

import cv2
import httpx
import numpy as np

from spike import fetch_clips, video


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(mly.time, "sleep", lambda s: None)


def test_summarize_returns_recent_flat():
    assert mly.summarize(IMAGES, SINCE)["recent_flat"] == 3


def test_summarize_capped_follows_result_size():
    many = [{"id": str(i), "sequence": "A", "captured_at": 1700000000000, "is_pano": False, "on_foot": True}
            for i in range(mly.MAX_RESULTS)]
    assert mly.summarize(many, SINCE)["capped"] is True
    assert mly.summarize(many, SINCE, capped=False)["capped"] is False


def test_get_retries_429_then_succeeds(monkeypatch, no_sleep):
    monkeypatch.setenv("MAPILLARY_TOKEN", "SECRET123")
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json={"data": []})

    monkeypatch.setattr(mly.httpx, "get", fake_get)
    assert mly._get("images", {}) == {"data": []}
    assert len(calls) == 2


def test_get_gives_up_after_last_attempt(monkeypatch, no_sleep):
    monkeypatch.setenv("MAPILLARY_TOKEN", "SECRET123")
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(503, text="busy")

    monkeypatch.setattr(mly.httpx, "get", fake_get)
    with pytest.raises(RuntimeError, match="503"):
        mly._get("images", {})
    assert len(calls) == mly.RETRY_ATTEMPTS


def test_error_message_includes_body_and_never_token(monkeypatch, no_sleep):
    monkeypatch.setenv("MAPILLARY_TOKEN", "SECRET123")
    monkeypatch.setattr(mly.httpx, "get",
                        lambda url, **kw: httpx.Response(400, text="invalid field, token SECRET123 rejected"))
    with pytest.raises(RuntimeError) as exc:
        mly._get("images", {})
    message = str(exc.value)
    assert "invalid field" in message
    assert "SECRET123" not in message


def test_token_straddling_the_300_char_cut_does_not_leak_a_prefix(monkeypatch, no_sleep):
    monkeypatch.setenv("MAPILLARY_TOKEN", "SECRET123")
    body = "x" * 295 + "SECRET123" + "y" * 20  # the token spans characters 295..303
    monkeypatch.setattr(mly.httpx, "get", lambda url, **kw: httpx.Response(400, text=body))
    with pytest.raises(RuntimeError) as exc:
        mly._get("images", {})
    message = str(exc.value)
    assert "SECRE" not in message  # truncating first would have kept "SECRE"
    assert "x" * 295 + "<reda" in message


def test_download_retries_transport_error_then_succeeds(monkeypatch, tmp_path, no_sleep):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ConnectError("reset")
        return httpx.Response(200, content=b"jpegbytes")

    monkeypatch.setattr(mly.httpx, "get", fake_get)
    dst = tmp_path / "a.jpg"
    assert mly.download("http://example/a.jpg", dst) is True
    assert dst.read_bytes() == b"jpegbytes"
    assert len(calls) == 2


def test_download_returns_false_on_404_without_retry(monkeypatch, tmp_path, no_sleep):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(404)

    monkeypatch.setattr(mly.httpx, "get", fake_get)
    assert mly.download("http://example/gone.jpg", tmp_path / "b.jpg") is False
    assert len(calls) == 1


def _fake_search(responses):
    """Return a stand-in for mly._get that answers each images query with the next canned response."""
    queue = iter(responses)

    def fake_get(path, params):
        return {"data": next(queue)}

    return fake_get


def _images(ids):
    return [{"id": str(i), "sequence": "A", "captured_at": 1700000000000, "is_pano": False, "on_foot": True}
            for i in ids]


def test_search_area_merges_quadrants_and_dedupes(monkeypatch):
    full = _images(range(mly.MAX_RESULTS))  # full bbox is capped
    q1, q2, q3, q4 = _images(range(0, 10)), _images(range(5, 15)), _images(range(100, 103)), []
    monkeypatch.setattr(mly, "_get", _fake_search([full, q1, q2, q3, q4]))
    images, capped = mly.search_area((127.0, 37.0, 127.05, 37.05))
    assert capped is False
    assert sorted(int(i["id"]) for i in images) == list(range(0, 15)) + [100, 101, 102]


def test_search_area_stays_capped_if_a_quadrant_is_capped(monkeypatch):
    full = _images(range(mly.MAX_RESULTS))
    big = _images(range(mly.MAX_RESULTS))
    monkeypatch.setattr(mly, "_get", _fake_search([full, big, _images([1]), [], []]))
    _, capped = mly.search_area((127.0, 37.0, 127.05, 37.05))
    assert capped is True


def test_search_images_not_split_when_under_cap(monkeypatch):
    monkeypatch.setattr(mly, "_get", _fake_search([_images(range(3))]))
    images, capped = mly.search_area((127.0, 37.0, 127.05, 37.05))
    assert len(images) == 3 and capped is False


def _write_jpg(url, dst):
    cv2.imwrite(str(dst), np.zeros((4, 4, 3), np.uint8))
    return True


def _clip_frames(n):
    return [{"id": str(i), "sequence": "S", "captured_at": 1700000000000 + i * 1000,
             "creator": {"username": "u"}, "thumb_2048_url": f"http://x/{i}"} for i in range(n)]


def test_build_fails_loud_when_most_photos_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_clips.config, "CLIPS", tmp_path)
    monkeypatch.setattr(mly, "sequence_frames", lambda seq: _clip_frames(10))
    monkeypatch.setattr(mly, "download", lambda url, dst: False)
    monkeypatch.setattr(video, "images_to_mp4", lambda paths, dst, fps=1.0: Path(dst))
    with pytest.raises(SystemExit) as exc:
        fetch_clips.build("t", "S", 0, 10)
    assert exc.value.code == 1
    meta = json.loads((tmp_path / "t" / "frames.json").read_text(encoding="utf-8"))
    assert meta["failed"] == list(range(10))


def test_build_succeeds_when_all_photos_download(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_clips.config, "CLIPS", tmp_path)
    monkeypatch.setattr(mly, "sequence_frames", lambda seq: _clip_frames(10))
    monkeypatch.setattr(mly, "download", _write_jpg)
    monkeypatch.setattr(video, "images_to_mp4", lambda paths, dst, fps=1.0: Path(dst))
    fetch_clips.build("ok", "S", 0, 10)  # no SystemExit
    meta = json.loads((tmp_path / "ok" / "frames.json").read_text(encoding="utf-8"))
    assert meta["failed"] == [] and meta["missing_creator"] == []
