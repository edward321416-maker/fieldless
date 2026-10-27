"""Mapillary API v4: search an area, rank photo sequences, fetch frames and download photos.

Ported from spike/mapillary.py. New: a search answered with HTTP 500 ("Please reduce the amount of data", seen in
D1 for dense areas) is split into quadrants like a full page, and each frame keeps its 2048-px photo URL.
"""
import random
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import httpx

from pipeline import config

MAX_BBOX_DEG2 = 0.01
MAX_RESULTS = 2000  # per request; a page this long may be truncated
MAX_SPLIT_DEPTH = 3
RETRY_ATTEMPTS = 5
SEARCH_FIELDS = "id,sequence,captured_at,is_pano,on_foot"
FRAME_FIELDS = ("id,sequence,captured_at,compass_angle,computed_compass_angle,geometry,computed_geometry,"
                "is_pano,on_foot,creator,thumb_2048_url")


class MapillaryError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def ms(date_iso: str) -> int:
    return int(datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _utc(ms_value: int) -> datetime:
    return datetime.fromtimestamp(ms_value / 1000, tz=timezone.utc)


def check_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lng, min_lat, max_lng, max_lat = bbox
    area = (max_lng - min_lng) * (max_lat - min_lat)
    if area >= MAX_BBOX_DEG2:
        raise ValueError(f"bbox area {area:.5f} must be under {MAX_BBOX_DEG2} square degrees")


def quadrants(bbox: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    min_lng, min_lat, max_lng, max_lat = bbox
    mid_lng, mid_lat = (min_lng + max_lng) / 2, (min_lat + max_lat) / 2
    return [(min_lng, min_lat, mid_lng, mid_lat), (mid_lng, min_lat, max_lng, mid_lat),
            (min_lng, mid_lat, mid_lng, max_lat), (mid_lng, mid_lat, max_lng, max_lat)]


def _retrying(request, retry_5xx: bool = True) -> httpx.Response:
    """Call request() until it returns a response that is not retryable: 429, transport errors and (if retry_5xx)
    5xx are retried with exponential backoff. After the last attempt the last response is returned, or the last
    transport error is raised."""
    for attempt in range(RETRY_ATTEMPTS):
        last = attempt == RETRY_ATTEMPTS - 1
        try:
            resp = request()
        except httpx.TransportError:
            if last:
                raise
        else:
            retry = resp.status_code == 429 or (retry_5xx and resp.status_code >= 500)
            if not retry or last:
                return resp
        time.sleep(min(30, 2 ** attempt) + random.random())
    raise RuntimeError("unreachable")


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "<redacted>") if token else text


def _get(path: str, params: dict, retry_5xx: bool = True) -> dict:
    token = config.mapillary_token()
    headers = {"Authorization": f"OAuth {token}"}
    url = f"{config.MAPILLARY_BASE_URL}/{path}"
    try:
        resp = _retrying(lambda: httpx.get(url, params=params, headers=headers, timeout=60), retry_5xx)
    except httpx.HTTPError as e:
        raise MapillaryError(_scrub(f"Mapillary request {path} failed: {type(e).__name__}: {e}", token)) from None
    if resp.status_code >= 400:
        body = _scrub(resp.text, token)[:300]  # scrub before truncating, so no token prefix survives the cut
        raise MapillaryError(_scrub(f"Mapillary HTTP {resp.status_code} for {path}: {body}", token),
                             resp.status_code)
    return resp.json()


def _search(bbox) -> tuple[list[dict], bool]:
    params = {"bbox": ",".join(f"{v:.6f}" for v in bbox), "fields": SEARCH_FIELDS, "limit": MAX_RESULTS}
    data = _get("images", params, retry_5xx=False).get("data", [])
    return data, len(data) >= MAX_RESULTS


def search_area(bbox: tuple[float, float, float, float], depth: int = 0) -> tuple[list[dict], bool]:
    """(images, capped). A full page or an HTTP 500 splits the bbox into quadrants, down to MAX_SPLIT_DEPTH;
    capped is True if some part was still full at the deepest level."""
    check_bbox(bbox)
    try:
        found, capped = _search(bbox)
    except MapillaryError as e:
        if e.status != 500 or depth >= MAX_SPLIT_DEPTH:
            raise
        found, capped = [], True
    if not capped:
        return found, False
    if depth >= MAX_SPLIT_DEPTH:
        return found, True
    merged = {str(img["id"]): img for img in found}
    still_capped = False
    for quadrant in quadrants(bbox):
        part, part_capped = search_area(quadrant, depth + 1)
        still_capped = still_capped or part_capped
        for img in part:
            merged.setdefault(str(img["id"]), img)
    return list(merged.values()), still_capped


def _flat(img: dict) -> bool:
    return not img.get("is_pano") and img.get("on_foot") is not False


def rank_sequences(images: list[dict], since_ms: int) -> list[str]:
    """Sequences with recent non-panorama photos: most pedestrian photos first, then most photos, then latest."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for img in images:
        if img["captured_at"] >= since_ms and _flat(img):
            groups[img["sequence"]].append(img)
    ranked = sorted(groups.items(), reverse=True, key=lambda item: (
        sum(1 for i in item[1] if i.get("on_foot") is True), len(item[1]), max(i["captured_at"] for i in item[1])))
    return [seq for seq, _ in ranked]


def sequence_frames(sequence_id: str) -> list[dict]:
    data = _get("images", {"sequence_ids": sequence_id, "fields": FRAME_FIELDS, "limit": MAX_RESULTS})
    return sorted(data.get("data", []), key=lambda i: i["captured_at"])


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
        "thumb_2048_url": img.get("thumb_2048_url"),
    }


def thumb_url(image_id: str) -> str | None:
    """A fresh 2048-px photo URL (the URLs in search results expire)."""
    return _get(image_id, {"fields": "thumb_2048_url"}).get("thumb_2048_url")


def download(url: str | None, dst: Path) -> bool:
    """Fetch url to dst. Retries 429, 5xx and transport errors; False on a permanent failure or after the last try."""
    if not url:
        return False
    try:
        resp = _retrying(lambda: httpx.get(url, timeout=60, follow_redirects=True))
    except httpx.TransportError:
        return False
    if resp.status_code != 200:
        return False
    dst = Path(dst)
    tmp = dst.with_name(f"{dst.name}.{threading.get_ident()}.part")
    tmp.write_bytes(resp.content)
    tmp.replace(dst)  # a reader never sees a half-written photo (the cache is shared by workers and scans)
    return True


def original_image(image_id: str, dst: Path, max_side: int = 2560) -> Path:
    """The original-resolution photo (spec 7.2 E4), scaled down to max_side if larger."""
    url = _get(image_id, {"fields": "thumb_original_url"}).get("thumb_original_url")
    if not download(url, dst):
        raise MapillaryError(f"could not download original for image {image_id}")
    img = cv2.imread(str(dst))
    if img is not None and max(img.shape[:2]) > max_side:
        scale = max_side / max(img.shape[:2])
        img = cv2.resize(img, (round(img.shape[1] * scale), round(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(dst), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return Path(dst)
