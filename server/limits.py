"""Request limits (spec 13): area size, optional invite code, and scans per address per day. Addresses are kept only
as a keyed hash."""
import hashlib
import hmac
import ipaddress
import os
from datetime import datetime, timedelta, timezone

from pipeline import db

# Trusted proxies in front of the server that append to X-Forwarded-For (Railway's edge: one). Check one request's
# header after the first deploy; behind no proxy (a bare `docker run -p`), set PROXY_HOPS=0 so the header is ignored.
def _proxy_hops() -> int:
    text = os.environ.get("PROXY_HOPS") or "1"
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"PROXY_HOPS: {text!r} is not a number") from None


PROXY_HOPS = _proxy_hops()
MAX_SIDE_DEG = 0.02  # about 1.8 km of longitude and 2.2 km of latitude in Seoul: no long slivers


class LimitError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _address_key(addr: str | None) -> str | None:
    """A parsed address as a stable key: IPv4 as is, IPv6 as its /64 network (one household's block). A port
    ("1.2.3.4:5678", "[2001:db8::1]:443") is dropped and an IPv4-mapped IPv6 address is read as IPv4. None if the text
    is not an address."""
    text = (addr or "").strip()
    if text.startswith("[") and "]" in text:
        text = text[1:text.index("]")]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_ip(headers, peer: str | None, proxy_hops: int = PROXY_HOPS) -> str:
    """The caller's address. Each trusted proxy appends the address it saw to X-Forwarded-For, so the entry
    proxy_hops from the right is what the nearest trusted proxy saw; entries further left come from the client and can
    be forged. Without a usable header, the socket peer is used."""
    forwarded = [p for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if proxy_hops > 0 and len(forwarded) >= proxy_hops:
        key = _address_key(forwarded[-proxy_hops])
        if key:
            return key
    return _address_key(peer) or peer or "unknown"


def ip_hash(ip: str, salt: str) -> str:
    """Keyed hash of an address (HMAC-SHA256, 32 hex characters); the address itself is never stored."""
    if not salt:
        raise ValueError("an address hash needs a non-empty salt (IP_HASH_SALT)")
    return hmac.new(salt.encode(), ip.encode(), hashlib.sha256).hexdigest()[:32]


def check_bbox(bbox: list[float], max_deg2: float) -> list[float]:
    if len(bbox) != 4:
        raise LimitError(422, "bbox needs four numbers: min_lng, min_lat, max_lng, max_lat")
    try:
        min_lng, min_lat, max_lng, max_lat = (float(v) for v in bbox)
    except (TypeError, ValueError):
        raise LimitError(422, "bbox needs four numbers: min_lng, min_lat, max_lng, max_lat") from None
    if not (-180 <= min_lng < max_lng <= 180 and -90 <= min_lat < max_lat <= 90):
        raise LimitError(422, "The area's corners are out of order or out of range")
    if (max_lng - min_lng) * (max_lat - min_lat) > max_deg2:
        raise LimitError(422, "This area is too large. Choose a rectangle about 1 km across or smaller.")
    if max_lng - min_lng > MAX_SIDE_DEG or max_lat - min_lat > MAX_SIDE_DEG:
        raise LimitError(422, "This area is too long. Choose a rectangle about 1 km across or smaller.")
    return [min_lng, min_lat, max_lng, max_lat]


def check_invite(given: str | None, expected: str | None) -> None:
    if expected and not (given and hmac.compare_digest(given.encode(), expected.encode())):
        raise LimitError(403, "Starting a survey needs an invite code")


def check_daily_scans(conn, hashed: str, limit: int, now: datetime | None = None) -> None:
    """At most limit scans per address in 24 hours; a limit of 0 turns this off."""
    if limit <= 0:
        return
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if db.scans_since(conn, hashed, since) >= limit:
        raise LimitError(429, f"At most {limit} surveys per day can be started from one address")
