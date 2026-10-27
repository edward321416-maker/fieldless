"""Entrance flow, first half (spec 7.2): merge Spark's entrance candidates (E2) and look for stairs with SAM on a
five-photo window around each entrance (E3). D1: SAM found every entrance with 2+ risers this way and never drew
stairs at a flat entrance; single-riser sills are left to the Spark reading (pipeline.entrance_read)."""
import re
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import geo, media
from pipeline.sam_output import check_frames, confident, parse

WINDOW = 2  # photos on each side of the entrance
STAIRS_PHRASE = "stairs"  # D1 best phrase (tied with "steps" and "staircase")
MERGE_GAP = 2  # photos, same clip
MERGE_DIST_M = 10.0  # different clips


@dataclass
class Candidate:
    frame_idx: int
    clip: int
    side: str
    name: str
    category: str
    has_visible_steps: bool
    merged: list[int] = field(default_factory=list)  # frame_idx of candidates folded into this one


def name_key(name: str) -> str:
    return re.sub(r"[\W_]+", "", name.casefold())


def _same(a: Candidate, b: Candidate, frames_by_idx: dict[int, dict]) -> bool:
    ka, kb = name_key(a.name), name_key(b.name)
    if a.clip == b.clip:
        if a.side != b.side:
            return False  # one walk never swaps left and right
        if abs(a.frame_idx - b.frame_idx) <= MERGE_GAP:
            return not ka or not kb or ka == kb
    if not ka or ka != kb:
        return False
    fa, fb = frames_by_idx[a.frame_idx], frames_by_idx[b.frame_idx]
    return geo.haversine_m(fa["lat"], fa["lng"], fb["lat"], fb["lng"]) <= MERGE_DIST_M


def merge_candidates(cands: list[dict], frames_by_idx: dict[int, dict]) -> list[Candidate]:
    """One Candidate per entrance; duplicates (neighbouring chunks, a street walked twice) are folded in."""
    kept: list[Candidate] = []
    for c in sorted(cands, key=lambda c: c["frame_idx"]):
        cand = Candidate(**c)
        twin = next((k for k in kept if _same(k, cand, frames_by_idx)), None)
        if twin is None:
            kept.append(cand)
        else:
            twin.merged.append(cand.frame_idx)
    return kept


def window_idxs(frame_idx: int, clip_idxs: list[int]) -> list[int]:
    pos = clip_idxs.index(frame_idx)
    return clip_idxs[max(0, pos - WINDOW):pos + WINDOW + 1]


@dataclass
class Stairs:
    found: bool
    conf: float | None
    best_idx: int | None
    box: tuple[int, int, int, int] | None  # photo pixels of best_idx


def detect_stairs(api, idxs: list[int], photos: dict[int, Path], sizes: dict[int, tuple[int, int]],
                  workdir: Path) -> Stairs:
    """SAM `stairs` on the window's photos as a 1 fps video; the frame with the largest confident mask wins."""
    workdir.mkdir(parents=True, exist_ok=True)
    video = workdir / f"stairs_{idxs[0]:04d}_{idxs[-1]:04d}.mp4"
    video_wh = media.images_to_mp4([photos[i] for i in idxs], video)
    with api.uploaded(video) as file_id:
        answer = api.sam_video(STAIRS_PHRASE, file_id, frames=len(idxs))
    segs = confident(parse(answer, video=True))
    check_frames(segs, len(idxs))
    if not segs:
        return Stairs(False, None, None, None)
    best = max(segs, key=lambda s: s.area)
    best_idx = idxs[best.frame]
    box = media.seg_to_photo(best, video_wh, sizes[best_idx]).box
    return Stairs(True, max(s.conf if s.conf is not None else 1.0 for s in segs), best_idx, box)
