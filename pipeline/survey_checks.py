"""Checks: survey items written as sentences (design docs/specs/2026-10-28-us-survey-items-design.md §4.3).

SAM 3.1 looks for a check's `find` on every second photo of each walk. Sampled photos in a row that show it are one
object, seen best where its mask is largest (a check with `group_m` cuts a long run into pieces of that many metres,
one object each, for things that stand in rows); sightings from different walks within MERGE_M are the same object.
Muse Spark answers the check's `ask` on that best photo. One sidewalk_issue row per object, in the format agreed
with eval on 2026-10-28 (plan 4, Global Constraints)."""
from pathlib import Path

from pipeline import geo
from pipeline.cost import CostCapReached
from pipeline.meta_api import error_text, image_part, text_part
from pipeline.parallel import run_parallel
from pipeline.sam_output import confident, parse
from pipeline.survey_items import Check

SAMPLE_EVERY = 2
MERGE_M = 8.0  # objects from different walks closer than this are the same object

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["visible", "answer", "box", "confidence", "note"],
    "properties": {
        "visible": {"type": "boolean"},
        "answer": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "box": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
}

PROMPT = """A pedestrian took this photo on a sidewalk.
{ask}
Answer as JSON:
- visible: whether the {target} is visible in the photo.
- answer: "yes", "no", or "unclear" if the photo does not show it well enough to tell.
- box: the {target} as [x1, y1, x2, y2] on a 0-1000 scale of the image, or [] if it is not visible.
- confidence: 0 to 1 for your answer.
- note: one sentence on what you see."""


def prompt(check: Check) -> str:
    return PROMPT.format(ask=check.ask, target=check.target)


def find_areas(api, check: Check, idxs: list[int], photos: dict[int, Path], workers: int,
               known: dict[int, int] | None = None) -> dict[int, int | None]:
    """Largest confident mask area of the check's `find` per photo: 0 when not found, None when the request failed.
    Photos in `known` (areas measured earlier for this same `find`) are not asked again."""
    def one(idx):
        try:
            segs = confident(parse(api.sam_image(check.find, photos[idx]), video=False))
        except CostCapReached:
            raise
        except Exception:
            return None
        return max((s.area for s in segs), default=0)

    known = known or {}
    todo = [i for i in idxs if i not in known]
    fresh = dict(zip(todo, run_parallel(one, todo, workers)))
    return {i: known[i] if i in known else fresh[i] for i in idxs}


def groups(areas: dict[int, int | None], clip_idxs: list[int]) -> list[list[int]]:
    """Runs of sampled photos, in walk order, that show the object; a photo without it, or a failed one, ends a run."""
    out: list[list[int]] = []
    run: list[int] = []
    for idx in [i for i in clip_idxs if i in areas]:
        if areas[idx]:
            run.append(idx)
            continue
        if run:
            out.append(run)
        run = []
    if run:
        out.append(run)
    return out


def split_by_distance(group: list[int], by_idx: dict[int, dict], meters: float) -> list[list[int]]:
    """Cut a run of photos into pieces that each cover at most `meters` of the walk (measured photo to photo), so
    things that stand in rows (poles, trees) get one row each instead of one row per run."""
    pieces: list[list[int]] = [[group[0]]]
    walked = 0.0
    for prev, idx in zip(group, group[1:]):
        a, b = by_idx[prev], by_idx[idx]
        walked += geo.haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
        if walked > meters:
            pieces.append([idx])
            walked = 0.0
        else:
            pieces[-1].append(idx)
    return pieces


def best(group: list[int], areas: dict[int, int | None]) -> int:
    """The photo where the object's mask is largest (the closest view); the earlier photo on a tie."""
    return max(group, key=lambda i: (areas[i], -group.index(i)))


def merge_near(picks: list[dict], by_idx: dict[int, dict], meters: float = MERGE_M) -> list[dict]:
    """picks: {"idx", "area", "group"}. Largest first, a pick within `meters` of one already kept is the same object.
    Returned in photo order."""
    kept: list[dict] = []
    for p in sorted(picks, key=lambda p: -p["area"]):
        f = by_idx[p["idx"]]
        if all(geo.haversine_m(f["lat"], f["lng"], by_idx[k["idx"]]["lat"], by_idx[k["idx"]]["lng"]) > meters
               for k in kept):
            kept.append(p)
    return sorted(kept, key=lambda p: p["idx"])


def status(check: Check, answer: dict) -> tuple[str, list[str], str] | None:
    """(review_status, reasons, current answer) for Spark's answer; None drops the pick. For an inventory check a
    "no", or the target not being visible, means SAM found something that is not the target (a plain utility pole,
    a private bin), so the pick is dropped instead of being sent to review."""
    if check.inventory:
        if not answer["visible"] or answer["answer"] == "no":
            return None
        if answer["answer"] == "unclear":
            return "needs_review", ["unclear"], "unclear"
        return "auto", [], "yes"
    current = answer["answer"] if answer["visible"] else "unclear"
    if current == "unclear":
        return "needs_review", ["unclear"], current
    if current == check.report_when:
        return "needs_review", ["possible_defect"], current
    return "auto", [], current


def run_check(api, check: Check, clips: dict[int, list[int]], photos: dict[int, Path], by_idx: dict[int, dict],
              workers: int, every: int = SAMPLE_EVERY, known: dict[int, int] | None = None) -> tuple[list[dict], dict]:
    """Rows for one check over every walk, and what could not be analysed: {"find": [photo idx], "ask": [photo idx]}.
    An inventory check also reports "not_target": the photos of picks Spark said are not the target (no row).
    `known` holds the areas already measured for this check's `find` (photo idx -> area). Those photos are not asked
    again, and every area measured now is added to it, clip by clip, so the caller can save it. A failed request is
    not added; it is asked again next time."""
    picks: list[dict] = []
    failed_find: list[int] = []
    for clip_idxs in clips.values():
        areas = find_areas(api, check, clip_idxs[::every], photos, workers, known)
        if known is not None:
            known.update({i: a for i, a in areas.items() if a is not None})
        failed_find += [i for i, a in areas.items() if a is None]
        for group in groups(areas, clip_idxs):
            for piece in (split_by_distance(group, by_idx, check.group_m) if check.group_m else [group]):
                idx = best(piece, areas)
                picks.append({"idx": idx, "area": areas[idx], "group": piece})
    picks = merge_near(picks, by_idx)

    def one(p):
        try:
            answer = api.spark_json([image_part(photos[p["idx"]]), text_part(prompt(check))], "survey_check", SCHEMA)
            if (not isinstance(answer, dict) or not isinstance(answer.get("visible"), bool)
                    or answer.get("answer") not in ("yes", "no", "unclear")):
                raise ValueError(f"unexpected answer: {str(answer)[:200]}")
            return answer
        except CostCapReached:
            raise
        except Exception as exc:  # this object only: reported as unavailable
            return {"error": error_text(exc)}

    rows: list[dict] = []
    failed_ask: list[int] = []
    not_target: list[int] = []
    for p, answer in zip(picks, run_parallel(one, picks, workers)):
        if "error" in answer:
            failed_ask.append(p["idx"])
            continue
        decided = status(check, answer)
        if decided is None:
            not_target.append(p["idx"])
            continue
        review_status, reasons, current = decided
        frame = by_idx[p["idx"]]
        rows.append({"issue_type": check.id, "object_label": current, "frame_start": min(p["group"]),
                     "frame_end": max(p["group"]), "frame_idx": p["idx"], "lat": frame["lat"], "lng": frame["lng"],
                     "confidence": answer.get("confidence"), "review_status": review_status, "review_reasons": reasons,
                     "model_outputs": {"check": answer, "find": {"phrase": check.find, "mask_area": p["area"],
                                                                 "group_idxs": p["group"]}},
                     "captured_at": frame["captured_at"]})
    return rows, {"find": failed_find, "ask": failed_ask, "not_target": not_target}
