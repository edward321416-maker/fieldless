"""A second look at the city's records a survey did not find (docs/specs/2026-10-31-city-reconciliation-design.md).

    python -m eval.second_look --run demo --scan 17 --city seattle --dry-run
    python -m eval.second_look --run demo --scan 17 --city seattle

The city's list says where an asset should be, so the question can be about one spot of one photo: the photo that
shows the record best is sent with where to look. Muse Spark answers first, and Muse Spark Open is asked again where
Spark says yes: a record counts as seen only when both do. A question that names a place invites a yes, so control
spots go in with the records: places in view where the city lists nothing and the survey found nothing, asked in the
same words. Their yes rate says how far the records' yes can be trusted.

A finding the city does not list that sits at a record seen this way is that record, found twice: it is merged.

Writes <run>/second_look_scan<N>.json. It spends at most COST_CAP_USD of Meta credit.
"""
import argparse
import dataclasses
import json
import random
from pathlib import Path

from eval import city
from eval.city_check import load_city
from eval.store import load_scan, open_ro
from pipeline import collect, config, geo, second_opinion, survey_items
from pipeline.cost import CostCapReached, CostMeter
from pipeline.meta_api import MetaApi, error_text, image_part, text_part
from pipeline.parallel import run_parallel
from pipeline.survey_checks import SCHEMA, prompt

COST_CAP_USD = 2.0  # one run stays under the project's limit for a run without asking
CONTROLS_PER_ITEM = 4
SEED = 17
CLEAR_M = 12.0  # a control spot has no city record of the item this close
CONTROL_DEG = (10.0, 30.0)
CONTROL_M = (8.0, 16.0)
OFF_CENTRE_M_PER_DEG = 0.3  # a record near the middle of a farther photo beats one at the edge of a nearer photo
MERGE_M = 20.0
MERGE_DEG = 30.0
WORKERS = 8
ASK = ("City records place a {target} about {dist:.0f} m from the camera, {pct:.0f}% of the way across the photo from "
       "the left. Look at that part of the photo. Is there a {target} there? Answer yes only if you can see one at "
       "about that spot. Answer no if that spot is in clear view and shows none. Answer unclear if it is blocked, too "
       "far, or out of the photo.")


NEAR_M, FAR_M = 8.0, 20.0  # a record closer than this can sit outside the photo with a few metres of position error
MAX_OFF_DEG = 30.0
VIEWS = 2
DIRECTION_DEG = 20.0  # the box the model draws must look this close to the record
ASK_AREA = ("City records list a {target} about {dist:.0f} m ahead, toward the {side} of this photo. Records and photo "
            "positions can be off by several metres. Look at that general area. Is there a {target} there? Answer yes "
            "only if you can see one there, and box it. Answer no if that area is in clear view and shows none. Answer "
            "unclear if it is blocked, too far, or out of the photo.")


def off_centre(frame: dict, point: dict) -> float:
    """Degrees from the middle of the photo to the point, negative to the left."""
    bearing = city.bearing_deg(frame["lat"], frame["lng"], point["lat"], point["lng"])
    return (bearing - frame["compass"] + 180) % 360 - 180


def view(frame: dict, point: dict) -> dict:
    """Where the point should be in this photo."""
    offset = off_centre(frame, point)
    return {"frame_idx": frame["idx"], "image_id": frame["image_id"], "captured_at": frame["captured_at"],
            "dist_m": round(geo.haversine_m(frame["lat"], frame["lng"], point["lat"], point["lng"]), 1),
            "pct": round(50 + offset / city.FOV * 100, 1)}


def best_view(point: dict, frames: list[dict]) -> dict | None:
    """The photo that shows the point best, or None when no photo covers it."""
    def score(frame):
        distance = geo.haversine_m(frame["lat"], frame["lng"], point["lat"], point["lng"])
        return distance + OFF_CENTRE_M_PER_DEG * abs(off_centre(frame, point))

    covering = city.covering_frames(point, frames)
    return view(min(covering, key=score), point) if covering else None


def far_views(point: dict, frames: list[dict]) -> list[dict]:
    """Up to VIEWS photos that look at the point from NEAR_M to FAR_M away, a few photos apart."""
    def distance(frame):
        return geo.haversine_m(frame["lat"], frame["lng"], point["lat"], point["lng"])

    fits = [f for f in frames if f.get("compass") is not None and NEAR_M <= distance(f) <= FAR_M
            and abs(off_centre(f, point)) <= MAX_OFF_DEG]
    chosen: list[dict] = []
    for frame in sorted(fits, key=lambda f: abs(distance(f) - 12.0) + OFF_CENTRE_M_PER_DEG * abs(off_centre(f, point))):
        if all(abs(frame["idx"] - c["idx"]) >= 3 for c in chosen):
            chosen.append(frame)
        if len(chosen) == VIEWS:
            break
    return [view(f, point) for f in chosen]


def area_check(check, spot_view: dict):
    side = "left" if spot_view["pct"] < 40 else "right" if spot_view["pct"] > 60 else "middle"
    return dataclasses.replace(check, ask=ASK_AREA.format(target=check.target, dist=spot_view["dist_m"], side=side))


def looks_at(answer: dict, frame: dict, point: dict) -> bool:
    """Whether the box in the answer lies toward the point."""
    box = answer.get("box") or []
    if len(box) != 4:
        return False
    direction = (frame["compass"] + ((box[0] + box[2]) / 2000 - 0.5) * city.FOV) % 360
    bearing = city.bearing_deg(frame["lat"], frame["lng"], point["lat"], point["lng"])
    return city.angle_between(direction, bearing) <= DIRECTION_DEG


def found_frames(rows: list[dict]) -> set[int]:
    """The photos of a survey's findings and their neighbours: a control spot stays out of them."""
    out: set[int] = set()
    for row in rows:
        group = ((row.get("model_outputs") or {}).get("find") or {}).get("group_idxs") or []
        for idx in [row["frame_idx"], *group]:
            out.update((idx - 1, idx, idx + 1))
    return out


def control_spots(frames: list[dict], records: list[dict], rows: list[dict], count: int, rng: random.Random) -> list[dict]:
    """Spots in view where the city lists nothing of this item and the survey found nothing of it."""
    taken = found_frames(rows)
    spots: list[dict] = []
    for frame in rng.sample(frames, len(frames)):
        if len(spots) == count:
            break
        if frame["idx"] in taken or frame.get("compass") is None:
            continue
        bearing = (frame["compass"] + rng.choice((-1, 1)) * rng.uniform(*CONTROL_DEG)) % 360
        lat, lng = geo.offset_point(frame["lat"], frame["lng"], bearing, rng.uniform(*CONTROL_M))
        if any(geo.haversine_m(lat, lng, r["lat"], r["lng"]) < CLEAR_M for r in records):
            continue
        taken.update((frame["idx"] - 1, frame["idx"], frame["idx"] + 1))
        spots.append({"lat": lat, "lng": lng, "view": view(frame, {"lat": lat, "lng": lng})})
    return spots


def merge(seen: list[dict], unlisted: list[dict], by_idx: dict[int, dict]) -> list[tuple[int, int]]:
    """(seen record number, finding id): a finding the city does not list, at a record seen on the second look. One
    to one, nearest first. A finding with a direction must look toward the record."""
    pairs = []
    for n, record in enumerate(seen):
        for row in unlisted:
            frame = by_idx[row["frame_idx"]]
            distance = geo.haversine_m(frame["lat"], frame["lng"], record["lat"], record["lng"])
            direction = city.row_direction(row, frame)
            bearing = city.bearing_deg(frame["lat"], frame["lng"], record["lat"], record["lng"])
            if distance <= MERGE_M and (direction is None or city.angle_between(direction, bearing) <= MERGE_DEG):
                pairs.append((distance, n, row["id"]))
    out, records, rows = [], set(), set()
    for _, n, row_id in sorted(pairs):
        if n not in records and row_id not in rows:
            records.add(n)
            rows.add(row_id)
            out.append((n, row_id))
    return out


def question(check, spot_view: dict):
    return dataclasses.replace(check, ask=ASK.format(target=check.target, dist=spot_view["dist_m"], pct=spot_view["pct"]))


def jobs_for(data: dict, check_file: dict, town: dict, rng: random.Random) -> tuple[list[dict], dict[str, list[dict]]]:
    """The spots to ask about, records and controls together, and each item's findings the city does not list."""
    root = config.data_dir() / town["root"]
    jobs: list[dict] = []
    unlisted: dict[str, list[dict]] = {}
    for item, info in check_file["items"].items():
        rows = [r for r in data["issues"] if r["issue_type"] == item]
        matched = set(info["matched_ids"])
        unlisted[item] = [r for r in rows if r["id"] not in matched]
        for record in info["missed"]:
            jobs.append({"item": item, "kind": "record", "lat": record["lat"], "lng": record["lng"],
                         "city": record["city"], "view": best_view(record, data["frames"])})
        layer = json.loads((root / town["items"][item]["layer"]).read_text(encoding="utf-8"))
        for spot in control_spots(data["frames"], city.load_points(layer), rows, CONTROLS_PER_ITEM, rng):
            jobs.append({"item": item, "kind": "control", **spot})
    return jobs, unlisted


def ask_spark(api, standard, job: dict) -> dict:
    if job["view"] is None:
        return {"answer": "no_photo"}
    check = question(standard.check(job["item"]), job["view"])
    photo = collect.photo_path(job["view"]["image_id"])
    try:
        return api.spark_json([image_part(photo), text_part(prompt(check))], "second_look", SCHEMA)
    except CostCapReached as exc:
        return {"answer": "not_asked", "error": str(exc)}
    except Exception as exc:
        return {"answer": "error", "error": error_text(exc)}


def ask_open(model, standard, job: dict) -> dict:
    check = question(standard.check(job["item"]), job["view"])
    try:
        return model.answer(check, collect.photo_path(job["view"]["image_id"]))
    except Exception as exc:
        return {"answer": "error", "error": error_text(exc)}


def is_seen(job: dict) -> bool:
    spark, other = job.get("spark") or {}, job.get("open") or {}
    return (spark.get("visible") is True and spark.get("answer") == "yes"
            and other.get("visible") is True and other.get("answer") == "yes")


def summary(jobs: list[dict], unlisted: dict[str, list[dict]], by_idx: dict[int, dict]) -> dict:
    items: dict[str, dict] = {}
    for item, rows in unlisted.items():
        records = [j for j in jobs if j["item"] == item and j["kind"] == "record"]
        seen = [j for j in records if is_seen(j)]
        merged = merge(seen, rows, by_idx)
        answers: dict[str, int] = {}
        for job in records:
            key = (job.get("spark") or {}).get("answer", "none")
            answers[key] = answers.get(key, 0) + 1
        items[item] = {"records": len(records), "seen_by_both": len(seen), "spark_answers": answers,
                       "unlisted": len(rows), "merged": len(merged), "merged_ids": [r for _, r in merged],
                       "still_to_visit": len(records) - len(seen) + len(rows) - len(merged)}
    controls = [j for j in jobs if j["kind"] == "control"]
    spark_yes = [j for j in controls if (j.get("spark") or {}).get("answer") == "yes"]
    both_yes = [j for j in controls if is_seen(j)]
    return {"items": items,
            "records": sum(i["records"] for i in items.values()),
            "seen_by_both": sum(i["seen_by_both"] for i in items.values()),
            "merged": sum(i["merged"] for i in items.values()),
            "to_visit_before": sum(i["records"] + i["unlisted"] for i in items.values()),
            "to_visit_after": sum(i["still_to_visit"] for i in items.values()),
            "controls": len(controls), "controls_spark_yes": len(spark_yes), "controls_both_yes": len(both_yes),
            "control_false_yes_rate": round(len(both_yes) / len(controls), 3) if controls else None}


def pilot(run_dir: Path, scan: int, count: int) -> dict:
    """The area question on a sample of the records the first pass did not see, with control spots."""
    first = json.loads((run_dir / f"second_look_scan{scan}.json").read_text(encoding="utf-8"))
    conn = open_ro(run_dir / "threshold.db")
    data = load_scan(conn, scan)
    conn.close()
    standard = survey_items.load(data["scan"]["jurisdiction"])
    by_idx = {f["idx"]: f for f in data["frames"]}
    rng = random.Random(SEED + 1)
    unseen = [j for j in first["spots"] if j["kind"] == "record" and not is_seen(j)]
    groups: dict[str, list[dict]] = {}
    for job in rng.sample(unseen, len(unseen)):
        groups.setdefault(job["item"], []).append(job)
    sample: list[dict] = []
    while len(sample) < count and any(groups.values()):
        for rows in groups.values():
            if rows and len(sample) < count:
                sample.append(rows.pop())
    controls = rng.sample([j for j in first["spots"] if j["kind"] == "control"], count // 2)
    jobs = []
    for job in sample:
        jobs.append({"item": job["item"], "kind": "record", "lat": job["lat"], "lng": job["lng"], "city": job["city"],
                     "views": far_views(job, data["frames"]), "first": job["spark"].get("answer")})
    for job in controls:
        jobs.append({"item": job["item"], "kind": "control", "lat": job["lat"], "lng": job["lng"],
                     "views": [job["view"]], "first": job["spark"].get("answer")})
    print(f"{len(sample)} records, {sum(not j['views'] for j in jobs)} with no photo at this range, {len(controls)} controls")
    meter = CostMeter(0.6)
    api = MetaApi(run_dir / "calls_second_look.jsonl", meter)
    open_model = second_opinion.from_env(run_dir)

    def one(job):
        job["answers"] = []
        job["seen"] = False
        for spot_view in job["views"]:
            check = area_check(standard.check(job["item"]), spot_view)
            photo = collect.photo_path(spot_view["image_id"])
            try:
                answer = api.spark_json([image_part(photo), text_part(prompt(check))], "second_look", SCHEMA)
            except Exception as exc:
                job["answers"].append({"view": spot_view, "spark": {"answer": "error", "error": error_text(exc)}})
                continue
            entry = {"view": spot_view, "spark": answer}
            job["answers"].append(entry)
            if answer.get("answer") == "yes" and answer.get("visible") is True:
                entry["aimed"] = looks_at(answer, by_idx[spot_view["frame_idx"]], job)
                if entry["aimed"]:
                    try:
                        entry["open"] = open_model.answer(check, photo)
                    except Exception as exc:
                        entry["open"] = {"answer": "error", "error": error_text(exc)}
                    if entry["open"].get("answer") == "yes":
                        job["seen"] = True
                        return job
        return job

    run_parallel(one, jobs, WORKERS)
    records = [j for j in jobs if j["kind"] == "record"]
    kept = [j for j in jobs if j["kind"] == "control"]
    result = {"records": len(records), "with_a_photo": sum(bool(j["views"]) for j in records),
              "seen": sum(j["seen"] for j in records), "controls": len(kept),
              "controls_seen": sum(j["seen"] for j in kept), "cost_usd": round(meter.spent_usd, 4)}
    out = run_dir / f"second_look_pilot_scan{scan}.json"
    out.write_text(json.dumps({"summary": result, "jobs": jobs}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result))
    for job in jobs:
        answers = [(a["spark"].get("answer"), a.get("aimed"), (a.get("open") or {}).get("answer"), a["view"]["dist_m"])
                   for a in job["answers"]]
        print(job["kind"][:3], job["item"][:12], "first", job["first"], "seen", job["seen"], answers)
    return result


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--scan", type=int, required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--dry-run", action="store_true", help="choose the spots and stop: no model is called")
    parser.add_argument("--pilot", type=int, help="ask the area question on this many records the first pass missed")
    parser.add_argument("--cap-usd", type=float, default=COST_CAP_USD,
                        help=f"a lower cost cap for this run; it cannot go above ${COST_CAP_USD:.0f}")
    args = parser.parse_args(argv)
    run_dir = config.run_dir(args.run)
    if args.pilot:
        return pilot(run_dir, args.scan, args.pilot)
    conn = open_ro(run_dir / "threshold.db")
    data = load_scan(conn, args.scan)
    conn.close()
    check_file = json.loads((run_dir / f"city_check_scan{args.scan}.json").read_text(encoding="utf-8"))
    standard = survey_items.load(data["scan"]["jurisdiction"])
    by_idx = {f["idx"]: f for f in data["frames"]}
    jobs, unlisted = jobs_for(data, check_file, load_city(args.city), random.Random(SEED))
    asked = [j for j in jobs if j["view"]]
    print(f"{len(jobs)} spots: {sum(j['kind'] == 'record' for j in jobs)} records, "
          f"{sum(j['kind'] == 'control' for j in jobs)} controls, {len(jobs) - len(asked)} without a photo")
    missing = sum(not collect.photo_path(j["view"]["image_id"]).is_file() for j in asked)
    print(f"photos missing on disk: {missing}; at most ${len(asked) * config.SPARK_IMAGE_ESTIMATE_USD:.2f} reserved")
    if args.dry_run:
        return {"spots": len(jobs)}
    open_model = second_opinion.from_env(run_dir)
    if open_model is None:
        raise SystemExit("BASETEN_API_KEY is not set: there is no second model to confirm a yes")
    meter = CostMeter(min(args.cap_usd, COST_CAP_USD))
    api = MetaApi(run_dir / "calls_second_look.jsonl", meter)
    out = run_dir / f"second_look_scan{args.scan}.json"

    def save() -> dict:
        result = {"scan_id": args.scan, "city": args.city, "seed": SEED, "model": config.SPARK_MODEL,
                  "second_model": open_model.model, "cost_usd": round(meter.spent_usd, 4),
                  "summary": summary(jobs, unlisted, by_idx), "spots": jobs}
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result

    try:
        for job, answer in zip(jobs, run_parallel(lambda j: ask_spark(api, standard, j), jobs, WORKERS)):
            job["spark"] = answer
        yes = [j for j in jobs if j["spark"].get("answer") == "yes" and j["spark"].get("visible") is True]
        print(f"Muse Spark said yes to {len(yes)}; asking Muse Spark Open")
        for job, answer in zip(yes, run_parallel(lambda j: ask_open(open_model, standard, j), yes, WORKERS)):
            job["open"] = answer
    finally:
        result = save()
    print(json.dumps(result["summary"], ensure_ascii=False, indent=1))
    print(f"${meter.spent_usd:.2f} spent; wrote {out}")
    return result


if __name__ == "__main__":
    main()
