"""A scan's findings as files a city can take: one GeoJSON of every finding and one CSV per survey item.

Why it is built this way:
- No survey item is special-cased. An item is whatever a row says it is: a check id from the scan's survey standard
  (curb-ramp warnings, streetlight poles, ...), a type another stage writes (KR tactile defects, objects in the path),
  or "entrance".
- Labels and defects follow the app's own rules (server.views.issue_label and entrance_label, Standard.is_defect), so
  the files say what the map says. On top of that, an inventory item (`inventory = true` in the standard: a list of
  assets such as streetlight poles) is never a defect, and neither is an entrance (the app counts them apart).
- A point is the photo's location. The pipeline does not project objects onto the map (plan 4 row contract), so
  claiming more precision here would mislead.
- Every feature names the photo's creator, its Mapillary link and the licence: Mapillary's terms require that
  attribution wherever a photo is referenced, and these files leave our hands.
- Rows a person rejected are left out unless asked for: a rejection means "the models were wrong here". A row a
  person could not settle from the photo either stays, as "Check on site" (review_status field_check).
- Finds the models set aside as not the target (pipeline.db.dropped) are not findings, but they are not hidden
  either: they get their own CSV, with the photo, the model's note and whether a person spot-checked it.
"""
import csv
import json
import re
from pathlib import Path

from pipeline.evidence import LICENSE, mapillary_link
from pipeline.survey_items import Standard
from server import views

ENTRANCE = "entrance"
PROPERTIES = ("item", "label", "answer", "review_status", "status", "defect", "photo_id", "photo_url",
              "captured_at", "creator", "license", "evidence", "scan_id", "row_id")
# review_status in words, for whoever opens the file in a GIS or a spreadsheet
STATUS_TEXT = {"auto": "Found by the models", "needs_review": "To review", "verified": "Confirmed by a person",
               "field_check": "Check on site", "rejected": "Rejected by a person"}
UNSAFE = re.compile(r"[^a-z0-9_]+")


def is_inventory(check) -> bool:
    return bool(getattr(check, "inventory", False))


def title(item: str, check) -> str:
    """What the item is about, for a person: the check's target when it has one, else the item id."""
    return (getattr(check, "target", "") or "").strip() or item


def is_defect(item: str, row: dict, standard: Standard) -> bool:
    if item == ENTRANCE or is_inventory(standard.check(item)):
        return False
    return standard.is_defect(row)


def label(item: str, row: dict, standard: Standard) -> str:
    if item == ENTRANCE:
        return views.entrance_label(row)["text"]
    return views.issue_label(row, standard)["text"]


def answer(item: str, row: dict):
    """The row's current answer: the step count for an entrance, else object_label (yes/no/unclear for a check, the
    object's kind for an object in the path, empty for a KR defect class)."""
    return row.get("step_count") if item == ENTRANCE else row.get("object_label")


def _photo(row: dict, frame: dict | None) -> dict:
    """The photo a row was judged on. The evidence record names it exactly; the frame fills in what it lacks."""
    ev, frame = row.get("evidence") or {}, frame or {}
    image_id = ev.get("image_id") or frame.get("image_id")
    lat, lng = frame.get("lat"), frame.get("lng")
    if lat is None or lng is None:
        lat, lng = row.get("lat"), row.get("lng")
    return {"photo_id": image_id, "photo_url": mapillary_link(image_id) if image_id else None,
            "captured_at": row.get("captured_at") or ev.get("captured_at") or frame.get("captured_at"),
            "creator": ev.get("creator") or frame.get("creator"), "license": LICENSE,
            "evidence": f"evidence/{ev['image']}" if ev.get("image") else None, "lat": lat, "lng": lng}


def feature(item: str, row: dict, frame: dict | None, standard: Standard, scan_id: int) -> dict:
    """One GeoJSON Feature for one row, at its photo's location. `evidence` is relative to the run folder."""
    photo = _photo(row, frame)
    lat, lng = photo.pop("lat"), photo.pop("lng")
    kind = ENTRANCE if item == ENTRANCE else "issue"
    props = {"item": item, "label": label(item, row, standard), "answer": answer(item, row),
             "review_status": row["review_status"],
             "status": STATUS_TEXT.get(row["review_status"], row["review_status"]),
             "defect": is_defect(item, row, standard), **photo,
             "scan_id": scan_id, "row_id": f"{kind}{row['id']}"}
    geometry = {"type": "Point", "coordinates": [lng, lat]} if None not in (lat, lng) else None
    return {"type": "Feature", "geometry": geometry, "properties": {k: props[k] for k in PROPERTIES}}


def rows(scan_data: dict) -> list[tuple[str, dict]]:
    """(item, row) for every survey row of the scan: sidewalk issues in id order, then entrances."""
    return ([(r["issue_type"], r) for r in scan_data["issues"]]
            + [(ENTRANCE, r) for r in scan_data["entrances"]])


def features(scan_data: dict, standard: Standard, include_rejected: bool = False) -> list[dict]:
    scan_id = scan_data["scan"]["id"]
    by_idx = {f["idx"]: f for f in scan_data["frames"]}
    return [feature(item, row, by_idx.get(row.get("frame_idx")), standard, scan_id)
            for item, row in rows(scan_data) if include_rejected or row["review_status"] != "rejected"]


def _cell(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _write_csv(path: Path, feats: list[dict]) -> None:
    # utf-8-sig: Excel then reads non-ASCII labels and place names correctly; QGIS and pandas skip the mark.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        out = csv.writer(f)
        out.writerow(["lat", "lng", *PROPERTIES])
        for feat in feats:
            lng, lat = feat["geometry"]["coordinates"] if feat["geometry"] else (None, None)
            out.writerow([_cell(v) for v in (lat, lng, *(feat["properties"][k] for k in PROPERTIES))])


DROPPED_COLUMNS = ("lat", "lng", "item", "photo_id", "photo_url", "captured_at", "creator", "license", "audited",
                   "model_answer", "model_note", "scan_id")


def _write_dropped(path: Path, scan_data: dict) -> None:
    by_idx = {f["idx"]: f for f in scan_data["frames"]}
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        out = csv.writer(f)
        out.writerow(DROPPED_COLUMNS)
        for d in scan_data["dropped"]:
            frame = by_idx.get(d.get("frame_idx")) or {}
            check = (d.get("model_outputs") or {}).get("check") or {}
            image_id = frame.get("image_id")
            row = {"lat": d.get("lat", frame.get("lat")), "lng": d.get("lng", frame.get("lng")),
                   "item": d["issue_type"], "photo_id": image_id,
                   "photo_url": mapillary_link(image_id) if image_id else None,
                   "captured_at": d.get("captured_at") or frame.get("captured_at"), "creator": frame.get("creator"),
                   "license": LICENSE, "audited": bool(d.get("audited")), "model_answer": check.get("answer"),
                   "model_note": check.get("note"), "scan_id": scan_data["scan"]["id"]}
            out.writerow([_cell(row[k]) for k in DROPPED_COLUMNS])


def write(scan_data: dict, standard: Standard, out_dir: Path) -> dict:
    """survey_scan<N>.geojson with every finding, survey_scan<N>_<item>.csv per item found, and
    survey_scan<N>_dropped.csv when the models set finds aside. Returns {"geojson": path, "csv": {item: path}} and
    "dropped": path when there is one."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scan_id = scan_data["scan"]["id"]
    feats = features(scan_data, standard)
    geojson = out_dir / f"survey_scan{scan_id}.geojson"
    geojson.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    csvs = {}
    for item in dict.fromkeys(f["properties"]["item"] for f in feats):
        path = out_dir / f"survey_scan{scan_id}_{UNSAFE.sub('_', item.lower())}.csv"
        _write_csv(path, [f for f in feats if f["properties"]["item"] == item])
        csvs[item] = path
    paths = {"geojson": geojson, "csv": csvs}
    if scan_data.get("dropped"):
        paths["dropped"] = out_dir / f"survey_scan{scan_id}_dropped.csv"
        _write_dropped(paths["dropped"], scan_data)
    return paths
