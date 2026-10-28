import httpx
import pytest

from pipeline import mapillary

BBOX = (126.98, 37.56, 126.99, 37.565)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MAPILLARY_TOKEN", "MLY|secret-token")
    monkeypatch.setattr(mapillary.time, "sleep", lambda s: None)


def respond(monkeypatch, handler):
    """handler(url, params) -> (status, json body or text); every request goes through it."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=None):
        calls.append((url, params))
        status, body = handler(url, params or {})
        request = httpx.Request("GET", url)
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body, request=request)
        return httpx.Response(status, content=body if isinstance(body, bytes) else str(body).encode(),
                              request=request)

    monkeypatch.setattr(mapillary.httpx, "get", fake_get)
    return calls


def images(n, start=0):
    return [{"id": str(start + i), "sequence": "s", "captured_at": 1_700_000_000_000, "is_pano": False,
             "on_foot": True} for i in range(n)]


def test_check_bbox_rejects_large_areas():
    with pytest.raises(ValueError):
        mapillary.check_bbox((126.0, 37.0, 126.2, 37.2))


def test_search_splits_a_full_page_into_quadrants(monkeypatch):
    starts = iter(range(10_000, 100_000, 10_000))

    def handler(url, params):
        if params["bbox"] == ",".join(f"{v:.6f}" for v in BBOX):
            return 200, {"data": images(2000)}
        return 200, {"data": images(3, start=next(starts))}

    calls = respond(monkeypatch, handler)
    found, capped = mapillary.search_area(BBOX)
    assert len(calls) == 5 and not capped
    assert len(found) == 2000 + 4 * 3


def test_search_splits_on_reduce_the_amount_of_data(monkeypatch):
    def handler(url, params):
        if params["bbox"] == ",".join(f"{v:.6f}" for v in BBOX):
            return 500, "Please reduce the amount of data you're asking for"
        return 200, {"data": images(1, start=len(params["bbox"]))}

    calls = respond(monkeypatch, handler)
    found, capped = mapillary.search_area(BBOX)
    assert len(calls) == 5  # one failed parent request, no retries, then four quadrants
    assert found and not capped


def test_grid_tiles_the_bbox_without_gaps():
    cells = mapillary.grid(BBOX, 4)
    assert len(cells) == 16
    assert min(c[0] for c in cells) == BBOX[0] and min(c[1] for c in cells) == BBOX[1]
    assert max(c[2] for c in cells) == pytest.approx(BBOX[2]) and max(c[3] for c in cells) == pytest.approx(BBOX[3])
    area = sum((c[2] - c[0]) * (c[3] - c[1]) for c in cells)
    assert area == pytest.approx((BBOX[2] - BBOX[0]) * (BBOX[3] - BBOX[1]))


def test_search_grid_searches_every_cell_and_keeps_each_photo_once(monkeypatch):
    def handler(url, params):
        cell = params["bbox"]
        # every cell returns its own photo plus one photo that every cell sees (a walk along a cell edge)
        return 200, {"data": images(1, start=hash(cell) % 10**9 + 10) + images(1, start=0)}

    calls = respond(monkeypatch, handler)
    found, capped = mapillary.search_grid(BBOX, n=4, passes=1)
    assert len(calls) == 16 and not capped
    assert len(found) == 16 + 1


def test_search_grid_searches_each_cell_again_and_merges_what_each_pass_returned(monkeypatch):
    seen = []

    def handler(url, params):  # a cell answers with a different part of its photos each time
        seen.append(params["bbox"])
        return 200, {"data": images(1, start=len(seen) * 100)}

    respond(monkeypatch, handler)
    found, _ = mapillary.search_grid(BBOX, n=2, passes=3)
    assert len(seen) == 12 and len(set(seen)) == 4
    assert len(found) == 12


def test_search_grid_is_capped_if_any_cell_is(monkeypatch):
    def handler(url, params):
        return 200, {"data": images(2000)}

    respond(monkeypatch, handler)
    monkeypatch.setattr(mapillary, "MAX_SPLIT_DEPTH", 0)
    _, capped = mapillary.search_grid(BBOX, n=2)
    assert capped


def test_error_messages_never_contain_the_token(monkeypatch):
    respond(monkeypatch, lambda url, params: (400, "bad token MLY|secret-token here"))
    with pytest.raises(mapillary.MapillaryError) as err:
        mapillary.sequence_frames("seq")
    assert "secret-token" not in str(err.value)
    assert err.value.status == 400


def test_rank_sequences_prefers_on_foot_then_count():
    since = mapillary.ms("2020-01-01")
    imgs = ([{"id": f"a{i}", "sequence": "A", "captured_at": since + 1, "is_pano": False, "on_foot": None}
             for i in range(5)]
            + [{"id": f"b{i}", "sequence": "B", "captured_at": since + 1, "is_pano": False, "on_foot": True}
               for i in range(2)]
            + [{"id": "old", "sequence": "C", "captured_at": since - 1, "is_pano": False, "on_foot": True}]
            + [{"id": "pano", "sequence": "D", "captured_at": since + 1, "is_pano": True, "on_foot": True}])
    assert mapillary.rank_sequences(imgs, since) == ["B", "A"]


def test_frame_record_prefers_computed_position_and_keeps_the_photo_url():
    img = {"id": 7, "sequence": "s", "captured_at": 1_727_168_400_000, "compass_angle": 10.0,
           "computed_compass_angle": 12.5, "geometry": {"coordinates": [1.0, 2.0]},
           "computed_geometry": {"coordinates": [126.98, 37.56]}, "creator": {"username": "filipb"},
           "thumb_2048_url": "https://example/x.jpg"}
    assert mapillary.frame_record(img) == {
        "id": "7", "sequence": "s", "lat": 37.56, "lng": 126.98, "compass": 12.5,
        "captured_at": "2024-09-24T09:00:00Z", "creator": "filipb", "thumb_2048_url": "https://example/x.jpg"}


def test_download_writes_on_200_and_reports_failure(monkeypatch, tmp_path):
    respond(monkeypatch, lambda url, params: (200, b"jpegbytes") if "ok" in url else (404, "missing"))
    assert mapillary.download("https://x/ok.jpg", tmp_path / "a.jpg")
    assert (tmp_path / "a.jpg").read_bytes() == b"jpegbytes"
    assert not mapillary.download("https://x/gone.jpg", tmp_path / "b.jpg")
    assert not mapillary.download(None, tmp_path / "c.jpg")


def test_a_coverage_tile_is_fetched_with_the_token_and_an_empty_one_is_none(offline, monkeypatch):
    tiles = {"14/2620/5720": (200, b"\x1a\x02tile"), "14/1/1": (204, b""), "14/2/2": (500, "MLY|secret-token down")}
    calls = respond(monkeypatch, lambda url, params: tiles["/".join(url.rsplit("/", 3)[1:])])
    assert mapillary.coverage_tile(14, 2620, 5720) == b"\x1a\x02tile"
    assert calls[0] == ("https://tiles.mapillary.com/maps/vtp/mly1_public/2/14/2620/5720",
                        {"access_token": "MLY|secret-token"})
    assert mapillary.coverage_tile(14, 1, 1) is None
    with pytest.raises(mapillary.MapillaryError) as err:
        mapillary.coverage_tile(14, 2, 2)
    assert "secret-token" not in str(err.value)
