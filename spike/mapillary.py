"""Mapillary API v4 helpers: search an area, rank photo sequences, download photos."""
import math
import random
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import httpx

from spike import config

MAX_BBOX_DEG2 = 0.01
SEARCH_FIELDS = "id,sequence,captured_at,is_pano,on_foot"
FRAME_FIELDS = ("id,sequence,captured_at,compass_angle,computed_compass_angle,geometry,computed_geometry,"
                "is_pano,on_foot,creator,thumb_1024_url,thumb_2048_url")


def ms(date_iso: str) -> int:
    return int(datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _utc(ms_value: int) -> datetime:
    return datetime.fromtimestamp(ms_value / 1000, tz=timezone.utc)


def bbox_area(bbox: tuple[float, float, float, float]) -> float:
    min_lng, min_lat, max_lng, max_lat = bbox
    return (max_lng - min_lng) * (max_lat - min_lat)


def check_bbox(bbox: tuple[float, float, float, float]) -> None:
    if bbox_area(bbox) >= MAX_BBOX_DEG2:
        raise ValueError(f"bbox area {bbox_area(bbox):.5f} must be under {MAX_BBOX_DEG2} square degrees")


def _get(path: str, params: dict) -> dict:
    headers = {"Authorization": f"OAuth {config.mapillary_token()}"}
    for attempt in range(5):
        try:
            resp = httpx.get(f"{config.MAPILLARY_BASE_URL}/{path}", params=params, headers=headers, timeout=60)
        except httpx.TransportError:
            if attempt == 4:
                raise
        else:
            if resp.status_code != 429 and resp.status_code < 500:
                resp.raise_for_status()
                return resp.json()
            if attempt == 4:
                resp.raise_for_status()
        time.sleep(min(30, 2 ** attempt) + random.random())
    raise RuntimeError("unreachable")


def search_images(bbox: tuple[float, float, float, float]) -> list[dict]:
    check_bbox(bbox)
    params = {"bbox": ",".join(f"{v:.6f}" for v in bbox), "fields": SEARCH_FIELDS, "limit": 2000}
    return _get("images", params).get("data", [])


def _flat(img: dict) -> bool:
    return not img.get("is_pano") and img.get("on_foot") is not False


def summarize(images: list[dict], since_ms: int) -> dict:
    recent = [i for i in images if i["captured_at"] >= since_ms]
    return {
        "total": len(images),
        "capped": len(images) >= 2000,
        "recent": len(recent),
        "recent_on_foot_flat": sum(1 for i in recent if _flat(i) and i.get("on_foot") is True),
        "recent_unclassified_flat": sum(1 for i in recent if _flat(i) and i.get("on_foot") is None),
        "pano_ratio": (sum(1 for i in images if i.get("is_pano")) / len(images)) if images else 0.0,
        "by_year": dict(sorted(Counter(str(_utc(i["captured_at"]).year) for i in images).items())),
    }


def rank_sequences(images: list[dict], since_ms: int) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for img in images:
        if img["captured_at"] >= since_ms and _flat(img):
            groups[img["sequence"]].append(img)
    ranked = [
        {
            "sequence": seq,
            "recent_on_foot_flat": sum(1 for i in imgs if i.get("on_foot") is True),
            "recent_flat": len(imgs),
            "latest": _utc(max(i["captured_at"] for i in imgs)).strftime("%Y-%m-%d"),
        }
        for seq, imgs in groups.items()
    ]
    return sorted(ranked, key=lambda r: (r["recent_on_foot_flat"], r["recent_flat"], r["latest"]), reverse=True)


def sequence_frames(sequence_id: str) -> list[dict]:
    data = _get("images", {"sequence_ids": sequence_id, "fields": FRAME_FIELDS, "limit": 2000}).get("data", [])
    return sorted(data, key=lambda i: i["captured_at"])


def frame_record(img: dict) -> dict:
    geometry = img.get("computed_geometry") or img.get("geometry") or {}
    lng, lat = (geometry.get("coordinates") or [None, None])[:2]
    compass = img.get("computed_compass_angle")
    return {
        "id": str(img["id"]),
        "sequence": img.get("sequence"),
        "lat": lat,
        "lng": lng,
        "compass": compass if compass is not None else img.get("compass_angle"),
        "captured_at": _utc(img["captured_at"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "creator": (img.get("creator") or {}).get("username"),
    }


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def track_km(frames: list[dict]) -> float:
    points = [(f["lat"], f["lng"]) for f in frames if f.get("lat") is not None and f.get("lng") is not None]
    return sum(_haversine_m(*a, *b) for a, b in zip(points, points[1:])) / 1000


def download(url: str | None, dst: Path) -> bool:
    if not url:
        return False
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    if resp.status_code != 200:
        return False
    Path(dst).write_bytes(resp.content)
    return True


def original_image(image_id: str, dst: Path, max_side: int = 2560) -> Path:
    url = _get(image_id, {"fields": "thumb_original_url"}).get("thumb_original_url")
    if not download(url, dst):
        raise RuntimeError(f"could not download original for image {image_id}")
    img = cv2.imread(str(dst))
    if img is not None and max(img.shape[:2]) > max_side:
        scale = max_side / max(img.shape[:2])
        img = cv2.resize(img, (round(img.shape[1] * scale), round(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(dst), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return Path(dst)
