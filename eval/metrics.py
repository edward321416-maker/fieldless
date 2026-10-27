"""Score one scan against a survey truth file (spec section 17, plan 3).

Defects and obstructions are matched as spots (runs of consecutive photos, tolerance SPOT_TOL); entrances are matched
one-to-one within ENTRANCE_TOL photos. Truth photos are placed by image id, never by position."""
import statistics

from eval.spans import match_points, match_spans, runs
from pipeline import geo

DEFECTS = ("missing_blocks", "cover_cut", "fixed_obstruction")
SPOT_TOL = 1
ENTRANCE_TOL = 2
SWEEP = (0.2, 0.3, 0.4, 0.5, 0.6)


def track_km(frames: list[dict]) -> float:
    """Length of the walked track: consecutive photos within each clip, by idx."""
    by_clip: dict[int, list[dict]] = {}
    for f in frames:
        by_clip.setdefault(f["clip"], []).append(f)
    total = 0.0
    for clip in by_clip.values():
        clip.sort(key=lambda f: f["idx"])
        for a, b in zip(clip, clip[1:]):
            if None not in (a["lat"], a["lng"], b["lat"], b["lng"]):
                total += geo.haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
    return total / 1000


def _truth_by_idx(truth: dict, frames: list[dict]) -> dict[int, dict]:
    by_image = {f["image_id"]: f["idx"] for f in frames}
    unknown = sorted(set(truth["photos"]) - set(by_image))
    if unknown:
        raise ValueError(f"truth has photos that are not in the scan: {unknown[:5]}")
    return {by_image[image_id]: p for image_id, p in truth["photos"].items()}


def _span(issue: dict) -> tuple[int, int]:
    start, end = issue.get("frame_start"), issue.get("frame_end")
    if start is None or end is None:
        return (issue["frame_idx"], issue["frame_idx"])
    return (start, end)


def _truth_spans(truth_idx: dict[int, dict], has) -> list[tuple[int, int]]:
    return runs(idx for idx, p in truth_idx.items() if has(p))


def _defects(issues, truth_idx):
    return {kind: match_spans([_span(i) for i in issues if i["issue_type"] == kind],
                              _truth_spans(truth_idx, lambda p, k=kind: any(d["type"] == k for d in p["defects"])),
                              SPOT_TOL)
            for kind in DEFECTS}


def _obstructions(issues, truth_idx):
    return match_spans([_span(i) for i in issues if i["issue_type"] == "obstruction"],
                       _truth_spans(truth_idx, lambda p: bool(p["obstructions"])), SPOT_TOL)


def _entrances(entrances, truth_idx):
    truth_list = [(idx, e) for idx, p in sorted(truth_idx.items()) for e in p["entrances"]]
    pairs = match_points([e["frame_idx"] for e in entrances], [idx for idx, _ in truth_list], ENTRANCE_TOL)
    exact = within_one = sill_agree = 0
    for pi, ti in pairs:
        pred, t = entrances[pi], truth_list[ti][1]
        if pred.get("step_count") is not None:
            exact += pred["step_count"] == t["steps"]
            within_one += abs(pred["step_count"] - t["steps"]) <= 1
        sill_agree += bool(pred.get("sill_possible")) == bool(t["sill_unsure"])
    return {"predicted": len(entrances), "truth": len(truth_list), "matched": len(pairs),
            "steps_exact": exact, "steps_within_one": within_one, "sill_agree": sill_agree}


def _review(issues, entrances):
    items = list(issues) + list(entrances)
    flagged = sum(1 for i in items if i.get("review_reasons"))
    seconds = [c["seconds"] for i in items for c in (i.get("corrections") or [])
               if isinstance(c.get("seconds"), (int, float))]
    durable = [i for i in issues if i["issue_type"] in DEFECTS]
    verified = sum(i["review_status"] == "verified" for i in durable)
    rejected = sum(i["review_status"] == "rejected" for i in durable)
    return {"items": len(items), "flagged": flagged, "flagged_share": round(flagged / len(items), 3) if items else 0.0,
            "reviewed_actions": len(seconds),
            "review_seconds_median": statistics.median(seconds) if seconds else None,
            "defects_verified": verified, "defects_rejected": rejected,
            "defects_confirmed_share": round(verified / (verified + rejected), 3) if verified + rejected else None}


def _speed(scan, frames, truth):
    km = track_km(frames)
    human, proc, review = truth["labeling_seconds"], scan["processing_seconds"], scan["review_seconds"]

    def per_km(value):
        return round(value / km, 1) if km else None

    return {"photos": len(frames), "km": round(km, 3), "human_seconds": human, "processing_seconds": proc,
            "review_seconds": review, "reviewed": review > 0, "cost_usd": scan["cost_usd"],
            "usd_per_km": round(scan["cost_usd"] / km, 3) if km else None,
            "human_seconds_per_km": per_km(human), "threshold_seconds_per_km": per_km(proc + review)}


def _sweep(issues, truth_idx):
    out = {}
    for kind, has in (("fixed_obstruction", lambda p: any(d["type"] == "fixed_obstruction" for d in p["defects"])),
                      ("obstruction", lambda p: bool(p["obstructions"]))):
        truth_spans = _truth_spans(truth_idx, has)
        rows = []
        for th in SWEEP:
            kept = [_span(i) for i in issues if i["issue_type"] == kind and (i.get("overlap_ratio") or 0) >= th]
            m = match_spans(kept, truth_spans, SPOT_TOL)
            rows.append({"min_overlap": th, "predicted": m["predicted"], "hits": m["hits"], "found": m["found"],
                         "truth": m["truth"]})
        out[kind] = rows
    return out


def evaluate(data: dict, truth: dict) -> dict:
    if truth["scan_id"] != data["scan"]["id"]:
        raise ValueError("truth is for another scan")
    truth_idx = _truth_by_idx(truth, data["frames"])
    issues, entrances = data["issues"], data["entrances"]
    return {"scan_id": data["scan"]["id"], "defects": _defects(issues, truth_idx),
            "obstructions": _obstructions(issues, truth_idx), "entrances": _entrances(entrances, truth_idx),
            "review": _review(issues, entrances), "speed": _speed(data["scan"], data["frames"], truth),
            "sweep": _sweep(issues, truth_idx)}
