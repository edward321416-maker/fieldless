"""Entrance flow, second half (spec 7.2 E3b-E6). One Spark call reads the representative photo at full resolution:
the step count, and whether there is a step or sill with its box. A SAM box prompt outlines the sill, and the
stairs result and the reading become a step count and review reasons. The entrance layer records only steps
(spec revision 4): no door, ramp, handrail, height or verdict.

D1: Spark's count was exact at 7 of 13 entrances and within one at all 13. Spark also saw a sill at 4 of 5
entrances labelled flat, so a sill seen only by Spark is never trusted: it becomes "possible sill, needs review".
"""
from pathlib import Path

import cv2

from pipeline import config, mapillary
from pipeline.meta_api import image_part, text_part
from pipeline.sam_output import box_prompt, confident, parse, to_pixels

LOW_CONFIDENCE = 0.6  # spec 9.4

_BOX = {"type": "object", "additionalProperties": False, "required": ["x1", "y1", "x2", "y2"],
        "properties": {k: {"type": "integer"} for k in ("x1", "y1", "x2", "y2")}}
READ_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["step_count", "has_step_or_sill", "step_box", "confidence", "notes"],
    "properties": {
        "step_count": {"type": "integer"},
        "has_step_or_sill": {"type": "boolean"},
        "step_box": _BOX,
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
}

READ_PROMPT = """This photo shows a shop or building entrance seen from the sidewalk. Look at the most clearly visible entrance.
1) Count the steps (risers) a wheelchair user would have to climb from the sidewalk up to the door threshold. A raised threshold or lip of a few centimeters counts as 1 step. If the entrance is level with the sidewalk, return 0.
2) Say whether there is any step, sill or raised threshold between the sidewalk and the door, and give one bounding box around all the steps or the sill, in coordinates from 0 to 1000 relative to the image width (x) and height (y), origin top-left. Use zeros for the box if there is none.
3) Report your confidence from 0 to 1 and a one-sentence note on what you counted.
Return JSON only."""


def original_photo(image_id: str) -> Path:
    """The original-resolution photo (spec 7.2 E4), cached by image id."""
    folder = config.photo_cache() / "original"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{image_id}.jpg"
    if path.exists() and cv2.imread(str(path)) is not None:
        return path
    return mapillary.original_image(image_id, path)


def read_entrance(api, photo: Path) -> dict:
    return api.spark_json([text_part(READ_PROMPT), image_part(photo)], "entrance_read", READ_SCHEMA)


def outline_sill(api, photo: Path, reading: dict) -> dict | None:
    """SAM mask inside the box Spark drew around a step or sill: {"box", "conf", "masked"}, or None when Spark saw no
    step or gave no usable box."""
    if not reading["has_step_or_sill"]:
        return None
    img = cv2.imread(str(photo))
    h, w = img.shape[:2]
    px = to_pixels(reading["step_box"], w, h)
    if px is None:
        return None
    segs = confident(parse(api.sam_image(box_prompt([px], w, h), photo), video=False))
    return {"box": list(px), "conf": max((s.conf for s in segs if s.conf is not None), default=None),
            "masked": bool(segs)}


def measure(stairs_found: bool, reading: dict) -> dict:
    spark_steps = max(0, int(reading["step_count"]))
    reasons: list[str] = []
    if stairs_found:
        count = max(spark_steps, 2)  # D1: SAM drew stairs only at entrances with 2+ risers
        if spark_steps < 2:
            reasons.append("count_mismatch")
    elif spark_steps >= 2:
        count = spark_steps
        reasons.append("count_mismatch")  # Spark sees stairs that SAM did not outline
    elif reading["has_step_or_sill"] or spark_steps == 1:
        count = 1
        reasons.append("possible_sill")  # a few-cm sill cannot be confirmed from photos (D1 re-test)
    else:
        count = 0
    if reading["confidence"] < LOW_CONFIDENCE:
        reasons.append("low_confidence")
    return {"step_count": count, "review_reasons": reasons,
            "review_status": "needs_review" if reasons else "auto"}
