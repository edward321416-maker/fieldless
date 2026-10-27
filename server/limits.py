"""Request limits (spec 13): area size, optional invite code, and scans per address per day. Addresses are kept only
as a salted hash."""
import hashlib
from datetime import datetime, timedelta, timezone

from pipeline import db


class LimitError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def client_ip(headers, peer: str | None) -> str:
    """The caller's address; behind Railway's proxy it is the first X-Forwarded-For entry."""
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return peer or "unknown"


def ip_hash(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


def check_bbox(bbox: list[float], max_deg2: float) -> list[float]:
    if len(bbox) != 4:
        raise LimitError(422, "bbox needs four numbers: min_lng, min_lat, max_lng, max_lat")
    min_lng, min_lat, max_lng, max_lat = (float(v) for v in bbox)
    if not (-180 <= min_lng < max_lng <= 180 and -90 <= min_lat < max_lat <= 90):
        raise LimitError(422, "The area's corners are out of order or out of range")
    if (max_lng - min_lng) * (max_lat - min_lat) > max_deg2:
        raise LimitError(422, "This area is too large. Choose a rectangle about 1 km across or smaller.")
    return [min_lng, min_lat, max_lng, max_lat]


def check_invite(given: str | None, expected: str | None) -> None:
    if expected and given != expected:
        raise LimitError(403, "Starting a survey needs an invite code")


def check_daily_scans(conn, hashed: str, limit: int, now: datetime | None = None) -> None:
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if db.scans_since(conn, hashed, since) >= limit:
        raise LimitError(429, f"At most {limit} surveys per day can be started from one address")
