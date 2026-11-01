"""One area's Asset Check: every city record the photos had in view and every object the city's lists do not have, one
row each, with what the photos settled.

    python -m eval.asset_check --run demo --scan 17 --city seattle

It adds no model judgement of its own. It reads what the survey's city check and the later looks already wrote
(eval.second_look, eval.pano_look), and blurs the photos those looks used, as a survey blurs its evidence photos. A
record the photos did not settle stays "Not determined", which does not mean missing.

A later look can be sent back: <run>/look_review_scan<N>.json names the rows an AI assistant looked at photo by photo,
and the ones whose box sat on something other than the record. Those rows go back to "Not determined".

Writes <run>/asset_check_scan<N>.json. The server attaches it to the scan; assist.asset_check turns it into the page
and the table a city's asset team takes. Nothing here writes to a city's records.
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval import precision
from eval.city import box_of
from eval.city_check import load_city
from eval.second_look import is_seen
from eval.store import load_scan, open_ro
from pipeline import config, evidence, survey_items
from pipeline.cost import CostMeter
from pipeline.meta_api import MetaApi
from pipeline.parallel import run_parallel

SEEN, NOT_DETERMINED, NOT_LISTED = "Seen in photo", "Not determined", "Not on the city list"
STATUSES = (SEEN, NOT_DETERMINED, NOT_LISTED)
BY_SURVEY, BY_LOOK, BY_PANORAMA = "Survey", "Second look", "Second look, panorama"
FIX, DATE, SAME, VISIT, COMPARE = ("Correct the record", "Update the date", "No change", "Visit on site",
                                   "Compare with the list")
NOT_SETTLED = "Not settled what this is"
CITY_POSITION = {"position_source": "City record", "position_accuracy": "City record"}
CAMERA_POSITION = {"position_source": "Camera position of the Mapillary photo",
                   "position_accuracy": "Unknown (within 50 ft)"}
COST_CAP_USD = 0.25  # two SAM requests a photo, for the photos of one area's later looks
WORKERS = 4


def month(value) -> str:
    """A date of the city's as YYYY-MM. The layers hold epoch milliseconds or text."""
    if isinstance(value, (int, float)) and value > 1e11:
        return datetime.fromtimestamp(value / 1000, timezone.utc).strftime("%Y-%m")
    return str(value)[:7] if value and str(value).strip() else ""


def record_key(city: dict) -> str:
    return json.dumps(city, sort_keys=True)


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _confirmed(job: dict) -> dict | None:
    """The first answer of a job that both models said yes to."""
    return next((a for a in job["answers"] if (a.get("open") or {}).get("answer") == "yes"), None)


def read_looks(run_dir: Path, scan: int, frames: list[dict]) -> tuple[dict[str, dict], dict]:
    """What the later looks settled, by record ({"how", "view", "box", "photo"}), and what they say about themselves."""
    creators = {f["image_id"]: f.get("creator") for f in frames}
    review = _read(run_dir / f"look_review_scan{scan}.json") or {}
    sent_back = {(r["image_id"], str(r["record"])) for r in review.get("sent_back", [])}
    looks: dict[str, dict] = {}

    def add(job: dict, how: str, view: dict, spark: dict, photo: Path) -> None:
        if any((view["image_id"], str(v)) in sent_back for v in job["city"].values()):
            return
        looks.setdefault(record_key(job["city"]), {
            "how": how, "view": {**view, "creator": view.get("creator") or creators.get(view["image_id"])},
            "box": spark.get("box") or None, "photo": photo})

    flat_controls = flat_false = pano_controls = pano_false = 0
    first = _read(run_dir / f"second_look_scan{scan}.json")
    if first:
        flat_controls += first["summary"]["controls"]
        flat_false += first["summary"]["controls_both_yes"]
        for job in first["spots"]:
            if job["kind"] == "record" and is_seen(job):
                add(job, BY_LOOK, job["view"], job["spark"], config.photo_cache() / f"{job['view']['image_id']}.jpg")
    pilot = _read(run_dir / f"second_look_pilot_scan{scan}.json")
    if pilot:
        flat_controls += pilot["summary"]["controls"]
        flat_false += pilot["summary"]["controls_seen"]
        for job in pilot["jobs"]:
            if job["kind"] == "record" and (answer := _confirmed(job)):
                add(job, BY_LOOK, answer["view"], answer["spark"], config.photo_cache() / f"{answer['view']['image_id']}.jpg")
    newer = _read(run_dir / f"pano_look_scan{scan}.json")
    if newer:
        pano_controls, pano_false = newer["summary"]["controls"], newer["summary"]["controls_seen"]
        for job in newer["jobs"]:
            if job["kind"] == "record" and (answer := _confirmed(job)):
                view = answer["view"]
                add(job, BY_PANORAMA, view, answer["spark"], run_dir / "pano_views" / f"{view['image_id']}_{view['yaw']:.0f}.jpg")
    controls = [{"photos": "Survey-date photos", "asked": flat_controls, "both_yes": flat_false},
                {"photos": "Panoramas", "asked": pano_controls, "both_yes": pano_false,
                 **({"note": review["newer_control_note"]} if review.get("newer_control_note") else {})}]
    return looks, {"controls": [c for c in controls if c["asked"]], "looked_at": review.get("looked_at", 0),
                   "sent_back": len(sent_back)}


def read_differs(run_dir: Path, scan: int) -> tuple[dict, dict | None]:
    """The records a photo contradicts by (item, record), without those the AI assistant looked at and sent back
    (look_review_scan<N>.json, "not_different"), and what eval.record_check says about itself."""
    checked = _read(run_dir / f"record_check_scan{scan}.json")
    if not checked:
        return {}, None
    review = _read(run_dir / f"look_review_scan{scan}.json") or {}
    sent_back = {(r["item"], str(r["record"])) for r in review.get("not_different", [])}
    differs = {(r["item"], r["record"]): {"city_says": r["city_says"], "photo_shows": r["photo_shows"]}
               for r in checked["rows"] if r["differs"] and (r["item"], r["record"]) not in sent_back}
    return differs, {**checked["summary"], "differ": len(differs), "sent_back": len(sent_back)}


def evidence_of(looks: dict[str, dict], scan: int) -> dict[Path, str]:
    """The evidence file each look's photo goes to, numbered by photo so two records in one photo share it."""
    return {photo: f"scan{scan}_look{n}_{next(v['view']['image_id'] for v in looks.values() if v['photo'] == photo)}.jpg"
            for n, photo in enumerate(sorted({v["photo"] for v in looks.values()}, key=str))}


def blur_looks(looks: dict[str, dict], run_dir: Path, scan: int, api=None) -> float:
    """Give every look its blurred evidence photo, with Mapillary's attribution. A failed blur raises, so an unblurred
    photo is never stored. Photos already blurred are not sent again: <run>/look_evidence_scan<N>.json keeps what each
    blur found. Returns what the blurring spent."""
    names = evidence_of(looks, scan)
    folder = run_dir / "evidence"
    made_path = run_dir / f"look_evidence_scan{scan}.json"
    made = _read(made_path) or {}
    missing = [p for p, name in names.items() if name not in made or not (folder / name).is_file()]
    meter = CostMeter(COST_CAP_USD)
    if missing and api is None:
        api = MetaApi(run_dir / "calls_second_look.jsonl", meter)

    def blur(photo: Path) -> None:
        view = next(v["view"] for v in looks.values() if v["photo"] == photo)
        made[names[photo]] = evidence.make_evidence(api, photo, view, folder / names[photo])

    try:
        run_parallel(blur, missing, WORKERS)
    finally:
        made_path.write_text(json.dumps(made, ensure_ascii=False, indent=1), encoding="utf-8")
    for look in looks.values():
        look["evidence"] = made[names[look["photo"]]]
    return meter.spent_usd


def asset_name(standard, item: str) -> str:
    target = standard.check(item).target.strip()
    return target[:1].upper() + target[1:]


def sure_of(row: dict, label: dict | None) -> bool:
    """The models were sure, and the assistant's check, when there is one, agrees."""
    return row["models_unsure"] != "yes" and (label is None or label.get("value") == "yes")


def action_of(row: dict, label: dict | None, differs: dict | None, verified: bool) -> str | None:
    """What a city does with a row. None: the AI assistant found the object wrong or a repeat, so it leaves the list.
    A visit is always a city record, so one number means the same on every surface; an object on no list is to compare,
    sure of or not. verified: the list's date says when the city last verified the record, not just when the list was
    written."""
    if row["status"] == NOT_LISTED:
        return None if label and (label.get("dup_of") is not None or label.get("value") == "no") else COMPARE
    if row["status"] == NOT_DETERMINED or not sure_of(row, label):
        return VISIT
    if differs:
        return FIX
    return DATE if verified and row["photo_newer_than_city_date"] == "yes" else SAME


def said(row: dict, action: str, differs: dict | None, cfg: dict, sure: bool = True) -> dict:
    """The two sides of a change. The city's own date stays in its column: a photo date never fills it."""
    if action == COMPARE and not sure:
        return {"city_says": "", "photo_shows": NOT_SETTLED}
    if action == FIX:
        return {"city_says": differs["city_says"], "photo_shows": differs["photo_shows"]}
    if action == DATE:
        return {"city_says": f"{cfg['checked_field']} {row['city_date']}",
                "photo_shows": f"Seen in a photo of {row['seen_in_photo_on']}"}
    return {"city_says": "", "photo_shows": ""}


def tally(rows: list[dict]) -> dict:
    status = Counter(r["status"] for r in rows)
    return {"in_view": status[SEEN] + status[NOT_DETERMINED], "seen": status[SEEN],
            "seen_by": dict(Counter(r["how_seen"] for r in rows if r["status"] == SEEN)),
            "not_determined": status[NOT_DETERMINED], "not_listed": status[NOT_LISTED],
            "not_listed_unsure": sum(r["status"] == NOT_LISTED and r["models_unsure"] == "yes" for r in rows),
            "seen_unsure": sum(r["status"] == SEEN and r["models_unsure"] == "yes" for r in rows),
            "newer": sum(r["photo_newer_than_city_date"] == "yes" for r in rows)}


def item_rows(item: str, info: dict, cfg: dict, features: list[dict], name: str, issues: list[dict],
              looks: dict[str, dict]) -> list[dict]:
    keys = info["id_fields"]
    by_key = {tuple(str(f["properties"].get(k)) for k in keys): f for f in features if f.get("geometry")}
    base = {"item": item, "asset": name}

    def record_row(city: dict, status: str, how: str = "", issue: dict | None = None, look: dict | None = None) -> dict:
        feature = by_key[tuple(str(city.get(k)) for k in keys)]
        props = feature["properties"]
        lng, lat = feature["geometry"]["coordinates"][:2]
        last = month(props.get(cfg["checked_field"])) if cfg.get("checked_field") else ""
        ev = (issue or {}).get("evidence") or (look or {}).get("evidence")
        seen_on = str((ev or {}).get("captured_at") or "")[:10]
        return {**base, "city_list": info["layer"], "city_record": str(city.get(cfg["record_field"]) or city.get(keys[0])),
                "city_location": str(props.get(cfg.get("place_field")) or ""), "status": status, "how_seen": how,
                "issue_id": issue["id"] if issue else None,
                "models_unsure": "yes" if issue and issue.get("object_label") == "unclear" else "",
                "city_date": last, "seen_in_photo_on": seen_on,
                "photo_newer_than_city_date": "yes" if last and seen_on and seen_on[:7] > last else "",
                "city_condition": str(props.get(cfg.get("condition_field")) or "").strip(),
                "lat": round(lat, 6), "lng": round(lng, 6), **CITY_POSITION,
                "box": box_of(issue) if issue else (look or {}).get("box"), "evidence": ev}

    rows = [record_row(pair["city"], SEEN, BY_SURVEY, issue=next(i for i in issues if i["id"] == pair["issue_id"]))
            for pair in info["pairs"] if pair.get("in_view")]
    for missed in info["missed"]:
        look = looks.get(record_key(missed["city"]))
        rows.append(record_row(missed["city"], SEEN, look["how"], look=look) if look
                    else record_row(missed["city"], NOT_DETERMINED))
    paired = {pair["issue_id"] for pair in info["pairs"]}
    for issue in issues:
        if issue["issue_type"] == item and issue["id"] not in paired:
            ev = issue.get("evidence")
            rows.append({**base, "city_list": "", "city_record": "", "city_location": "", "status": NOT_LISTED,
                         "how_seen": "", "issue_id": issue["id"],
                         "models_unsure": "yes" if issue.get("object_label") == "unclear" else "",
                         "city_date": "", "seen_in_photo_on": str((ev or {}).get("captured_at") or "")[:10],
                         "photo_newer_than_city_date": "", "city_condition": "", "lat": round(issue["lat"], 6),
                         "lng": round(issue["lng"], 6), **CAMERA_POSITION, "box": box_of(issue), "evidence": ev})
    return rows


def build(data: dict, check_file: dict, town: dict, layers: dict[str, list[dict]], standard, looks: dict[str, dict],
          trust: dict, labels: dict | None = None, differs: dict | None = None) -> dict:
    """data as eval.store.load_scan returns it; layers: each item's GeoJSON features; looks and trust as read_looks
    returns them, each look with its evidence; labels: the AI assistant's check of the findings (eval.precision);
    differs: the records a photo contradicts, by (item, record) (eval.record_check)."""
    rows, listed, items = [], [], []
    left_out = Counter(repeats=0, wrong=0)
    issues = [i for i in data["issues"] if i["review_status"] != "rejected"]
    judged = (labels or {}).get("issues", {})
    for item, info in check_file["items"].items():
        name, cfg = asset_name(standard, item), town["items"][item]
        found = item_rows(item, info, cfg, layers[item], name, issues, looks)
        rows += found
        kept = []
        for row in found:
            label = judged.get(str(row.pop("issue_id")))
            differ = (differs or {}).get((item, row["city_record"]))
            action = action_of(row, label, differ, bool(cfg.get("verified")))
            if action is None:
                left_out["repeats" if label.get("dup_of") is not None else "wrong"] += 1
            else:
                kept.append({**row, "action": action, **said(row, action, differ, cfg, sure_of(row, label))})
        listed += kept
        items.append({"item": item, "asset": name, "city_list": info["layer"], **tally(found),
                      "actions": dict(Counter(r["action"] for r in kept))})
    outside = sum(not p.get("in_view") for info in check_file["items"].values() for p in info["pairs"])
    checked = next((r for r in precision.summary(labels) if r["item"] == "all"), None) if labels else None
    months = sorted({r["seen_in_photo_on"][:7] for r in rows if r["status"] == SEEN and r["seen_in_photo_on"]})
    no_visit = sum(bool(r["city_record"]) and r["action"] != VISIT for r in listed)
    compare_unsure = sum(r["photo_shows"] == NOT_SETTLED for r in listed)
    return {"scan_id": data["scan"]["id"], "city": town["city"], **({"credit": town["credit"]} if "credit" in town else {}),
            "items": items, "totals": tally(rows),
            "actions": dict(Counter(r["action"] for r in listed)), "no_visit": no_visit, "compare_unsure": compare_unsure,
            "left_out": dict(left_out),
            "rows": listed,
            "trust": {**trust, "photo_months": months, "outside_view": outside,
                      **({"findings": {k: checked[k] for k in ("n", "yes", "no", "unclear")}} if checked else {})}}


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--scan", type=int, required=True)
    parser.add_argument("--city", default="seattle", help="a file in eval/cities, without .toml")
    args = parser.parse_args(argv)
    run_dir = config.run_dir(args.run)
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, args.scan)
    finally:
        conn.close()
    check_file = json.loads((run_dir / f"city_check_scan{args.scan}.json").read_text(encoding="utf-8"))
    town = load_city(args.city)
    root = config.data_dir() / town["root"]
    layers = {item: json.loads((root / town["items"][item]["layer"]).read_text(encoding="utf-8"))["features"]
              for item in check_file["items"]}
    looks, trust = read_looks(run_dir, args.scan, data["frames"])
    spent = blur_looks(looks, run_dir, args.scan)
    differs, record_trust = read_differs(run_dir, args.scan)
    if record_trust:
        trust["record_check"] = record_trust
    out = build(data, check_file, town, layers, survey_items.load(data["scan"]["jurisdiction"]), looks, trust,
                _read(run_dir / f"precision_scan{args.scan}.json"), differs)
    path = run_dir / f"asset_check_scan{args.scan}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"totals": out["totals"], "trust": out["trust"]}, ensure_ascii=False, indent=1))
    print(f"${spent:.2f} spent; wrote {path}")
    return path


if __name__ == "__main__":
    main()
