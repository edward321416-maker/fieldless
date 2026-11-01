"""Newer photos of a city's records (docs/specs/2026-11-01-fresh-photos-design.md).

    python -m eval.pano_look --run demo --scan 17 --city seattle --plan
    python -m eval.pano_look --run demo --scan 17 --city seattle

A survey uses flat photos, and an area's newest photos are often panoramas. This takes the records the survey's city
check lists and looks at each in the newest panorama taken near it, as eval.second_look does: Muse Spark first, Muse
Spark Open on a yes, and a record counts as seen only when both say so. Control spots with no city record go in with
the records. Each view is cut from the panorama toward the record, so a record is never outside the photo.

Writes <run>/pano_look_scan<N>.json. It spends at most COST_CAP_USD of Meta credit.
"""
import argparse
import dataclasses
import json
import random
from pathlib import Path

import cv2

from eval import city
from eval.city_check import load_city
from eval.second_look import CLEAR_M
from eval.store import load_scan, open_ro
from pipeline import config, geo, mapillary, panorama, second_opinion, survey_items
from pipeline.cost import CostCapReached, CostMeter
from pipeline.meta_api import MetaApi, error_text, image_part, text_part
from pipeline.parallel import run_parallel
from pipeline.survey_checks import SCHEMA, prompt

COST_CAP_USD = 0.85
SINCE = "2024-01-01"
EDGE_DEG = 0.0003  # a panorama just outside the area can show a record just inside it
MIN_M, MAX_M, BEST_M = 3.0, 20.0, 8.0
CONTROLS_PER_ITEM = 2
CONTROL_M = (8.0, 14.0)
DIRECTION_DEG = 20.0  # the box the model draws must lie this close to the middle of the view
WORKERS = 8
SEED = 31
ASK = ("City records list a {target} about {dist:.0f} m from the camera, near the middle of this photo. Records and "
       "photo positions can be off by several metres. Look at the middle part of the photo. Is there a {target} there? "
       "Answer yes only if you can see one there, and box it. Answer no if that area is in clear view and shows none. "
       "Answer unclear if it is blocked, too far, or cut off.")


def panos_of(bbox) -> list[dict]:
    """The panoramas taken in bbox since SINCE, as mapillary.frame_record makes them, newest first."""
    images, _ = mapillary.search_grid(tuple(bbox), n=3, passes=2)
    since = mapillary.ms(SINCE)
    sequences = sorted({i["sequence"] for i in images if i.get("is_pano") and i["captured_at"] >= since})
    frames = [f for found in run_parallel(mapillary.sequence_frames, sequences, 4) for f in found]
    west, south, east, north = bbox
    records = [mapillary.frame_record(f) for f in frames if f.get("is_pano") and f["captured_at"] >= since]
    return sorted((r for r in records if r["lat"] is not None and r["compass"] is not None
                   and south - EDGE_DEG <= r["lat"] <= north + EDGE_DEG and west - EDGE_DEG <= r["lng"] <= east + EDGE_DEG),
                  key=lambda r: r["captured_at"], reverse=True)


def city_records(check_file: dict, town: dict) -> list[dict]:
    """Every city record the survey's photos had in view: {"item", "city", "lat", "lng", "found"}."""
    root = config.data_dir() / town["root"]
    out = []
    for item, info in check_file["items"].items():
        keys = info["id_fields"]
        layer = json.loads((root / town["items"][item]["layer"]).read_text(encoding="utf-8"))["features"]
        where = {tuple(str(f["properties"].get(k)) for k in keys): f["geometry"]["coordinates"][:2]
                 for f in layer if f.get("geometry")}
        for pair in info["pairs"]:
            if pair.get("in_view"):
                lng, lat = where[tuple(str(pair["city"].get(k)) for k in keys)]
                out.append({"item": item, "city": pair["city"], "lat": lat, "lng": lng, "found": True})
        out += [{"item": item, "city": m["city"], "lat": m["lat"], "lng": m["lng"], "found": False} for m in info["missed"]]
    return out


def already_seen(run_dir: Path, scan: int) -> set[str]:
    """The records eval.second_look settled, which need no newer look to count as seen."""
    keys = set()
    first = run_dir / f"second_look_scan{scan}.json"
    if first.is_file():
        for job in json.loads(first.read_text(encoding="utf-8"))["spots"]:
            if job["kind"] == "record" and job["spark"].get("answer") == "yes" and (job.get("open") or {}).get("answer") == "yes":
                keys.add(json.dumps(job["city"], sort_keys=True))
    pilot = run_dir / f"second_look_pilot_scan{scan}.json"
    if pilot.is_file():
        for job in json.loads(pilot.read_text(encoding="utf-8"))["jobs"]:
            if job["kind"] == "record" and job["seen"]:
                keys.add(json.dumps(job["city"], sort_keys=True))
    return keys


def distance(pano: dict, point: dict) -> float:
    return geo.haversine_m(pano["lat"], pano["lng"], point["lat"], point["lng"])


def choose(point: dict, panos: list[dict], count: int = 2) -> list[dict]:
    """The panoramas to look at the point in: the newest month first, near BEST_M, at most one from a sequence."""
    near = [p for p in panos if MIN_M <= distance(p, point) <= MAX_M]
    near.sort(key=lambda p: (p["captured_at"][:7], -abs(distance(p, point) - BEST_M)), reverse=True)
    chosen: list[dict] = []
    for pano in near:
        if pano["sequence"] not in {c["sequence"] for c in chosen}:
            chosen.append(pano)
        if len(chosen) == count:
            break
    return chosen


def view_of(pano: dict, point: dict) -> dict:
    bearing = city.bearing_deg(pano["lat"], pano["lng"], point["lat"], point["lng"])
    return {"image_id": pano["id"], "captured_at": pano["captured_at"], "creator": pano["creator"],
            "dist_m": round(distance(pano, point), 1), "yaw": round((bearing - pano["compass"] + 180) % 360 - 180, 1)}


def control_spots(panos: list[dict], records: list[dict], count: int, rng: random.Random) -> list[dict]:
    spots: list[dict] = []
    for pano in rng.sample(panos, len(panos)):
        if len(spots) == count:
            break
        lat, lng = geo.offset_point(pano["lat"], pano["lng"], rng.uniform(0, 360), rng.uniform(*CONTROL_M))
        if all(geo.haversine_m(lat, lng, r["lat"], r["lng"]) >= CLEAR_M for r in records):
            spots.append({"lat": lat, "lng": lng, "pano": pano})
    return spots


def seen(job: dict) -> bool:
    return any((a.get("open") or {}).get("answer") == "yes" for a in job["answers"])


def summary(jobs: list[dict]) -> dict:
    records = [j for j in jobs if j["kind"] == "record"]
    controls = [j for j in jobs if j["kind"] == "control"]
    items: dict[str, dict] = {}
    for job in records:
        row = items.setdefault(job["item"], {"asked": 0, "seen": 0, "seen_again": 0, "newly_seen": 0})
        row["asked"] += 1
        if seen(job):
            row["seen"] += 1
            row["seen_again" if job["found"] else "newly_seen"] += 1
    return {"items": items, "records": len(records), "seen": sum(seen(j) for j in records),
            "newly_seen": sum(i["newly_seen"] for i in items.values()),
            "controls": len(controls), "controls_seen": sum(seen(j) for j in controls)}


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--scan", type=int, required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--plan", action="store_true", help="choose the views, cut them and stop: no model is called")
    args = parser.parse_args(argv)
    run_dir = config.run_dir(args.run)
    conn = open_ro(run_dir / "threshold.db")
    data = load_scan(conn, args.scan)
    conn.close()
    check_file = json.loads((run_dir / f"city_check_scan{args.scan}.json").read_text(encoding="utf-8"))
    town = load_city(args.city)
    cache = run_dir / f"panos_scan{args.scan}.json"
    if cache.is_file():
        panos = json.loads(cache.read_text(encoding="utf-8"))
    else:
        panos = panos_of(data["scan"]["bbox"])
        cache.write_text(json.dumps(panos), encoding="utf-8")
    records = city_records(check_file, town)
    rng = random.Random(SEED)
    jobs: list[dict] = []
    for record in records:
        chosen = choose(record, panos)
        if chosen:
            jobs.append({**record, "kind": "record", "views": [view_of(p, record) for p in chosen], "answers": []})
    root = config.data_dir() / town["root"]
    for item in check_file["items"]:
        layer = json.loads((root / town["items"][item]["layer"]).read_text(encoding="utf-8"))
        for spot in control_spots(panos, city.load_points(layer), CONTROLS_PER_ITEM, rng):
            jobs.append({"item": item, "kind": "control", "lat": spot["lat"], "lng": spot["lng"], "found": False,
                         "views": [view_of(spot["pano"], spot)], "answers": []})
    settled = already_seen(run_dir, args.scan)
    jobs = [j for j in jobs if j["kind"] == "control" or not (j["found"] or json.dumps(j["city"], sort_keys=True) in settled)]
    first = [j["views"][0] for j in jobs]
    wanted = {v["image_id"] for j in jobs for v in j["views"]}
    print(f"{len(panos)} panoramas, {len(records)} records, {sum(j['kind'] == 'record' for j in jobs)} within {MAX_M:.0f} m, "
          f"{sum(j['kind'] == 'control' for j in jobs)} controls, {len(first)} first views, {len(wanted)} panoramas to fetch")
    crops = run_dir / "pano_views"
    crops.mkdir(exist_ok=True)

    def fetch(image_id: str) -> None:
        path = config.photo_cache() / f"pano_{image_id}.jpg"
        if not path.is_file():
            mapillary.original_image(image_id, path, max_side=8000)

    def cut(spot_view: dict) -> Path:
        path = crops / f"{spot_view['image_id']}_{spot_view['yaw']:.0f}.jpg"
        if not path.is_file():
            img = cv2.imread(str(config.photo_cache() / f"pano_{spot_view['image_id']}.jpg"))
            cv2.imwrite(str(path), panorama.view(img, spot_view["yaw"]), [cv2.IMWRITE_JPEG_QUALITY, 90])
        return path

    run_parallel(fetch, sorted(wanted), 6)
    if args.plan:
        for job in rng.sample(jobs, min(4, len(jobs))):
            print(job["kind"], job["item"], job["city"] if job["kind"] == "record" else "", job["views"][0], cut(job["views"][0]))
        return {"jobs": len(jobs)}
    open_model = second_opinion.from_env(run_dir)
    if open_model is None:
        raise SystemExit("BASETEN_API_KEY is not set: there is no second model to confirm a yes")
    meter = CostMeter(COST_CAP_USD)
    api = MetaApi(run_dir / "calls_second_look.jsonl", meter)
    standard = survey_items.load(data["scan"]["jurisdiction"])

    def ask(args_):
        job, spot_view = args_
        check = standard.check(job["item"])
        check = dataclasses.replace(check, ask=ASK.format(target=check.target, dist=spot_view["dist_m"]))
        photo = cut(spot_view)
        entry = {"view": spot_view}
        try:
            entry["spark"] = api.spark_json([image_part(photo), text_part(prompt(check))], "pano_look", SCHEMA)
        except CostCapReached as exc:
            entry["spark"] = {"answer": "not_asked", "error": str(exc)}
        except Exception as exc:
            entry["spark"] = {"answer": "error", "error": error_text(exc)}
        answer = entry["spark"]
        box = answer.get("box") or []
        entry["aimed"] = len(box) == 4 and abs(panorama.box_angle(box)) <= DIRECTION_DEG
        if answer.get("answer") == "yes" and answer.get("visible") is True and entry["aimed"]:
            try:
                entry["open"] = open_model.answer(check, photo)
            except Exception as exc:
                entry["open"] = {"answer": "error", "error": error_text(exc)}
        job["answers"].append(entry)

    out = run_dir / f"pano_look_scan{args.scan}.json"

    def save() -> dict:
        result = {"scan_id": args.scan, "city": args.city, "model": config.SPARK_MODEL, "second_model": open_model.model,
                  "cost_usd": round(meter.spent_usd, 4), "summary": summary(jobs), "jobs": jobs}
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result

    try:
        run_parallel(ask, [(j, j["views"][0]) for j in jobs], WORKERS)
        second = [(j, j["views"][1]) for j in jobs if len(j["views"]) > 1 and not seen(j) and not j["found"]]
        print(f"first views done, ${meter.spent_usd:.2f}; {len(second)} second views")
        run_parallel(ask, second, WORKERS)
    finally:
        result = save()
    print(json.dumps(result["summary"], ensure_ascii=False, indent=1))
    print(f"${meter.spent_usd:.2f} spent; wrote {out}")
    return result


if __name__ == "__main__":
    main()
