"""Stage 0a (spec 7.1): choose the photo sequences of an area, thin them by distance, and download the photos.

Pedestrian, non-panorama photos taken since 2020 come first (mapillary.rank_sequences). A sequence that mostly
re-walks a street already chosen is skipped, so the higher-ranked walk of a street wins. D1: the Myeongdong
sequence had a photo every 0.5 m, so photos are thinned to one every MIN_GAP_M.
"""
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from pipeline import config, geo, mapillary

SINCE = "2020-01-01"
MIN_GAP_M = 2.0
MIN_TURN_DEG = 15.0  # a close photo is kept when the walker turns: corners and crossings carry warning pads
MIN_SEQUENCE_PHOTOS = 10
OVERLAP_RADIUS_M = 10.0
MAX_OVERLAP_SHARE = 0.5
DOWNLOAD_WORKERS = 8


def in_bbox(rec: dict, bbox) -> bool:
    min_lng, min_lat, max_lng, max_lat = bbox
    if rec.get("lat") is None or rec.get("lng") is None:
        return False
    return min_lat <= rec["lat"] <= max_lat and min_lng <= rec["lng"] <= max_lng


def _turn(a: float | None, b: float | None) -> float:
    """Smallest angle between two compass headings in degrees; 0 when either is unknown."""
    if a is None or b is None:
        return 0.0
    d = abs(a - b) % 360
    return min(d, 360 - d)


def thin(frames: list[dict], min_gap_m: float = MIN_GAP_M, min_turn_deg: float = MIN_TURN_DEG) -> list[dict]:
    """Keep the first photo, then each photo at least min_gap_m from the last one kept, or turned at least
    min_turn_deg from it. A walker slows and turns at corners and crossings, where the tactile warning pads are; the
    D1 street lost its patched crossing (photos 63-64, under 1 m apart) to distance-only thinning."""
    kept: list[dict] = []
    for f in frames:
        last = kept[-1] if kept else None
        if (last is None or geo.haversine_m(last["lat"], last["lng"], f["lat"], f["lng"]) >= min_gap_m
                or _turn(last.get("compass"), f.get("compass")) >= min_turn_deg):
            kept.append(f)
    return kept


def overlap_share(frames: list[dict], chosen: list[dict]) -> float:
    """Share of frames within OVERLAP_RADIUS_M of some chosen frame."""
    if not frames or not chosen:
        return 0.0
    near = sum(1 for f in frames
               if any(geo.haversine_m(f["lat"], f["lng"], c["lat"], c["lng"]) <= OVERLAP_RADIUS_M for c in chosen))
    return near / len(frames)


def plan_clips(sequences: Iterable[list[dict]], max_photos: int) -> list[list[dict]]:
    """Sequences in rank order -> clips to process, within max_photos in total."""
    clips: list[list[dict]] = []
    chosen: list[dict] = []
    for frames in sequences:
        budget = max_photos - len(chosen)
        if budget < MIN_SEQUENCE_PHOTOS:
            break
        if len(frames) < MIN_SEQUENCE_PHOTOS or overlap_share(frames, chosen) > MAX_OVERLAP_SHARE:
            continue
        clip = frames[:budget]
        clips.append(clip)
        chosen += clip
    return clips


def candidate_sequences(bbox) -> Iterator[list[dict]]:
    """Ranked sequences of the area as thinned frame records inside the bbox, fetched one at a time as the caller
    asks for them (Mapillary API calls)."""
    since = mapillary.ms(SINCE)
    images, _ = mapillary.search_area(tuple(bbox))
    for seq in mapillary.rank_sequences(images, since):
        recs = [mapillary.frame_record(img) for img in mapillary.sequence_frames(seq)
                if not img.get("is_pano") and img.get("captured_at", 0) >= since]
        recs = thin([r for r in recs if in_bbox(r, bbox)])
        if recs:
            yield recs


def preview(bbox, max_photos: int = config.MAX_PHOTOS_PER_SCAN) -> dict:
    """What a scan of the area would cover and cost, without downloading photos (spec 5 step 1)."""
    frames = [f for clip in plan_clips(candidate_sequences(bbox), max_photos) for f in clip]
    times = sorted(f["captured_at"] for f in frames)
    return {"photos": len(frames), "sequences": len({f["sequence"] for f in frames}),
            "captured_from": times[0] if times else None, "captured_to": times[-1] if times else None,
            "est_cost_usd": round(len(frames) / 100 * config.EST_USD_PER_100_PHOTOS, 2)}


def photo_path(image_id: str) -> Path:
    return config.photo_cache() / f"{image_id}.jpg"


def _readable(path: Path) -> tuple[int, int] | None:
    img = cv2.imread(str(path)) if path.exists() else None
    return (img.shape[1], img.shape[0]) if img is not None else None


def fetch_photo(rec: dict) -> tuple[int, int] | None:
    """Make sure the frame's 2048-px photo is in the cache. Returns its (width, height), or None if it cannot be
    had. Photo URLs expire, so a failed download asks Mapillary for a fresh URL once."""
    path = photo_path(rec["id"])
    if size := _readable(path):
        return size
    if mapillary.download(rec.get("thumb_2048_url"), path) and (size := _readable(path)):
        return size
    try:
        fresh = mapillary.thumb_url(rec["id"])
    except mapillary.MapillaryError:
        fresh = None
    if mapillary.download(fresh, path) and (size := _readable(path)):
        return size
    path.unlink(missing_ok=True)
    return None


def collect(bbox, max_photos: int = config.MAX_PHOTOS_PER_SCAN) -> list[dict]:
    """Frames of the area with their photos in the cache. idx runs 0..n-1 in clip order; photos that could not be
    downloaded are left out."""
    clips = plan_clips(candidate_sequences(bbox), max_photos)
    records = [(clip_no, rec) for clip_no, clip in enumerate(clips) for rec in clip]
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        sizes = list(pool.map(lambda item: fetch_photo(item[1]), records))
    frames: list[dict] = []
    for (clip_no, rec), size in zip(records, sizes):
        if size is None:
            continue
        frames.append({"idx": len(frames), "clip": clip_no, "image_id": rec["id"], "sequence_id": rec["sequence"],
                       "lat": rec["lat"], "lng": rec["lng"], "compass": rec["compass"],
                       "captured_at": rec["captured_at"], "creator": rec["creator"],
                       "width": size[0], "height": size[1]})
    return frames
