"""Checks: survey items written as sentences (design docs/specs/2026-10-28-us-survey-items-design.md §4.3).

A check found by video (find_by = "video") gives SAM 3.1 the walk's photos joined as a video, and SAM follows each
object it finds from photo to photo under one number. Each followed object is one row, judged on its largest view
that is not cut off by the photo's edge. What SAM saw as two objects is made one where it can tell (parts of one
thing, an object lost for a photo or crossing into the next video), and two walks that saw one object from the same
place are merged. On a rerun of Seattle scan 4 it found as many city poles and curb ramps as photos did (12 and 9;
by photos 11 and 10), in one row per object with SAM's own box, and half the curb ramp rows (10, against 19). Each
item says which way it is found (pipeline/standards/us.toml).

A check found by photos looks for `find` on every second photo of each walk. Sampled photos in a row that show it
are one object, seen best where its mask is largest (a check with `group_m` cuts a long run into pieces of that many
metres, one object each); sightings within MERGE_M are the same object.

Muse Spark answers the check's `ask` on the chosen photo, told where SAM marked the object when it is known. One
sidewalk_issue row per object, in the format agreed with eval on 2026-10-28 (plan 4, Global Constraints).

A check with judge = "glimmer" is answered first by a judge model (Muse Glimmer, an OpenModel) when the caller passes
one: Spark takes the picks it cannot decide, and audits a share of the rest (design 2.3,
docs/specs/2026-10-28-glimmer-modal-design.md)."""
import math
import random
from dataclasses import dataclass
from pathlib import Path

from pipeline import config, geo, media
from pipeline.cost import CostCapReached
from pipeline.meta_api import error_text, image_part, text_part
from pipeline.parallel import run_parallel
from pipeline.sam_output import check_frames, confident, parse
from pipeline.survey_items import Check

SAMPLE_EVERY = 2
MERGE_M = 8.0  # objects from different walks closer than this are the same object
AUDIT_SHARE = 0.1  # of the judge's yes/no answers, Spark answers this share too (at least one)
DROP_AUDIT_SHARE = 0.1  # of a defect check's dropped picks, this share goes to a person anyway (at least one)
JUDGE_GIVE_UP = 3  # failures in a row after which the judge is not asked again in this check (a hung server)
FOV = 70.0  # degrees across a photo, as the map and eval/city.py draw where a box looks
EDGE = 10  # of 1000: a box this close to the photo's left, right or bottom edge is cut off there
JOIN_PHOTOS = 2  # an object first seen this few photos after another was last seen in the walk ...
JOIN_DEG = 10.0  # ... looking this close to the same way is the same object (SAM lost it, or a video ended)
CONTAIN = 0.8  # share of a box inside another's in every photo they share: parts of one object
WALKS_M = 8.0  # objects of two walks whose photos are this close ...
WALKS_DEG = 15.0  # ... and look this close to the same way are one object
# Rows of one check on one photo whose answer boxes overlap this much (intersection over union) show one object.
# Scan 17: SAM followed one signal pole as two objects and both got a row on the same photo (IoU 0.54); two poles
# side by side on one photo overlapped 0.
SAME_BOX_IOU = 0.5

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

PROMPT = """A pedestrian took this photo on a sidewalk.{marked}
{ask}
Answer as JSON:
- visible: whether the {target} is visible in the photo.
- answer: "yes", "no", or "unclear" if the photo does not show it well enough to tell.
- box: the {target} as [x1, y1, x2, y2] on a 0-1000 scale of the image, or [] if it is not visible.
- confidence: 0 to 1 for your answer.
- note: one sentence on what you see."""


MARKED = """ An object finder marked a possible {target} at {box} ([x1, y1, x2, y2] on a 0-1000 scale of the image).
Answer about the object in that box."""


def prompt(check: Check, box: list[int] | None = None) -> str:
    """The question for one photo; with box, about the object SAM marked there (several may be in the photo)."""
    marked = MARKED.format(target=check.target, box=list(box)) if box else ""
    return PROMPT.format(ask=check.ask, target=check.target, marked=marked)


@dataclass(frozen=True)
class Video:
    """One video the checks are found on: the photo idx of each frame, its frame size and its walk."""
    idxs: tuple[int, ...]
    size: tuple[int, int]
    clip: int


def direction(frame: dict, box: list[int]) -> float | None:
    """Compass bearing from the photo toward a 0-1000 box: compass + (box centre / 1000 - 0.5) * FOV."""
    if frame.get("compass") is None:
        return None
    return (frame["compass"] + ((box[0] + box[2]) / 2000 - 0.5) * FOV) % 360


def _angle(a: float | None, b: float | None) -> float:
    return 180.0 if a is None or b is None else abs((a - b + 180) % 360 - 180)


def tracks(text: str, video: Video, by_idx: dict[int, dict]) -> list[list[dict]]:
    """The objects SAM followed in one video's answer, each a list of sightings {"idx", "area", "box"} in photo
    order: box on a 0-1000 scale of the photo, area the box's area in photo pixels (masks are not decoded). Raises
    ValueError for an answer that does not fit the video."""
    segs = confident(parse(text, video=True, masks=False))
    check_frames(segs, len(video.idxs))
    out: dict[str, list[dict]] = {}
    for s in segs:
        idx = video.idxs[s.frame]
        f = by_idx[idx]
        wh = (f["width"], f["height"])
        left, top, right, bottom = media.video_box_to_photo(s.box, video.size, wh)
        if right <= left or bottom <= top:
            continue
        scale = media.letterbox(wh, video.size)[0]
        box = [round(left * 1000 / wh[0]), round(top * 1000 / wh[1]), round(right * 1000 / wh[0]),
               round(bottom * 1000 / wh[1])]
        out.setdefault(s.obj, []).append({"idx": idx, "area": round(s.area / scale ** 2), "box": box})
    return [sorted(t, key=lambda d: d["idx"]) for _, t in sorted(out.items(), key=lambda kv: kv[1][0]["idx"])]


def _inside(a: list[int], b: list[int]) -> float:
    """How much of the smaller of two boxes lies inside the other, 0-1."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / smaller if w > 0 and h > 0 and smaller > 0 else 0.0


def merge_parts(objs: list[list[dict]]) -> list[list[dict]]:
    """Objects of one video that are parts of one thing (a tree's trunk and crown): seen together in at least two
    photos, one box at least CONTAIN inside the other in every one of them. A thing behind another shifts against it
    from photo to photo, so it does not stay inside."""
    parent = list(range(len(objs)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i in range(len(objs)):
        for j in range(i + 1, len(objs)):
            a = {d["idx"]: d["box"] for d in objs[i]}
            b = {d["idx"]: d["box"] for d in objs[j]}
            shared = a.keys() & b.keys()
            if len(shared) >= 2 and all(_inside(a[k], b[k]) >= CONTAIN for k in shared):
                parent[root(j)] = root(i)
    groups: dict[int, list[dict]] = {}
    for i, t in enumerate(objs):
        groups.setdefault(root(i), []).extend(t)
    return [sorted(t, key=lambda d: d["idx"]) for t in groups.values()]


def join(found: list[tuple[Video, list[list[dict]]]], by_idx: dict[int, dict]) -> list[list[dict]]:
    """Every object of every video, with what SAM saw as two objects made one: parts of one thing (merge_parts), and,
    in one walk, an object first seen at most JOIN_PHOTOS photos after another was last seen, looking within JOIN_DEG
    of the same way: SAM lost the object and found it again, or it crossed into the next video. Objects join in the
    order they are first seen, each to the closest way."""
    walks: dict[int, list[tuple[Video, list[list[dict]]]]] = {}
    for video, objs in found:
        walks.setdefault(video.clip, []).append((video, merge_parts([list(t) for t in objs])))
    out: list[list[dict]] = []
    for videos in walks.values():
        pos = {idx: n for n, idx in enumerate(sorted(i for v, _ in videos for i in v.idxs))}
        joined: list[list[dict]] = []
        for b in sorted((t for _, objs in videos for t in objs), key=lambda t: (pos[t[0]["idx"]], t[0]["box"])):
            first = b[0]
            way = direction(by_idx[first["idx"]], first["box"])
            best = None
            for a in joined:
                gap = pos[first["idx"]] - pos[a[-1]["idx"]]
                angle = _angle(direction(by_idx[a[-1]["idx"]], a[-1]["box"]), way)
                if 1 <= gap <= JOIN_PHOTOS and angle <= JOIN_DEG and (best is None or angle < best[0]):
                    best = (angle, a)
            if best is None:
                joined.append(b)
            else:
                best[1].extend(b)
        out += joined
    return out


def track_pick(track: list[dict]) -> dict:
    """The pick for one followed object: its largest sighting whose box is not cut off by the photo's left, right or
    bottom edge (the whole object is in view), else its largest sighting; the earlier photo on a tie."""
    whole = [d for d in track if d["box"][0] > EDGE and d["box"][2] < 1000 - EDGE and d["box"][3] < 1000 - EDGE]
    best = max(whole or track, key=lambda d: (d["area"], -d["idx"]))
    return {"idx": best["idx"], "area": best["area"], "group": sorted({d["idx"] for d in track}), "box": best["box"]}


def merge_walks(picks: list[dict], by_idx: dict[int, dict]) -> list[dict]:
    """Largest first, a pick from another walk whose photo is within WALKS_M of a kept one's and looks within WALKS_DEG
    of the same way is the same object. Within one walk SAM's numbering already tells objects apart, so a kept pick
    takes in at most one pick of each other walk. Photo order."""
    kept: list[tuple[dict, set]] = []  # (pick, the walks it stands for)
    for p in sorted(picks, key=lambda p: (-p["area"], p["idx"])):
        f = by_idx[p["idx"]]
        walk = f.get("clip")
        same = next((walks for k, walks in kept if walk not in walks
                     and geo.haversine_m(f["lat"], f["lng"], by_idx[k["idx"]]["lat"], by_idx[k["idx"]]["lng"]) <= WALKS_M
                     and _angle(direction(f, p["box"]), direction(by_idx[k["idx"]], k["box"])) <= WALKS_DEG), None)
        if same is None:
            kept.append((p, {walk}))
        else:
            same.add(walk)
    return sorted((k for k, _ in kept), key=lambda p: p["idx"])


def video_picks(texts: dict[int, str | None], videos: dict[int, Video],
                by_idx: dict[int, dict]) -> tuple[list[dict], list[int]]:
    """One pick per object SAM followed, from each video's answer (texts and videos keyed alike, by the video's
    first photo idx; None: the request failed). Returns the picks and the keys of videos whose answer is missing or
    unreadable."""
    found, failed = [], []
    for key, video in videos.items():
        text = texts.get(key)
        try:
            if text is None:
                raise ValueError("no answer")
            found.append((video, tracks(text, video, by_idx)))
        except ValueError:
            failed.append(key)
    return merge_walks([track_pick(t) for t in join(found, by_idx)], by_idx), sorted(failed)


def find_areas(api, check: Check, idxs: list[int], photos: dict[int, Path], workers: int,
               known: dict[int, int] | None = None) -> dict[int, int | None]:
    """Largest confident box area of the check's `find` per photo (boxes only: sam_output.boxes): 0 when not found,
    None when the request failed. Photos in `known` (areas measured earlier for this same `find`) are not asked
    again."""
    def one(idx):
        try:
            segs = confident(parse(api.sam_image(check.find, photos[idx]), video=False, masks=False))
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
    """(review_status, reasons, current answer) for Spark's answer; None drops the pick. The target not being
    visible means SAM found something that is not the target (a driveway, a plain curb, a private bin): the ask
    sentence defines it so, and on the recording survey a person agreed on all 12 such curb ramps (2026-10-28). So the
    pick is dropped instead of being sent to review; run_check keeps a record of it and sends a sample to a person.
    For an inventory check a "no" is dropped too."""
    if not answer["visible"]:
        return None
    if check.inventory:
        if answer["answer"] == "no":
            return None
        if answer["answer"] == "unclear":
            return "needs_review", ["unclear"], "unclear"
        return "auto", [], "yes"
    current = answer["answer"]
    if current == "unclear":
        return "needs_review", ["unclear"], current
    if current == check.report_when:
        return "needs_review", ["possible_defect"], current
    return "auto", [], current


def _valid(answer) -> bool:
    return (isinstance(answer, dict) and isinstance(answer.get("visible"), bool)
            and answer.get("answer") in ("yes", "no", "unclear"))


def _current(answer: dict) -> str:
    return answer["answer"] if answer["visible"] else "unclear"


def _opinion(answer: dict, agree: bool) -> dict:
    return {"model": answer.get("model"), "answer": _current(answer), "agree": agree,
            "confidence": answer.get("confidence"), "note": answer.get("note"), "box": answer.get("box")}


def _outcome(decided) -> str:
    return "drop" if decided is None else decided[2]


def _iou(a: list[int], b: list[int]) -> float:
    across = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - across
    return across / union if union > 0 else 0.0


def one_row_per_object(rows: list[dict]) -> list[dict]:
    """Of rows on the same photo whose answer boxes overlap SAME_BOX_IOU or more, the most confident; the rest keep
    their order."""
    def box(r):
        return ((r.get("model_outputs") or {}).get("check") or {}).get("box")

    kept: list[dict] = []
    for r in sorted(rows, key=lambda r: -(r.get("confidence") or 0.0)):
        if box(r) and any(k["frame_idx"] == r["frame_idx"] and box(k) and _iou(box(r), box(k)) >= SAME_BOX_IOU
                          for k in kept):
            continue
        kept.append(r)
    return [r for r in rows if any(r is k for k in kept)]


def run_check(api, check: Check, clips: dict[int, list[int]], photos: dict[int, Path], by_idx: dict[int, dict],
              workers: int, every: int = SAMPLE_EVERY, known: dict[int, int] | None = None, judge=None,
              audit_seed: int | None = None, audit_share: float = AUDIT_SHARE,
              dropped: list[dict] | None = None,
              video: tuple[dict[int, str | None], dict[int, Video]] | None = None) -> tuple[list[dict], dict]:
    """_check_rows, one row per object (one_row_per_object)."""
    rows, missing = _check_rows(api, check, clips, photos, by_idx, workers, every, known, judge, audit_seed,
                                audit_share, dropped, video)
    return one_row_per_object(rows), missing


def _check_rows(api, check: Check, clips: dict[int, list[int]], photos: dict[int, Path], by_idx: dict[int, dict],
                workers: int, every: int = SAMPLE_EVERY, known: dict[int, int] | None = None, judge=None,
                audit_seed: int | None = None, audit_share: float = AUDIT_SHARE,
                dropped: list[dict] | None = None,
                video: tuple[dict[int, str | None], dict[int, Video]] | None = None) -> tuple[list[dict], dict]:
    """Rows for one check over every walk, and what could not be analysed: {"find": [photo idx], "ask": [photo idx]}.
    A check found by video takes SAM's answers on the videos from the caller, video = (texts, videos) as in
    video_picks, and "find" then lists the first photo of each video without an answer.
    "not_target" lists the photos of picks the model said are not the target (no row), and `dropped`, when given,
    receives a record of each (db.add_dropped). Of a defect check's dropped picks, DROP_AUDIT_SHARE (at least one),
    chosen with audit_seed, still become rows for a person ("dropped_audit"): a person who finds the target there
    keeps the row, against the model.
    `known` holds the areas already measured for this check's `find` (photo idx -> area). Those photos are not asked
    again, and every area measured now is added to it, clip by clip, so the caller can save it. A failed request is
    not added; it is asked again next time.

    With a judge (an OpenModel: answer(check, photo), wake(), model), the judge answers every pick first and is woken
    before SAM starts. Spark answers the picks the judge calls unclear or fails on, and audits audit_share of the
    others, chosen with audit_seed (the scan id). An audit that disagrees keeps the judge's row and sends it to a
    person ("models_disagree"); if the judge dropped what Spark keeps, a row is made from Spark's answer for a person
    to settle. Every answer names its model, and "judge" in the result counts what happened."""
    if judge is not None:
        judge.wake()  # the container starts while SAM looks for this check's objects
    picks: list[dict] = []
    failed_find: list[int] = []
    if check.find_by == "video":
        if video is None:
            raise ValueError(f"check {check.id} is found by video, but no video answers were given")
        picks, failed_find = video_picks(*video, by_idx)
    else:
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

    def found(p):
        """model_outputs.find: SAM's phrase, the object's mask area, its photos and, by video, its box."""
        if p.get("box"):  # followed by video: the area is the box's
            return {"phrase": check.find, "box_area": p["area"], "group_idxs": p["group"], "box": p["box"]}
        return {"phrase": check.find, "mask_area": p["area"], "group_idxs": p["group"]}

    def spark(p):
        try:
            answer = api.spark_json([image_part(photos[p["idx"]]), text_part(prompt(check, p.get("box")))],
                                    "survey_check", SCHEMA)
            if not _valid(answer):
                raise ValueError(f"unexpected answer: {str(answer)[:200]}")
            return {**answer, "model": config.SPARK_MODEL} if judge is not None else answer
        except CostCapReached:
            raise
        except Exception as exc:  # this object only: reported as unavailable
            return {"error": error_text(exc)}

    def drop(p, answer, audited=False):
        not_target.append(p["idx"])
        frame = by_idx[p["idx"]]
        if dropped is not None:
            dropped.append({"issue_type": check.id, "frame_idx": p["idx"], "frame_start": min(p["group"]),
                            "frame_end": max(p["group"]), "lat": frame["lat"], "lng": frame["lng"],
                            "captured_at": frame["captured_at"], "audited": audited,
                            "model_outputs": {"check": answer, "find": found(p)}})

    def row(p, answer, decided, extra=None):
        review_status, reasons, current = decided
        frame = by_idx[p["idx"]]
        return {"issue_type": check.id, "object_label": current, "frame_start": min(p["group"]),
                "frame_end": max(p["group"]), "frame_idx": p["idx"], "lat": frame["lat"], "lng": frame["lng"],
                "confidence": answer.get("confidence"), "review_status": review_status, "review_reasons": reasons,
                "model_outputs": {"check": answer, "find": found(p), **(extra or {})},
                "captured_at": frame["captured_at"]}

    rows: list[dict] = []
    failed_ask: list[int] = []
    not_target: list[int] = []
    if judge is None:
        gone: list[tuple[dict, dict]] = []
        for p, answer in zip(picks, run_parallel(spark, picks, workers)):
            if "error" in answer:
                failed_ask.append(p["idx"])
                continue
            decided = status(check, answer)
            if decided is None:
                gone.append((p, answer))
                continue
            rows.append(row(p, answer, decided))
        sampled = set() if check.inventory or not gone else set(
            random.Random(audit_seed).sample(range(len(gone)), math.ceil(DROP_AUDIT_SHARE * len(gone))))
        for i, (p, answer) in enumerate(gone):
            drop(p, answer, audited=i in sampled)
            if i in sampled:
                rows.append(row(p, answer, ("needs_review", ["dropped_audit"], "unclear")))
        return rows, {"find": failed_find, "ask": failed_ask, "not_target": not_target}

    failures_in_a_row = [0]

    def ask_judge(p):
        if failures_in_a_row[0] >= JUDGE_GIVE_UP:
            return {"error": f"gave up on {judge.model} after {JUDGE_GIVE_UP} failures in a row"}
        try:
            answer = judge.answer(check, photos[p["idx"]], box=p.get("box"))
            if not _valid(answer):
                raise ValueError(f"unexpected answer from {judge.model}: {str(answer)[:200]}")
        except Exception as exc:  # Spark answers this pick instead
            failures_in_a_row[0] += 1
            return {"error": error_text(exc)}
        failures_in_a_row[0] = 0
        return answer

    first = run_parallel(ask_judge, picks, workers)
    decisive = {i for i, a in enumerate(first) if "error" not in a and not (a["visible"] and a["answer"] == "unclear")}
    fallback = [i for i in range(len(picks)) if i not in decisive]
    audited = set(random.Random(audit_seed).sample(sorted(decisive), math.ceil(audit_share * len(decisive))))
    asked = sorted({*fallback, *audited})
    second = dict(zip(asked, run_parallel(lambda i: spark(picks[i]), asked, workers)))
    audit = {"asked": 0, "agree": 0}
    for i, p in enumerate(picks):
        if i not in decisive:
            answer = second[i]
            if "error" in answer:
                failed_ask.append(p["idx"])
                continue
            decided = status(check, answer)
            if decided is None:
                drop(p, answer)
                continue
            said = first[i]
            note = ({"model": judge.model, "error": said["error"]} if "error" in said
                    else {"model": judge.model, "answer": _current(said)})
            rows.append(row(p, answer, decided, {"judge": note}))
            continue
        said, decided, extra = first[i], status(check, first[i]), {}
        if i in audited:
            other = second[i]
            if "error" in other:
                extra["audit"] = {"model": config.SPARK_MODEL, "error": other["error"]}
            else:
                spark_decided = status(check, other)
                agree = _outcome(decided) == _outcome(spark_decided)
                audit["asked"] += 1
                audit["agree"] += agree
                if decided is None and _outcome(spark_decided) == "yes":
                    rows.append(row(p, other, ("needs_review", ["models_disagree"], "yes"),
                                    {"audit": _opinion(said, False)}))
                    continue
                extra["audit"] = _opinion(other, agree)
                if decided is not None and not agree:
                    decided = ("needs_review", [*decided[1], "models_disagree"], decided[2])
        if decided is None:
            drop(p, said)
            continue
        rows.append(row(p, said, decided, extra))
    summary = {"model": judge.model, "answered": len(decisive), "fallback": len(fallback), "audit": audit}
    return rows, {"find": failed_find, "ask": failed_ask, "not_target": not_target, "judge": summary}
