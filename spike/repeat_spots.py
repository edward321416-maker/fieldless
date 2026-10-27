"""Spike: does Mapillary have photos of the same sidewalk spot from different dates (needed for repeat-obstacle spots)?"""
import math
from collections import defaultdict
from datetime import datetime, timezone

M_PER_DEG = 111_320.0
DAY_MS = 86_400_000


def cell_key(lat: float, lng: float, cell_m: float) -> tuple[int, int]:
    """Square grid cell of about cell_m metres (equirectangular, good enough inside one city district)."""
    x = lng * M_PER_DEG * math.cos(math.radians(round(lat, 1)))
    y = lat * M_PER_DEG
    return math.floor(x / cell_m), math.floor(y / cell_m)


def _day(ms_value: int) -> str:
    return datetime.fromtimestamp(ms_value / 1000, tz=timezone.utc).date().isoformat()


def multi_date_cells(images: list[dict], cell_m: float, min_gap_days: int) -> dict[tuple[int, int], dict]:
    """Cells holding flat photos whose capture dates span at least min_gap_days.

    Panoramas and images without geometry are skipped. Each value has the mean position, the number of distinct
    sequences and capture days, the first and last capture day, and the gap in days.
    """
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for im in images:
        geom = im.get("computed_geometry") or im.get("geometry")
        if im.get("is_pano") or not geom or im.get("captured_at") is None:
            continue
        lng, lat = geom["coordinates"]
        groups[cell_key(lat, lng, cell_m)].append({"lat": lat, "lng": lng, "t": im["captured_at"],
                                                   "seq": im.get("sequence")})
    cells = {}
    for key, pts in groups.items():
        first, last = min(p["t"] for p in pts), max(p["t"] for p in pts)
        gap = (last - first) // DAY_MS
        if gap < min_gap_days:
            continue
        cells[key] = {
            "lat": sum(p["lat"] for p in pts) / len(pts),
            "lng": sum(p["lng"] for p in pts) / len(pts),
            "sequences": len({p["seq"] for p in pts}),
            "days": len({_day(p["t"]) for p in pts}),
            "first": _day(first),
            "last": _day(last),
            "gap_days": gap,
        }
    return cells
