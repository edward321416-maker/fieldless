from datetime import datetime, timezone

from spike.repeat_spots import cell_key, multi_date_cells

DAY_MS = 86_400_000


def ms(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def img(i, lng, lat, when, seq="s1", pano=False):
    return {"id": str(i), "sequence": seq, "captured_at": when, "is_pano": pano,
            "geometry": {"type": "Point", "coordinates": [lng, lat]}}


def test_points_a_few_metres_apart_share_a_cell_and_far_points_do_not():
    a = cell_key(37.54453, 127.05564, 15)
    assert cell_key(37.54454, 127.05565, 15) == a  # about 1.5 m away
    assert cell_key(37.54493, 127.05564, 15) != a  # about 44 m north


def test_cell_with_two_dates_far_enough_apart_counts_and_reports_the_gap():
    images = [
        img(1, 127.05564, 37.54453, ms(2023, 5, 1), "s1"),
        img(2, 127.05565, 37.54454, ms(2025, 5, 1), "s2"),
        img(3, 127.0600, 37.5480, ms(2025, 5, 1), "s3"),  # alone in its cell
    ]
    cells = multi_date_cells(images, cell_m=15, min_gap_days=30)
    assert len(cells) == 1
    (cell,) = cells.values()
    assert cell["sequences"] == 2
    assert cell["gap_days"] == 731


def test_same_day_or_close_dates_and_panoramas_do_not_count():
    images = [
        img(1, 127.05564, 37.54453, ms(2025, 5, 1), "s1"),
        img(2, 127.05565, 37.54454, ms(2025, 5, 10), "s2"),  # 9 days later: too close
        img(3, 127.05566, 37.54455, ms(2023, 1, 1), "s3", pano=True),  # panoramas are skipped
    ]
    assert multi_date_cells(images, cell_m=15, min_gap_days=30) == {}


def test_images_without_geometry_are_skipped():
    images = [{"id": "1", "sequence": "s", "captured_at": ms(2023, 1, 1), "is_pano": False},
              img(2, 127.05564, 37.54453, ms(2025, 1, 1))]
    assert multi_date_cells(images, cell_m=15, min_gap_days=30) == {}
