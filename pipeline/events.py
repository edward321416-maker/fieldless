"""Sidewalk flow, events (spec 7.3 S4, S5): an obstacle standing on tactile paving in at least MIN_RUN photos in a
row becomes an event, and Spark checks the event's middle photo.

"Standing on" uses the obstacle's footprint (the bottom FOOTPRINT of its mask) against the tactile mask grown by
DILATE_PX, so an object beside the line that hides part of it in the picture does not count. D1 had no obstacle on
tactile paving, so these constants are first guesses, to be tuned on the expanded truth set (plan 3)."""
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from pipeline import media
from pipeline.meta_api import image_part, text_part
from pipeline.sam_output import Seg
from pipeline.scene import Chunk

OVERLAP_MIN = 0.2
FOOTPRINT = 0.2
DILATE_PX = 8
MIN_RUN = 2
LOW_CONFIDENCE = 0.6
# Objects that stay where they stand: on tactile paving they are durable defects (spec 9.3), not dated facts.
FIXED_OBJECT = re.compile(r"\b(bollard|pole|post|street ?light|lamp|tree|planter|cabinet|hydrant|bench)(?:es|s)?\b")

CHECK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["blocked", "object", "damage_visible", "confidence", "notes"],
    "properties": {
        "blocked": {"type": "boolean"},
        "object": {"type": "string"},
        "damage_visible": {"type": "boolean"},
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
}

CHECK_PROMPT = """This street photo comes from a survey of tactile paving (the raised guide blocks for blind pedestrians, usually yellow).
Is the tactile paving actually blocked by an object standing on it? Name the object in a short English noun phrase.
Also say whether the tactile paving looks damaged or interrupted, give your confidence from 0 to 1, and a one-sentence note.
Return JSON only."""


def _mask(s: Seg) -> np.ndarray:
    left, top, right, bottom = s.box
    return s.mask if s.mask is not None else np.ones((bottom - top, right - left), bool)


def footprint(s: Seg) -> np.ndarray:
    """The bottom FOOTPRINT share of the segment's mask: where the object stands."""
    mask = _mask(s)
    foot = np.zeros_like(mask)
    start = int(round(mask.shape[0] * (1 - FOOTPRINT)))
    foot[start:] = mask[start:]
    return foot


def overlap_ratio(obstacle: Seg, tactile: list[Seg]) -> float:
    """Share of the obstacle's footprint lying on tactile paving grown by DILATE_PX. Both in photo pixels."""
    foot = footprint(obstacle)
    total = int(foot.sum())
    if total == 0:
        return 0.0
    left, top, right, bottom = obstacle.box
    d = DILATE_PX
    # canvas: the obstacle box padded by d on every side; canvas pixel (0, 0) is photo pixel (left - d, top - d)
    canvas = np.zeros((bottom - top + 2 * d, right - left + 2 * d), bool)
    for t in tactile:
        tl, tt, tr, tb = t.box
        x0, x1 = max(tl, left - d), min(tr, right + d)
        y0, y1 = max(tt, top - d), min(tb, bottom + d)
        if x0 >= x1 or y0 >= y1:
            continue
        canvas[y0 - top + d:y1 - top + d, x0 - left + d:x1 - left + d] |= _mask(t)[y0 - tt:y1 - tt, x0 - tl:x1 - tl]
    if not canvas.any():
        return 0.0
    if d:
        kernel = np.ones((2 * d + 1, 2 * d + 1), np.uint8)
        canvas = cv2.dilate(canvas.astype(np.uint8), kernel).astype(bool)
    on = canvas[d:d + bottom - top, d:d + right - left]
    return float((foot & on).sum() / total)


def tactile_for(idx: int, tactile: dict[int, list | None], clip_idxs: list[int]) -> list | None:
    """Tactile segments of a photo. An unsampled photo borrows from the nearest sampled photo of its clip (the
    earlier one on a tie). None: unavailable."""
    if idx in tactile:
        return tactile[idx]
    pos = clip_idxs.index(idx)
    for d in range(1, len(clip_idxs)):
        for p in (pos - d, pos + d):
            if 0 <= p < len(clip_idxs) and clip_idxs[p] in tactile:
                return tactile[clip_idxs[p]]
    return None


def chunk_ratios(obstacles: dict[str, dict[int, list[Seg]] | None], chunk: Chunk,
                 tactile: dict[int, list[Seg] | None], frames_by_idx: dict[int, dict],
                 clip_idxs: list[int]) -> dict[str, dict[int, float]]:
    """Per label and photo, the highest overlap ratio of that label's obstacles with the photo's tactile paving.
    Photos with no tactile result are skipped (unavailable, not zero)."""
    out: dict[str, dict[int, float]] = {}
    for label, by_idx in obstacles.items():
        if by_idx is None:
            continue
        for idx, segs in by_idx.items():
            paving = tactile_for(idx, tactile, clip_idxs)
            if paving is None:
                continue
            photo_wh = (frames_by_idx[idx]["width"], frames_by_idx[idx]["height"])
            ratio = max(overlap_ratio(media.seg_to_photo(s, (chunk.width, chunk.height), photo_wh), paving)
                        for s in segs)
            out.setdefault(label, {})[idx] = ratio
    return out


@dataclass
class Event:
    label: str
    clip: int
    start_idx: int
    end_idx: int
    rep_idx: int
    max_ratio: float


def find_events(ratios: dict[str, dict[int, float]], clip: int, clip_idxs: list[int]) -> list[Event]:
    """Runs of at least MIN_RUN photos in a row where a label's ratio reaches OVERLAP_MIN."""
    found: list[Event] = []
    for label, by_idx in ratios.items():
        run: list[int] = []
        for idx in [*clip_idxs, None]:
            if idx is not None and by_idx.get(idx, 0.0) >= OVERLAP_MIN:
                run.append(idx)
                continue
            if len(run) >= MIN_RUN:
                found.append(Event(label, clip, run[0], run[-1], run[len(run) // 2], max(by_idx[i] for i in run)))
            run = []
    return found


def check_event(api, photo: Path) -> dict:
    return api.spark_json([text_part(CHECK_PROMPT), image_part(photo)], "event_check", CHECK_SCHEMA)


def review(check: dict) -> tuple[str | None, list[str]]:
    """(review_status, reasons) for an event after the Spark check; status None means drop the event."""
    if not check["blocked"]:
        if check["confidence"] >= LOW_CONFIDENCE:
            return None, []
        return "needs_review", ["spark_unsure"]
    if check["confidence"] < LOW_CONFIDENCE:
        return "needs_review", ["low_confidence"]
    return "auto", []


def is_fixed(label: str) -> bool:
    """Whether an obstacle label names a fixed object (a bollard, pole, street light and the like)."""
    return FIXED_OBJECT.search(label.lower()) is not None
