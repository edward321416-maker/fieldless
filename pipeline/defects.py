"""Sidewalk flow, S7 (spec 7.3): tactile paving defects a surveyor can confirm from one photo.

The user chose the classes on 2026-10-27: blocks missing (or replaced by other paving), and a tactile line cut by a
cover (a manhole, utility cover or plate). Broken, worn or abruptly ending paving is not reported: the 2d check
(threshold/spike/out/durable_check.md) found fading unreliable, a line end needs the neighbouring photos, and a
surveyor could not confirm those from the photo without going on site. Fixed objects standing on the paving, the
third class, come from the S4/S5 events (pipeline.events.is_fixed).

Candidates come from the per-photo tactile results of S2 (sampled photos only): the photos where a run of tactile
paving starts or ends while the walk goes on, and every CHECK_EVERY-th photo inside a run. Spark judges each one on
its full-resolution photo. Every defect is for a person to confirm (needs_review) with its photo and Spark's box.

2b re-ran this prompt on the 38 labelled Seongsu stills (2026-10-27): missing blocks found at the crossing (64, not
63 from farther), the manhole cover found from near and far (82, 81), and no shown class on the worn or intact
photos.
"""
from pathlib import Path

from pipeline import geo
from pipeline.cost import CostCapReached
from pipeline.meta_api import error_text, image_part, text_part
from pipeline.parallel import run_parallel

ISSUE_TYPES = ("missing_blocks", "cover_cut")
DAMAGE_TO_ISSUE = {"missing": "missing_blocks", "cut_by_cover": "cover_cut"}
CHECK_EVERY = 4  # inside a run, every 4th sampled tactile photo is also checked
MERGE_GAP = 4  # photos: a same-type defect this close to the last one in a clip is the same defect

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tactile_visible", "line_end", "damage", "box", "confidence", "note"],
    "properties": {
        "tactile_visible": {"type": "boolean"},
        "line_end": {"type": "string", "enum": ["none", "normal", "abrupt", "unclear"]},
        "damage": {"type": "string",
                   "enum": ["none", "broken", "missing", "worn", "cut_by_cover", "covered", "unclear"]},
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
  "cut_by_cover" (a manhole, utility cover or metal plate sits in the tactile paving and cuts it),
  "covered" (covered by a mat, paint or debris), "unclear".
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


def defect_of(answer: dict) -> str | None:
    """The issue type for an answer that reports one of the two S7 classes, or None."""
    if not answer["tactile_visible"]:
        return None
    return DAMAGE_TO_ISSUE.get(answer["damage"])


def find_defects(api, cands: list[tuple[int, str]], photos: dict[int, Path], frames_by_idx: dict[int, dict],
                 clip_idxs: list[int], workers: int) -> tuple[list[dict], list[int], list[dict]]:
    """Issue rows for the candidates of one clip that Spark flags, the candidates whose check failed, and every answer (for evaluation)."""
    def one(item):
        try:
            return check_photo(api, photos[item[0]])
        except CostCapReached:
            raise
        except Exception as exc:  # this candidate only: reported as unavailable, not as a defect
            return {"error": error_text(exc)}

    rows: list[dict] = []
    failed: list[int] = []
    checks: list[dict] = []
    last_pos: dict[str, int] = {}
    for (idx, reason), answer in zip(cands, run_parallel(one, cands, workers)):
        if "error" in answer:
            failed.append(idx)
            continue
        checks.append({"frame_idx": idx, "candidate": reason, "check": answer})
        issue_type = defect_of(answer)
        if issue_type is None:
            continue
        pos = clip_idxs.index(idx)
        if issue_type in last_pos and pos - last_pos[issue_type] <= MERGE_GAP:
            continue  # the same defect seen again a few photos on
        last_pos[issue_type] = pos
        lat, lng = geo.issue_location(frames_by_idx, idx, idx)
        rows.append({"issue_type": issue_type, "object_label": None, "frame_start": idx, "frame_end": idx,
                     "frame_idx": idx, "lat": lat, "lng": lng, "confidence": answer["confidence"],
                     "review_status": "needs_review", "review_reasons": ["possible_defect"],
                     "model_outputs": {"check": answer, "candidate": reason},
                     "captured_at": frames_by_idx[idx]["captured_at"]})
    return rows, failed, checks
