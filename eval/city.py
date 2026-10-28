"""Compare one scan's survey rows for one item with the city's own inventory layer (GeoJSON points).

    python -m eval.city --run R --scan 1 --item streetlight_pole --layer poles.geojson --id-field OBJECTID --where IS_STREETLIGHT=YES --inventory

Writes <data>/runs/<run>/city_<item>_scan<id>.json. The item id, the layer and its field names are arguments, so the
same code scores poles, trees, litter cans, bike racks, signs or curb ramps.

A row is paired with the city point it looks at, not only the nearest one: two poles on the corners of a crossing are
metres apart but tens of degrees apart seen from the photo, so the row's direction (photo compass plus where the box
sits across the image) decides between them. Only points a photo could have shown can be missed. The city record is
not ground truth either: a row with no city point is "not in the city record" (new, moved, or a false find), a row
that could only pair with a point another row already found is a repeat (two photos of one thing), and a point
installed after the photo is its own class. A row a person rejected whose photo looked straight at a city point
nobody found is listed apart: the person and the record disagree, so the place needs a visit."""
import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eval.store import load_scan, open_ro
from pipeline import config, geo

FOV = 70.0  # degrees across a photo: a box centre on the right edge looks FOV/2 right of the compass
MAX_M = 30.0
MAX_DEG = 20.0
MAX_M_NO_DIR = 12.0  # a row without a direction can only be trusted to mean something this close
COVER_M = 20.0
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
ANSWERS = ("yes", "no")


def bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Compass bearing, 0-360, from the first position toward the second."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


def angle_between(a: float, b: float) -> float:
    """Smallest angle between two bearings, 0-180."""
    return abs((a - b + 180) % 360 - 180)


def row_direction(row: dict, frame: dict, fov: float = FOV) -> float | None:
    """Bearing from the photo to the row's object: compass + (box centre x / 1000 - 0.5) * fov. None when the row
    has no box or the photo no compass."""
    box = ((row.get("model_outputs") or {}).get("check") or {}).get("box") or []
    if len(box) != 4 or frame.get("compass") is None:
        return None
    return (frame["compass"] + ((box[0] + box[2]) / 2000 - 0.5) * fov) % 360


def row_answer(row: dict) -> str | None:
    """yes, no or unclear: a verified row's last reviewer answer if the reviewer gave one, else the row's answer."""
    if row.get("review_status") == "verified":
        given = [c["answer"] for c in row.get("corrections") or [] if c.get("action") == "answer"]
        if given:
            return given[-1]
    return row.get("object_label")


def load_points(layer: dict | list[dict], id_field: str | None = None) -> list[dict]:
    """GeoJSON Point features (a FeatureCollection or a list of features) -> {"id", "lat", "lng", "props"}. A feature
    without a point is skipped: the city never placed it. The id is properties[id_field], else the feature id, else
    its position in the layer, so pass id_field for ids that survive a new download."""
    features = layer["features"] if isinstance(layer, dict) else layer
    out = []
    for n, feature in enumerate(features):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point" or not geometry.get("coordinates"):
            continue
        props = feature.get("properties") or {}
        if id_field and props.get(id_field) is None:
            raise ValueError(f"feature {n} has no {id_field}")
        lng, lat = geometry["coordinates"][:2]
        out.append({"id": props[id_field] if id_field else feature.get("id", n), "lat": lat, "lng": lng,
                    "props": props})
    return out


def installed_at(props: dict, field: str | None) -> datetime | None:
    """The install date from epoch milliseconds (the ArcGIS export format); None when there is no field or value."""
    value = props.get(field) if field else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} is not epoch milliseconds: {value!r}")
    return EPOCH + timedelta(milliseconds=value)  # fromtimestamp fails before 1970 on Windows


def _when(captured_at: str | None) -> datetime | None:
    return datetime.fromisoformat(captured_at.replace("Z", "+00:00")) if captured_at else None


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def city_answer(props: dict, field: str | None, yes: str | None) -> str | None:
    """The city's value as yes or no: yes when it equals `yes` as text, None when the city left it empty."""
    value = props.get(field) if field else None
    if value is None or str(value).strip() == "":
        return None
    return "yes" if str(value).strip() == yes else "no"


def near_track(points: list[dict], frames: list[dict], margin_m: float) -> list[dict]:
    """Points inside the photos' bounding box grown by margin_m (plus 1 m), so a citywide layer of 100k points is cut
    to the streets the scan walked before any distance is measured."""
    if not frames:
        return []
    lats, lngs = [f["lat"] for f in frames], [f["lng"] for f in frames]
    dlat = math.degrees((margin_m + 1) / geo.EARTH_RADIUS_M)
    dlng = dlat / math.cos(math.radians(min(89.0, max(abs(v) for v in lats) + dlat)))
    return [p for p in points if min(lats) - dlat <= p["lat"] <= max(lats) + dlat
            and min(lngs) - dlng <= p["lng"] <= max(lngs) + dlng]


def candidates(rows: list[dict], frames_by_idx: dict[int, dict], points: list[dict], *, fov: float, max_m: float,
               max_deg: float, max_m_no_dir: float) -> list[tuple[float, int, int, float, float | None]]:
    """(score, row index, point index, metres, degrees off) for every allowed pair; score = offset/max_deg +
    dist/max_m. A row without a direction scores its offset as max_deg, so rows that know where they looked choose
    first."""
    out = []
    for ri, row in enumerate(rows):
        frame = frames_by_idx.get(row.get("frame_idx"))
        if frame is None:
            continue
        direction = row_direction(row, frame, fov)
        for pi, p in enumerate(points):
            dist = geo.haversine_m(frame["lat"], frame["lng"], p["lat"], p["lng"])
            if direction is None:
                if dist <= max_m_no_dir:
                    out.append((1.0 + dist / max_m, ri, pi, dist, None))
                continue
            offset = angle_between(direction, bearing_deg(frame["lat"], frame["lng"], p["lat"], p["lng"]))
            if dist <= max_m and offset <= max_deg:
                out.append((offset / max_deg + dist / max_m, ri, pi, dist, offset))
    return out


def assign(cands: list[tuple]) -> list[tuple[int, int, float, float | None]]:
    """One-to-one, greedy: the lowest score first, each row and each point used at most once. Ties go to the earlier
    row, then the earlier point, so a run is repeatable."""
    used_rows: set[int] = set()
    used_points: set[int] = set()
    out = []
    for _, ri, pi, dist, offset in sorted(cands, key=lambda c: c[:3]):
        if ri not in used_rows and pi not in used_points:
            used_rows.add(ri)
            used_points.add(pi)
            out.append((ri, pi, dist, offset))
    return out


def covering_frames(point: dict, frames: list[dict], fov: float = FOV, cover_m: float = COVER_M) -> list[dict]:
    """Photos that could show the point: within cover_m and inside the view, compass +- fov/2. A photo without a
    compass covers nothing, since where it looked is unknown."""
    out = []
    for f in frames:
        if f.get("compass") is None:
            continue
        if geo.haversine_m(f["lat"], f["lng"], point["lat"], point["lng"]) > cover_m:
            continue
        if angle_between(f["compass"], bearing_deg(f["lat"], f["lng"], point["lat"], point["lng"])) <= fov / 2:
            out.append(f)
    return out


def _after_photo(point: dict, field: str | None, photos: list[dict]) -> dict | None:
    """{"installed", "photo"} when the point was installed after the latest of these photos, else None."""
    installed = installed_at(point["props"], field)
    taken = [w for w in (_when(f.get("captured_at")) for f in photos) if w is not None]
    if installed is None or not taken or installed <= max(taken):
        return None
    return {"installed": _iso(installed), "photo": _iso(max(taken))}


def _agreement(matched: list[dict]) -> dict:
    """Counts over matched pairs: an unclear row first, then a city point with no value, then agree or disagree."""
    out = {"agree": 0, "disagree": 0, "unclear": 0, "city_unknown": 0}
    for m in matched:
        if m["row_answer"] not in ANSWERS:
            out["unclear"] += 1
        elif m["city_value"] is None:
            out["city_unknown"] += 1
        else:
            out["agree" if m["row_answer"] == m["city_value"] else "disagree"] += 1
    return out


def evaluate(rows: list[dict], frames: list[dict], points: dict | list[dict], item_id: str, *,
             inventory: bool = False, fov: float = FOV, max_m: float = MAX_M, max_deg: float = MAX_DEG,
             cover_m: float = COVER_M, max_m_no_dir: float = MAX_M_NO_DIR, installed_field: str | None = None,
             truth_field: str | None = None, truth_yes: str | None = None, id_field: str | None = None,
             where: dict | None = None) -> dict:
    """Pair the item's rows with the layer's points and sort every point into found, missed, installed after the
    photo or not photographed. A found point was photographed by definition, so covered = found + missed and
    found_share is at most 1; a point installed after its photo leaves both. `where` keeps only points whose
    properties equal the given values, compared as text (Seattle's pole layer: IS_STREETLIGHT=YES)."""
    layer = [p for p in load_points(points, id_field)
             if all(str(p["props"].get(k)) == str(v) for k, v in (where or {}).items())]
    kept = [r for r in rows if r["issue_type"] == item_id and r.get("review_status") != "rejected"
            and not (inventory and row_answer(r) == "no")]
    located = [f for f in frames if f.get("lat") is not None and f.get("lng") is not None]
    by_idx = {f["idx"]: f for f in located}
    near = near_track(layer, located, max(max_m, max_m_no_dir, cover_m))
    cands = candidates(kept, by_idx, near, fov=fov, max_m=max_m, max_deg=max_deg, max_m_no_dir=max_m_no_dir)
    pairs = assign(cands)

    matched, after = [], []
    for ri, pi, dist, offset in pairs:
        row, point = kept[ri], near[pi]
        late = _after_photo(point, installed_field, [by_idx[row["frame_idx"]]])
        if late:
            after.append({"point_id": point["id"], "issue_id": row["id"], **late})
            continue
        matched.append({"issue_id": row["id"], "point_id": point["id"], "dist_m": round(dist, 1),
                        "offset_deg": None if offset is None else round(offset, 1), "row_answer": row_answer(row),
                        "city_value": city_answer(point["props"], truth_field, truth_yes)})
    paired_rows, paired_points = {ri for ri, *_ in pairs}, {pi for _, pi, *_ in pairs}
    # An unpaired row that could have paired with a point another row took photographed that same thing again (two
    # pieces of one walk past a pole, or two walks): a repeat. Only a row that reaches no city point is outside it.
    repeats = {ri for _, ri, pi, *_ in cands if ri not in paired_rows and pi in paired_points}

    missed, not_photographed = [], len(layer) - len(near)
    for pi, point in enumerate(near):
        if pi in paired_points:
            continue
        cover = covering_frames(point, located, fov, cover_m)
        if not cover:
            not_photographed += 1
        elif late := _after_photo(point, installed_field, cover):
            after.append({"point_id": point["id"], "issue_id": None, **late})
        else:
            missed.append(point["id"])

    # A row a person rejected ("not one") whose photo looked straight at a city point the scan did not find: the person
    # and the city record disagree (the record is old, or the object was too far to tell). Neither is taken as the
    # truth; the place is listed for a site visit. A row without a direction does not count: its photo may not show it.
    rejected = [r for r in rows if r["issue_type"] == item_id and r.get("review_status") == "rejected"]
    disputed = [{"issue_id": rejected[ri]["id"], "point_id": near[pi]["id"], "dist_m": round(dist, 1),
                 "offset_deg": round(offset, 1), "city_value": city_answer(near[pi]["props"], truth_field, truth_yes)}
                for ri, pi, dist, offset in assign(
                    [c for c in candidates(rejected, by_idx, near, fov=fov, max_m=max_m, max_deg=max_deg,
                                           max_m_no_dir=max_m_no_dir) if c[4] is not None and c[2] not in paired_points])]

    covered = len(matched) + len(missed)
    out = {"item": item_id, "rows": len(kept), "points_in_layer": len(layer), "covered": covered,
           "matched": sorted(matched, key=lambda m: m["issue_id"]),
           "found_share": round(len(matched) / covered, 3) if covered else None,
           "rows_unmatched": sorted(r["id"] for ri, r in enumerate(kept) if ri not in paired_rows | repeats),
           "rows_repeat": sorted(kept[ri]["id"] for ri in repeats),
           "missed_covered": missed, "not_photographed": not_photographed, "installed_after_photo": after,
           "rejected_at_city_point": sorted(disputed, key=lambda d: d["issue_id"]),
           "params": {"inventory": inventory, "fov": fov, "max_m": max_m, "max_deg": max_deg,
                      "max_m_no_dir": max_m_no_dir, "cover_m": cover_m, "id_field": id_field,
                      "installed_field": installed_field, "truth_field": truth_field, "truth_yes": truth_yes,
                      "where": dict(where or {})}}
    if truth_field:
        out.update(_agreement(matched))
    return out


def to_markdown(r: dict) -> str:
    scan = f", scan {r['scan_id']}" if "scan_id" in r else ""
    share = f" ({r['found_share']} of photographed)" if r["found_share"] is not None else ""
    lines = [f"City record: {r['item']}{scan}", "", "| | Count |", "|---|---|",
             f"| Survey rows | {r['rows']} |",
             f"| City points in the layer | {r['points_in_layer']} |",
             f"| Photographed | {r['covered']} |",
             f"| Found | {len(r['matched'])}{share} |",
             f"| Photographed, not found | {len(r['missed_covered'])} |",
             f"| Installed after the photo | {len(r['installed_after_photo'])} |",
             f"| Not photographed | {r['not_photographed']} |",
             f"| Rows repeating a found city point | {len(r.get('rows_repeat', []))} |",
             f"| Rows not in the city record | {len(r['rows_unmatched'])} |",
             f"| Rejected by a person where the photo looked at a city point | "
             f"{len(r.get('rejected_at_city_point', []))} |"]
    if "agree" in r:
        lines += [f"| Answer agrees with the city record | {r['agree']} |",
                  f"| Answer differs from the city record | {r['disagree']} |",
                  f"| Answer unclear | {r['unclear']} |",
                  f"| No city value | {r['city_unknown']} |"]
    lines.append("")
    if r.get("rows_repeat"):
        lines.append(f"- Repeats a found city point (two photos of one thing): rows {r['rows_repeat']}")
    if r["rows_unmatched"]:
        lines.append(f"- Not in the city record (new, moved, or a false find): rows {r['rows_unmatched']}")
    if r["missed_covered"]:
        lines.append(f"- Photographed but not found: city points {r['missed_covered']}")
    if r.get("rejected_at_city_point"):
        d = r["rejected_at_city_point"]
        lines.append(f"- A person rejected these but the photo looked at a city point (check on site): rows "
                     f"{[x['issue_id'] for x in d]}, city points {[x['point_id'] for x in d]}")
    for a in r["installed_after_photo"]:
        seen = f", row {a['issue_id']}" if a["issue_id"] is not None else ""
        lines.append(f"- Installed after the photo: city point {a['point_id']} (installed {a['installed'][:10]}, "
                     f"photo {a['photo'][:10]}{seen})")
    if "agree" in r:
        for m in r["matched"]:
            if m["row_answer"] in ANSWERS and m["city_value"] not in (None, m["row_answer"]):
                lines.append(f"- Answer differs: row {m['issue_id']} says {m['row_answer']}, city point "
                             f"{m['point_id']} says {m['city_value']}")
    return "\n".join(lines).rstrip()


def parse_where(pairs: list[str]) -> dict:
    """["KEY=VALUE", ...] -> {KEY: VALUE}; raises ValueError on a pair without "="."""
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--where wants KEY=VALUE, got {pair!r}")
        out[key] = value
    return out


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--item", required=True, help="survey item id, the rows' issue_type")
    ap.add_argument("--layer", required=True, help="the city's GeoJSON point layer")
    ap.add_argument("--id-field", help="property that names a city point (default: feature id, else position)")
    ap.add_argument("--inventory", action="store_true", help="drop rows answered 'no': keep only found things")
    ap.add_argument("--installed-field", help="property with the install date in epoch milliseconds")
    ap.add_argument("--truth-field", help="property the rows' yes/no answer is compared with")
    ap.add_argument("--truth-yes", help="value of --truth-field that means yes")
    ap.add_argument("--fov", type=float, default=FOV)
    ap.add_argument("--max-m", type=float, default=MAX_M)
    ap.add_argument("--max-deg", type=float, default=MAX_DEG)
    ap.add_argument("--cover-m", type=float, default=COVER_M)
    ap.add_argument("--where", action="append", default=[], metavar="KEY=VALUE",
                    help="keep only city points with this property value (repeatable, all must match)")
    args = ap.parse_args(argv)
    if bool(args.truth_field) != bool(args.truth_yes):
        ap.error("--truth-field and --truth-yes go together")
    try:
        where = parse_where(args.where)
    except ValueError as exc:
        ap.error(str(exc))
    layer = json.loads(Path(args.layer).read_text(encoding="utf-8"))
    run_dir = config.run_dir(args.run)
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, args.scan)
    finally:
        conn.close()
    result = {"scan_id": args.scan, "layer": Path(args.layer).name,
              **evaluate(data["issues"], data["frames"], layer, args.item, inventory=args.inventory, fov=args.fov,
                         max_m=args.max_m, max_deg=args.max_deg, cover_m=args.cover_m,
                         installed_field=args.installed_field, truth_field=args.truth_field,
                         truth_yes=args.truth_yes, id_field=args.id_field, where=where)}
    out = run_dir / f"city_{args.item}_scan{args.scan}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(to_markdown(result))
    return out


if __name__ == "__main__":
    main()
