"""SAM 3.1 text in and out: answers -> segments (frame, object, box, confidence, mask) decoded with Meta's
meta_sam_parser, and box prompts built from Spark's 0-1000 boxes (spec 7.2 E3b, D1 box re-test).

meta_sam_parser is pure Python: a 58 KB answer takes about 1 s to read and 1 s more to decode its masks
(2026-10-28). With 16 requests in flight that work held the GIL and slowed every other request's stream (a
sidewalk request went from 7.6 s to 24.5 s). So boxes are read with the grammar's own regular expressions
(boxes()), and answers whose masks are needed are decoded in other processes (parse_away())."""
import atexit
import os
import re
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

import cv2
import meta_sam_parser as msp
import numpy as np

CONF = 0.5  # D1: the confidence cut behind every gate number
MAX_BOXES = 16  # SAM accepts at most 16 prompted objects
# Processes that decode masks; THRESHOLD_PARSE_PROCESSES overrides (the live container has 2 CPUs).
PARSE_PROCESSES = int(os.environ.get("THRESHOLD_PARSE_PROCESSES") or max(1, min(8, (os.cpu_count() or 2) - 1)))
POOL_MIN_CHARS = 20_000  # a smaller answer is decoded in place: sending it to a process costs more than it saves
# The record grammar of meta_sam_parser (_segmentation.py): a line is a frame header <Nf> (optional ;fields)
# followed by records, each an object id and its <|box;...|> and <|mask;...|> tokens; payloads never contain "|".
_LINE = re.compile(r"<([0-9]+)f(?:;[^>]*)?>(.*)")
_RECORD = re.compile(r",?([0-9]+)((?:<\|[^|]*\|>)+)")
_TOKEN = re.compile(r"<\|([A-Za-z][A-Za-z0-9_.-]*)(?:;([^|]*))?\|>")


@dataclass(frozen=True)
class Seg:
    frame: int
    obj: str
    box: tuple[int, int, int, int]  # left, top, right, bottom in pixels; right and bottom are exclusive
    conf: float | None
    mask: np.ndarray | None = None  # bool, shape (bottom - top, right - left), aligned with box

    @property
    def area(self) -> int:
        if self.mask is not None:
            return int(self.mask.sum())
        left, top, right, bottom = self.box
        return max(0, right - left) * max(0, bottom - top)


def _frame(record) -> int:
    ref = getattr(record, "frame", None)
    return ref.frame_index if ref is not None else 0


def _mask(record) -> np.ndarray:
    """The record's mask at the size of its bounds. SAM sends large masks at reduced resolution (D1: a 1223x670
    box came with a 1024x561 mask), so the raster is scaled to the bounds with nearest-neighbour."""
    b = record.bounds
    raster = np.frombuffer(msp.decode_mask_to_raster(record.mask), np.uint8)
    raster = raster.reshape(record.mask.height, record.mask.width)
    width, height = b.right - b.left, b.bottom - b.top
    if raster.shape != (height, width):
        raster = cv2.resize(raster, (width, height), interpolation=cv2.INTER_NEAREST)
    return raster.astype(bool)


def _order(seg: Seg) -> tuple:
    return seg.frame, len(seg.obj), seg.obj  # "2" before "10"


def _fields(body: str | None) -> dict[str, str]:
    out = {}
    for part in (body or "").split(";"):
        key, _, value = part.strip().partition("=")
        out[key.strip()] = value.strip()
    return out


def boxes(text: str) -> list[Seg]:
    """The segments of a SAM answer without masks, as parse() gives them, read with regular expressions: the
    box's corners (x2 and y2 are inclusive in the text) and the mask's confidence, else the box's. Checked equal to
    meta_sam_parser on 495 real answers, 455 photos and 40 videos with 1,663 segments: 0.06 s against 305 s
    (2026-10-28). Raises ValueError for a record
    whose box lacks a corner."""
    segs: list[Seg] = []
    for raw in text.splitlines():
        line = _LINE.match(raw.strip())
        if not line:
            continue
        for record in _RECORD.finditer(line.group(2)):
            tokens = {name: _fields(body) for name, body in _TOKEN.findall(record.group(2))}
            box = tokens.get("box")
            if box is None:
                continue
            try:
                x1, y1, x2, y2 = (int(box[k]) for k in ("x1", "y1", "x2", "y2"))
            except (KeyError, ValueError):
                raise ValueError(f"SAM output has a malformed box: {record.group(0)[:120]}") from None
            conf = tokens.get("mask", {}).get("c") or box.get("c")
            segs.append(Seg(int(line.group(1)), record.group(1), (x1, y1, x2 + 1, y2 + 1),
                            float(conf) if conf else None))
    return sorted(segs, key=_order)


def parse(text: str, video: bool, masks: bool = True) -> list[Seg]:
    """Segments in a SAM answer, sorted by frame then object. An empty answer means nothing was found. masks=False
    keeps only boxes, read fast (boxes()). Raises ValueError when the parser reports an error (a malformed record)."""
    if not text.strip():
        return []
    if not masks:
        return boxes(text)
    fmt = msp.video_segmentation_format() if video else msp.image_segmentation_format()
    parser = fmt.create_parser()
    parser.push(text)
    result = parser.finish(msp.CompletedOutcome()).result
    errors = [d for d in result.diagnostics if d.severity == "error"]
    if errors:
        raise ValueError(f"SAM output has {len(errors)} malformed record(s): {errors[0].message} "
                         f"(line {errors[0].line})")
    box_records: dict[tuple[int, str], object] = {}
    segs: list[Seg] = []
    for record in result.records:
        key = (_frame(record), str(record.object_id))
        if record.kind == "box":
            box_records[key] = record
        elif record.kind == "mask":
            b = record.bounds
            segs.append(Seg(key[0], key[1], (b.left, b.top, b.right, b.bottom), record.confidence,
                            _mask(record) if masks else None))
    with_mask = {(s.frame, s.obj) for s in segs}
    for key, r in box_records.items():
        if key not in with_mask:
            segs.append(Seg(key[0], key[1], (r.left, r.top, r.right, r.bottom), r.confidence))
    return sorted(segs, key=_order)


_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def _shutdown() -> None:
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown)


def parse_away(text: str, video: bool) -> list[Seg]:
    """parse() with masks, in another process when the answer is large, so decoding never holds this process's GIL
    while other requests stream. Errors are raised as parse() raises them; a pool that broke is replaced and the
    answer is decoded here instead."""
    global _pool
    if PARSE_PROCESSES <= 1 or len(text) < POOL_MIN_CHARS:
        return parse(text, video)
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=PARSE_PROCESSES)
        pool = _pool
    try:
        return pool.submit(parse, text, video).result()
    except BrokenProcessPool:
        with _pool_lock:
            if _pool is pool:
                _pool = None
        return parse(text, video)


def confident(segs: list[Seg], threshold: float = CONF) -> list[Seg]:
    return [s for s in segs if s.conf is None or s.conf >= threshold]


def check_frames(segs: list[Seg], frames: int) -> None:
    """Raise ValueError if a segment's frame index does not fit a request of `frames` photos."""
    beyond = [s.frame for s in segs if s.frame >= frames]
    if beyond:
        raise ValueError(f"frame index {max(beyond)} out of range for {frames} photos")


def to_pixels(box: dict, w: int, h: int) -> tuple[int, int, int, int] | None:
    """A Spark 0-1000 box as inclusive pixel corners clamped to the image, or None if it has no area."""
    x1, x2 = (min(w - 1, max(0, int(box[k] * w / 1000))) for k in ("x1", "x2"))
    y1, y2 = (min(h - 1, max(0, int(box[k] * h / 1000))) for k in ("y1", "y2"))
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def box_prompt(boxes: list[tuple[int, int, int, int]], w: int, h: int) -> str:
    """SAM box-prompt text for one image: objects 1..n on frame 0 (dev.meta.ai/docs/sam/segmenting).
    Coordinates are inclusive pixel corners; w and h are the image size."""
    return "<0f>" + ",".join(f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}|>"
                             for i, (x1, y1, x2, y2) in enumerate(boxes[:MAX_BOXES], 1))
