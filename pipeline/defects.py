"""Sidewalk flow, S7 (spec 7.3): possible breaks and damage in the tactile paving.

Candidates come from the per-photo tactile results of S2 (sampled photos only): the photos where a run of tactile
paving starts or ends while the walk goes on, and every CHECK_EVERY-th photo inside a run. Spark judges each
candidate on its full-resolution photo, with the prompt the 2d session checked on 38 Seongsu stills
(threshold/spike/out/durable_check.md). It raised no structural alarm on 18 intact photos and found 2 of 3
structural spots when fixed covers count, but fading was unreliable and the sample was small. So "worn" is never a
defect, and every defect is a possible defect for a person to confirm (needs_review), never an automatic verdict.
"""
from pathlib import Path

from pipeline import geo
from pipeline.cost import CostCapReached
from pipeline.meta_api import error_text, image_part, text_part
from pipeline.parallel import run_parallel

ISSUE_TYPES = ("break", "damage")
CHECK_EVERY = 4  # inside a run, every 4th sampled tactile photo is also checked for damage
MERGE_GAP = 4  # photos: a same-type defect this close to the last one in a clip is the same defect
DEFECT_DAMAGE = ("broken", "missing", "covered")  # "worn" is unreliable from one photo (2d check: 1 of 7)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tactile_visible", "line_end", "damage", "box", "confidence", "note"],
    "properties": {
        "tactile_visible": {"type": "boolean"},
        "line_end": {"type": "string", "enum": ["none", "normal", "abrupt", "unclear"]},
        "damage": {"type": "string", "enum": ["none", "broken", "missing", "worn", "covered", "unclear"]},
        "box": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
}

PROMPT = """A pedestrian took this photo on a sidewalk in Korea. Look only at the yellow tactile paving on the ground
(raised blocks for blind pedestrians: bar blocks form a guide line, dot blocks mark warnings and turns).
Answer as JSON:
- tactile_visible: whether yellow tactile paving is visible on the ground.
- line_end: if a tactile guide line ends in this photo, how it ends. "none": no end is visible (it continues, or
  leaves the picture). "normal": it ends at a crosswalk, curb, building entrance, stairs or a dot-block pad.
  "abrupt": it stops in the middle of the sidewalk for no visible reason, or a section of it is missing.
  "unclear": you cannot tell.
- damage: the worst condition of the visible tactile blocks. "none", "broken" (cracked, chipped or sunken blocks),
  "missing" (blocks missing or replaced by other paving), "worn" (color faded or bumps worn flat),
  "covered" (covered by a plate, mat, paint or debris), "unclear".
- box: the problem area (abrupt end or damage) as [x1, y1, x2, y2] on a 0-1000 scale of the image, or [] if none.
- confidence: 0 to 1 for your line_end and damage answers.
- note: one sentence on what you see."""


def candidates(flags: dict[int, bool | None], clip_idxs: list[int],
               every: int = CHECK_EVERY) -> list[tuple[int, str]]:
    """(photo, reason) pairs to check, in clip order. flags holds the sampled photos: True if tactile paving was
    found, False if not, None if the request failed. A run edge counts only next to a photo known to be empty, so
    the start or end of the walk and a failed request never make a candidate."""
    order = [i for i in clip_idxs if i in flags]
    out: list[tuple[int, str]] = []
    run_pos = -1
    for k, idx in enumerate(order):
        if not flags[idx]:
            run_pos = -1
            continue
        run_pos += 1
        prev = flags[order[k - 1]] if k > 0 else None
        nxt = flags[order[k + 1]] if k + 1 < len(order) else None
        if prev is False or nxt is False:
            out.append((idx, "edge"))
        elif run_pos % every == every // 2:
            out.append((idx, "sample"))
    return out


def check_photo(api, photo: Path) -> dict:
    return api.spark_json([image_part(photo), text_part(PROMPT)], "tactile_condition", SCHEMA)


def defect_of(answer: dict) -> tuple[str, str | None] | None:
    """(issue_type, kind) for an answer that reports a defect, or None."""
    if not answer["tactile_visible"]:
        return None
    if answer["line_end"] == "abrupt":
        return "break", None
    if answer["damage"] in DEFECT_DAMAGE:
        return "damage", answer["damage"]
    return None


def find_defects(api, cands: list[tuple[int, str]], photos: dict[int, Path], frames_by_idx: dict[int, dict],
                 clip_idxs: list[int], workers: int) -> tuple[list[dict], list[int]]:
    """Issue rows for the candidates of one clip that Spark flags, and the candidates whose check failed."""
    def one(item):
        try:
            return check_photo(api, photos[item[0]])
        except CostCapReached:
            raise
        except Exception as exc:  # this candidate only: reported as unavailable, not as a defect
            return {"error": error_text(exc)}

    rows: list[dict] = []
    failed: list[int] = []
    last_pos: dict[str, int] = {}
    for (idx, reason), answer in zip(cands, run_parallel(one, cands, workers)):
        if "error" in answer:
            failed.append(idx)
            continue
        found = defect_of(answer)
        if found is None:
            continue
        issue_type, kind = found
        pos = clip_idxs.index(idx)
        if issue_type in last_pos and pos - last_pos[issue_type] <= MERGE_GAP:
            continue  # the same defect seen again a few photos on
        last_pos[issue_type] = pos
        lat, lng = geo.issue_location(frames_by_idx, idx, idx)
        rows.append({"issue_type": issue_type, "object_label": kind, "frame_start": idx, "frame_end": idx,
                     "frame_idx": idx, "lat": lat, "lng": lng, "confidence": answer["confidence"],
                     "review_status": "needs_review", "review_reasons": ["possible_defect"],
                     "model_outputs": {"check": answer, "candidate": reason},
                     "captured_at": frames_by_idx[idx]["captured_at"]})
    return rows, failed
