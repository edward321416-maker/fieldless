"""Positions on the ground (spec 7.4 step 9): distances, offsets, and where to pin entrances and sidewalk issues.
Times and positions are never guessed by a model: they come from photo numbers and photo coordinates."""
import math

EARTH_RADIUS_M = 6371008.8
ENTRANCE_OFFSET_M = 4.0  # spec 7.4: the entrance sits about 4 m to the side the camera saw it on
SIDE_BEARING = {"left": -90.0, "right": 90.0, "center": 0.0}


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def offset_point(lat: float, lng: float, bearing_deg: float, meters: float) -> tuple[float, float]:
    d = meters / EARTH_RADIUS_M
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lng)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def entrance_location(frame: dict, side: str) -> tuple[float, float, str]:
    """Photo position moved ENTRANCE_OFFSET_M toward the side of the frame the entrance was on."""
    if frame.get("compass") is None or side not in SIDE_BEARING:
        return frame["lat"], frame["lng"], "image_position"
    lat, lng = offset_point(frame["lat"], frame["lng"], frame["compass"] + SIDE_BEARING[side], ENTRANCE_OFFSET_M)
    return lat, lng, "image_offset"


def issue_location(frames_by_idx: dict[int, dict], start_idx: int, end_idx: int) -> tuple[float, float]:
    """Position of the middle photo of a sidewalk issue's run of photos."""
    mid = frames_by_idx[(start_idx + end_idx) // 2]
    return mid["lat"], mid["lng"]
