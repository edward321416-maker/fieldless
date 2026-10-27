from datetime import datetime, timedelta, timezone

import pytest

from pipeline import db
from server import limits

BBOX = [126.9842, 37.5623, 126.9858, 37.5632]


def test_client_ip_prefers_the_first_forwarded_address():
    assert limits.client_ip({"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, "10.0.0.2") == "1.2.3.4"
    assert limits.client_ip({}, "10.0.0.2") == "10.0.0.2"
    assert limits.client_ip({}, None) == "unknown"


def test_ip_hash_is_salted_and_short():
    a = limits.ip_hash("1.2.3.4", "salt")
    assert len(a) == 32 and a == limits.ip_hash("1.2.3.4", "salt")
    assert a != limits.ip_hash("1.2.3.4", "other") and "1.2.3.4" not in a


def test_check_bbox():
    assert limits.check_bbox(BBOX, 0.0001) == BBOX
    for bad in ([1, 2, 3], [126.99, 37.56, 126.98, 37.57], [126.0, 37.0, 127.0, 38.0], [200, 0, 201, 1]):
        with pytest.raises(limits.LimitError) as err:
            limits.check_bbox(bad, 0.0001)
        assert err.value.status == 422


def test_check_invite():
    limits.check_invite(None, None)
    limits.check_invite("abc", "abc")
    with pytest.raises(limits.LimitError) as err:
        limits.check_invite("wrong", "abc")
    assert err.value.status == 403


def test_check_daily_scans_counts_the_last_24_hours(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for _ in range(3):
        scan_id = db.create_scan(conn, "mapillary", BBOX)
        db.update_scan(conn, scan_id, client_ip_hash="h")
    with pytest.raises(limits.LimitError) as err:
        limits.check_daily_scans(conn, "h", 3)
    assert err.value.status == 429
    limits.check_daily_scans(conn, "h", 4)
    limits.check_daily_scans(conn, "other", 3)
    limits.check_daily_scans(conn, "h", 3, now=datetime.now(timezone.utc) + timedelta(days=2))
