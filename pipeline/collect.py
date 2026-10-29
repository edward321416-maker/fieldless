"""Stage 0a (spec 7.1): choose the photos of an area from its walks, thin them by distance, and download them.

Sequences with the most pedestrian, non-panorama photos taken since 2020 inside the area are ranked first. D1: the
Myeongdong sequence had a photo every 0.5 m, so photos are thinned to one every MIN_GAP_M. The walks are then cut
into sections, and the sections that bring the most ground into view that no chosen section shows yet are taken
first (plan_clips, docs/specs/2026-10-29-walk-sections-design.md).
"""
import math
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from pipeline import config, geo, mapillary
from pipeline.parallel import run_parallel

SINCE = "2020-01-01"
MIN_GAP_M = 2.0
MIN_TURN_DEG = 15.0  # a close photo is kept when the walker turns: corners and crossings carry warning pads
MIN_SEQUENCE_PHOTOS = 10
MAX_CANDIDATES = 40  # sequences fetched whole to be ranked (Capitol Hill has about 20)
# A section is up to one video chunk (scene.CHUNK): SAM bills a video request at least config.SAM_MIN_FRAMES frames,
# so a section is valued per billed frame. In Capitol Hill, 181 photos chosen this way brought 158 of the city's
# points of five items into view against 112 for whole walks in rank order, and 1.17 km against 0.89 (2026-10-29,
# Mapillary metadata only; the city's points scored the choice, they never made it).
SECTION_PHOTOS = 60
SECTION_STEP = 10  # a window starts every this many photos, so a section can begin where the new ground begins
VIEW_CELL_M = 4.0  # the ground is counted in squares this big
VIEW_REACH_M = 20.0  # and seen up to this far: eval/city.py's COVER_M
VIEW_FOV_DEG = 70.0  # inside this angle around the compass: eval/city.py's FOV
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


def view_cells(frames: list[dict], lat0: float) -> set[tuple[int, int]]:
    """The VIEW_CELL_M squares of ground the photos look at: within VIEW_REACH_M and VIEW_FOV_DEG of the compass. A
    photo without a compass looks at nothing. lat0 fixes the grid's scale for the area."""
    across = 111_320 * math.cos(math.radians(lat0)) / VIEW_CELL_M  # cells per degree of longitude
    along = 111_320 / VIEW_CELL_M
    cells = set()
    for f in frames:
        if f.get("compass") is None or f.get("lat") is None or f.get("lng") is None:
            continue
        for metres in range(2, int(VIEW_REACH_M) + 1, 2):
            for turn in range(-int(VIEW_FOV_DEG / 2), int(VIEW_FOV_DEG / 2) + 1, 7):
                lat, lng = geo.offset_point(f["lat"], f["lng"], f["compass"] + turn, metres)
                cells.add((math.floor(lng * across), math.floor(lat * along)))
    return cells


def plan_clips(sequences: Iterable[list[dict]], max_photos: int) -> list[list[dict]]:
    """Sequences in rank order -> clips to process, within max_photos in total.

    Windows of SECTION_PHOTOS run along every walk, one starting every SECTION_STEP photos. The window whose photos not
    yet chosen bring the most ground into view that no chosen photo shows, per frame SAM would bill for them, is taken
    next (rank breaks ties), until the budget is spent or no window shows anything new; the last one is cut to what
    the budget has left. The chosen photos of a walk that follow each other make one clip; clips keep the walks' rank
    order. Sequences shorter than MIN_SEQUENCE_PHOTOS are left out."""
    walks = [frames for frames in sequences if len(frames) >= MIN_SEQUENCE_PHOTOS]
    if not walks:
        return []
    lat0 = walks[0][0]["lat"]
    ground = [[view_cells([f], lat0) for f in frames] for frames in walks]
    windows = [(rank, range(start, min(start + SECTION_PHOTOS, len(frames))))
               for rank, frames in enumerate(walks) for start in range(0, len(frames), SECTION_STEP)
               if min(start + SECTION_PHOTOS, len(frames)) - start >= MIN_SEQUENCE_PHOTOS]
    chosen: list[set[int]] = [set() for _ in walks]
    seen: set[tuple[int, int]] = set()
    used = 0
    while max_photos - used >= MIN_SEQUENCE_PHOTOS:
        best, best_worth = None, 0.0
        for rank, window in windows:
            new = [i for i in window if i not in chosen[rank]]
            if not new:
                continue
            worth = len(set().union(*(ground[rank][i] for i in new)) - seen) / max(len(new), config.SAM_MIN_FRAMES)
            if worth > best_worth:  # strictly: an equal window of a lower rank does not replace it
                best, best_worth = (rank, new), worth
        if best is None:
            break  # every window left re-walks ground already in view
        rank, new = best
        new = new[:max_photos - used]
        chosen[rank].update(new)
        used += len(new)
        seen.update(*(ground[rank][i] for i in new))
    clips: list[list[dict]] = []
    for rank, idxs in enumerate(chosen):
        run: list[int] = []
        for i in sorted(idxs):
            if run and i != run[-1] + 1:
                clips.append([walks[rank][j] for j in run])
                run = []
            run.append(i)
        if run:
            clips.append([walks[rank][j] for j in run])
    return clips


def candidate_sequences(bbox) -> list[list[dict]]:
    """The area's sequences as thinned frame records inside the bbox, most pedestrian photos first (Mapillary API
    calls). A bbox search returns a different part of the photos each time, so it only finds the sequences; each is
    ranked by all of its own recent flat frames inside the bbox, so the same area gives the same photos."""
    since = mapillary.ms(SINCE)
    images, _ = mapillary.search_grid(tuple(bbox))
    found = mapillary.rank_sequences(images, since)[:MAX_CANDIDATES]
    ranked = []
    for seq, frames in zip(found, run_parallel(mapillary.sequence_frames, found, mapillary.SEARCH_WORKERS)):
        inside = [img for img in frames if not img.get("is_pano") and img.get("captured_at", 0) >= since
                  and in_bbox(mapillary.frame_record(img), bbox)]
        recs = thin([mapillary.frame_record(img) for img in inside])
        if recs:
            on_foot = sum(1 for img in inside if img.get("on_foot") is True)
            flat = sum(1 for img in inside if img.get("on_foot") is not False)
            ranked.append(((on_foot, flat, max(img["captured_at"] for img in inside), str(seq)), recs))
    return [recs for _, recs in sorted(ranked, key=lambda item: item[0], reverse=True)]


def preview(bbox, max_photos: int = config.MAX_PHOTOS_PER_SCAN, checks: int = 0) -> dict:
    """What a scan of the area would cover and cost with a standard of this many photo checks, without downloading
    photos (spec 5 step 1)."""
    clips = plan_clips(candidate_sequences(bbox), max_photos)
    frames = [f for clip in clips for f in clip]
    times = sorted(f["captured_at"] for f in frames)
    return {"photos": len(frames), "sequences": len({f["sequence"] for f in frames}),
            "captured_from": times[0] if times else None, "captured_to": times[-1] if times else None,
            "est_cost_usd": round(len(frames) / 100 * config.est_usd_per_100_photos(checks), 2),
            # the walks the survey would follow, for the map (the photos are public already)
            "walks": [[[f["lng"], f["lat"]] for f in clip if f["lat"] is not None and f["lng"] is not None]
                      for clip in clips]}


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
