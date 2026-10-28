import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest

from pipeline import db
from server import limits

BBOX = [126.9842, 37.5623, 126.9858, 37.5632]


def test_client_ip_trusts_only_the_entry_the_proxy_appended():
    assert limits.client_ip({"x-forwarded-for": "6.6.6.6, 1.2.3.4"}, "10.0.0.2") == "1.2.3.4"  # 6.6.6.6 is forged
    assert limits.client_ip({"x-forwarded-for": "1.2.3.4"}, "10.0.0.2") == "1.2.3.4"
    assert limits.client_ip({"x-forwarded-for": "not-an-ip"}, "10.0.0.2") == "10.0.0.2"
    assert limits.client_ip({}, "10.0.0.2") == "10.0.0.2"
    assert limits.client_ip({}, None) == "unknown"
    assert limits.client_ip({}, "testclient") == "testclient"


def test_one_ipv6_household_is_one_address():
    a = limits.client_ip({"x-forwarded-for": "2001:db8::1"}, None)
    assert a == limits.client_ip({"x-forwarded-for": "2001:db8::ffff"}, None) == "2001:db8::/64"


def test_ports_and_ipv4_mapped_addresses_keep_one_key_per_client():
    assert limits.client_ip({"x-forwarded-for": "::ffff:203.0.113.5"}, None) == "203.0.113.5"
    assert limits.client_ip({"x-forwarded-for": "::ffff:198.51.100.7"}, None) == "198.51.100.7"
    assert limits.client_ip({"x-forwarded-for": "1.2.3.4:5678"}, "10.0.0.2") == "1.2.3.4"
    assert limits.client_ip({"x-forwarded-for": "[2001:db8::1]:443"}, None) == "2001:db8::/64"


def test_ip_hash_is_a_keyed_hash():
    a = limits.ip_hash("1.2.3.4", "salt")
    assert a == hmac.new(b"salt", b"1.2.3.4", hashlib.sha256).hexdigest()[:32]
    assert a != limits.ip_hash("1.2.3.4", "other")
    with pytest.raises(ValueError):
        limits.ip_hash("1.2.3.4", "")


def test_check_bbox_refuses_slivers_and_non_numbers():
    for bad in ([126.0, 37.0, 126.9, 37.0001], [None, 37.56, 126.99, 37.57], ["x", 37.56, 126.99, 37.57]):
        with pytest.raises(limits.LimitError) as err:
            limits.check_bbox(bad, 0.0001)
        assert err.value.status == 422


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
    with pytest.raises(limits.LimitError):
        limits.check_invite(None, "abc")


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


def test_a_per_address_limit_of_zero_is_off(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for _ in range(5):
        db.update_scan(conn, db.create_scan(conn, "mapillary", BBOX), client_ip_hash="h")
    limits.check_daily_scans(conn, "h", 0)
