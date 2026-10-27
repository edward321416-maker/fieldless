"""Test sets for any street asset made from a city's point layer, so the agent and SAM phrase tests work for every
survey item without hand labels:

    python -m assist.testset_build --name seattle_poles --target "streetlight pole" --layer poles.geojson \\
        --photos photos.json [--where IS_STREETLIGHT=YES] [--n-pos 12 --n-neg 6] [--out DIR]

Hand labels exist for one item (curb ramps, seattle_ramps.json), but a survey covers many assets (streetlight poles,
trees, litter cans, bike racks, signs) and cities publish where each one stands as GeoJSON points. A photo is a
positive when a point lies a few metres ahead, inside a narrow cone around the camera's compass heading; it is a
negative when no point lies anywhere in a wider and longer cone. Everything between (a point too close to be in frame,
far off, or at the edge of the view) is unclear and left out, so the gap between the two cones absorbs GPS and compass
error and the set holds only clear cases. Photos without a compass heading cannot be placed and are skipped.

Geometry is not sight: a point can be stale and an asset can be hidden by a parked truck. Each photo keeps a note
naming the city point, its distance and its angle off the heading so a person can audit it, and the file says it was
not checked by eye. The photos chosen are spread out, one sequence at a time (a sequence is one walk or drive) and
never two within 8 m of each other, so a long sequence cannot fill the set with near-copies of one spot. There is no
randomness: the same inputs give the same file."""
import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from assist.testset import FOLDER
from pipeline.geo import EARTH_RADIUS_M, haversine_m

DEFAULTS = {"min_m": 4.0, "max_m": 20.0, "pos_deg": 20.0, "neg_m": 35.0, "neg_deg": 45.0}
SPACING_M = 8.0  # no two chosen photos closer than this
M_PER_DEG = EARTH_RADIUS_M * math.pi / 180  # metres per degree of latitude
ID_KEYS = ("OBJECTID", "objectid", "FID", "id", "ID")  # property names a city layer keeps its point ids under
NAME = re.compile(r"[A-Za-z0-9_-]+")
VISIBLE = {"positive": "yes", "negative": "no", "unclear": "unclear"}
KEEP = ("sequence", "lat", "lng", "compass", "captured_at", "city_point", "distance_m", "angle_deg")


@dataclass(frozen=True)
class CityPoint:
    id: str
    lat: float
    lng: float


def parse_where(pairs: list[str]) -> dict[str, str]:
    """["IS_STREETLIGHT=YES", ...] -> {"IS_STREETLIGHT": "YES", ...}."""
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--where needs KEY=VALUE, got {pair!r}")
        out[key.strip()] = value.strip()
    return out


def _matches(props: dict, where: dict) -> bool:
    """Every where pair matches the property's value as text, exactly (so YES does not match yes)."""
    return all(k in props and str(props[k]) == v for k, v in where.items())


def _point_id(feature: dict, index: int) -> str:
    props = feature.get("properties") or {}
    for value in (feature.get("id"), *(props.get(k) for k in ID_KEYS)):
        if value not in (None, ""):
            return str(value)
    return f"#{index}"  # the feature's position in the layer


def layer_points(layer, where: dict | None = None) -> list[CityPoint]:
    """The Point features of a GeoJSON FeatureCollection (or a list of features) whose properties match every where
    pair. Other geometries are skipped."""
    features = layer.get("features", []) if isinstance(layer, dict) else layer
    out = []
    for i, f in enumerate(features):
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point" or not _matches(f.get("properties") or {}, where or {}):
            continue
        lng, lat = geom["coordinates"][:2]  # GeoJSON order is longitude, latitude
        out.append(CityPoint(_point_id(f, i), float(lat), float(lng)))
    return out


def bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Initial compass bearing from the first position to the second, 0-360 clockwise from north."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def turn_deg(heading: float, bearing: float) -> float:
    """Signed angle from the heading to the bearing, -180 to 180; positive is to the right."""
    return (bearing - heading + 180) % 360 - 180


def _grid(points: list[CityPoint], cell_deg: float) -> dict:
    grid = defaultdict(list)
    for p in points:
        grid[(math.floor(p.lat / cell_deg), math.floor(p.lng / cell_deg))].append(p)
    return grid


def _near(grid: dict, cell_deg: float, lat: float, lng: float, radius_m: float) -> list[tuple[float, CityPoint]]:
    """(distance, point) for every point within radius_m, nearest first. A cell is radius_m tall, so such a point is at
    most one row away and 1/cos(latitude) columns away; haversine then keeps the exact ones. A city layer has tens of
    thousands of points, too many to measure from every photo."""
    row, col = math.floor(lat / cell_deg), math.floor(lng / cell_deg)
    cols = math.ceil(1 / math.cos(math.radians(min(abs(lat) + cell_deg, 89.0))))
    found = []
    for r in range(row - 1, row + 2):
        for c in range(col - cols, col + cols + 1):
            for p in grid.get((r, c), ()):
                d = haversine_m(lat, lng, p.lat, p.lng)
                if d <= radius_m:
                    found.append((d, p))
    return sorted(found, key=lambda x: (x[0], x[1].id))


def _describe(point: CityPoint, dist: float, turn: float) -> str:
    side = "straight ahead" if abs(turn) < 0.5 else f"{abs(turn):.0f}° {'right' if turn > 0 else 'left'} of heading"
    return f"city point {point.id} at {dist:.1f} m, {side}"


def _check(min_m: float, max_m: float, pos_deg: float, neg_m: float, neg_deg: float) -> None:
    """The positive cone must sit inside the negative one, or a photo could be both."""
    if not (0 <= min_m <= max_m <= neg_m and neg_m > 0 and 0 <= pos_deg <= neg_deg <= 180):
        raise ValueError("need 0 <= min_m <= max_m <= neg_m, neg_m > 0 and 0 <= pos_deg <= neg_deg <= 180")


def _usable(photo: dict, on_foot_only: bool) -> bool:
    if photo.get("compass") is None or photo.get("lat") is None or photo.get("lng") is None:
        return False
    return photo.get("on_foot") is True or not on_foot_only  # on_foot null is unknown, so not on foot


def _label(photo: dict, near: list[tuple[float, CityPoint]], min_m: float, max_m: float, pos_deg: float,
           neg_m: float, neg_deg: float) -> dict:
    heading = float(photo["compass"]) % 360
    seen = [(d, turn_deg(heading, bearing_deg(photo["lat"], photo["lng"], p.lat, p.lng)), p) for d, p in near]
    ahead = [s for s in seen if abs(s[1]) <= neg_deg]  # near holds only points within neg_m
    hits = [s for s in ahead if min_m <= s[0] <= max_m and abs(s[1]) <= pos_deg]
    if hits:
        cls, (d, turn, p) = "positive", hits[0]
        note = _describe(p, d, turn)
    elif ahead:
        cls, (d, turn, p) = "unclear", ahead[0]
        note = f"{_describe(p, d, turn)}: outside {min_m:g}-{max_m:g} m or ±{pos_deg:g}°"
    else:
        cls, (d, turn, p) = "negative", (None, None, None)
        note = f"no city point within {neg_m:g} m inside ±{neg_deg:g}° of heading"
        if seen:
            note += f"; nearest is {_describe(seen[0][2], seen[0][0], seen[0][1])}"
    return {"class": cls, "note": note, "city_point": p.id if p else None,
            "distance_m": None if d is None else round(d, 1), "angle_deg": None if turn is None else round(turn, 1)}


def label_photos(photos: list[dict], points, *, min_m: float = DEFAULTS["min_m"], max_m: float = DEFAULTS["max_m"],
                 pos_deg: float = DEFAULTS["pos_deg"], neg_m: float = DEFAULTS["neg_m"],
                 neg_deg: float = DEFAULTS["neg_deg"], where: dict | None = None,
                 on_foot_only: bool = False) -> list[dict]:
    """Every usable photo (one with a position and a compass heading, and on foot if asked) with its class (positive,
    negative or unclear), a note for a person to audit, and the city point the class rests on. points is a GeoJSON
    FeatureCollection or its list of features."""
    _check(min_m, max_m, pos_deg, neg_m, neg_deg)
    cell = neg_m / M_PER_DEG
    grid = _grid(layer_points(points, where), cell)
    out = []
    for photo in photos:
        if not _usable(photo, on_foot_only):
            continue
        near = _near(grid, cell, photo["lat"], photo["lng"], neg_m)
        out.append({**photo, "id": str(photo["id"]), **_label(photo, near, min_m, max_m, pos_deg, neg_m, neg_deg)})
    return out


def _gap(photo: dict, taken: list[dict]) -> float:
    return min((haversine_m(photo["lat"], photo["lng"], t["lat"], t["lng"]) for t in taken), default=math.inf)


def _pick(pool: list[dict], k: int, taken: list[dict], spacing_m: float) -> list[dict]:
    """Up to k photos from pool, one sequence at a time in sequence id order, each time the sequence's photo farthest
    from every photo taken so far and never within spacing_m of one. Ties go to the earlier photo."""
    by_seq = defaultdict(list)
    for p in sorted(pool, key=lambda p: (str(p.get("captured_at") or ""), p["id"])):
        by_seq[str(p.get("sequence") or "")].append(p)
    taken, picked = list(taken), []
    while len(picked) < k:
        before = len(picked)
        for seq in sorted(by_seq):
            if len(picked) == k:
                break
            gaps = [_gap(p, taken) for p in by_seq[seq]]
            if not gaps or max(gaps) < spacing_m:
                continue
            p = by_seq[seq].pop(gaps.index(max(gaps)))  # index finds the first, so a tie goes to the earlier photo
            taken.append(p)
            picked.append(p)
        if len(picked) == before:
            break
    return picked


def sample(labelled: list[dict], n_pos: int, n_neg: int, n_unclear: int = 0,
           spacing_m: float = SPACING_M) -> list[dict]:
    """Up to n_pos positives, then n_neg negatives (and n_unclear unclear photos, none by default), spread across
    sequences and space. The spacing holds across classes: no two chosen photos are within spacing_m."""
    chosen = []
    for cls, k in (("positive", n_pos), ("negative", n_neg), ("unclear", n_unclear)):
        chosen += _pick([p for p in labelled if p["class"] == cls], k, chosen, spacing_m)
    return chosen


def build(name: str, target: str, photos: list[dict], points, *, source_layer: str = "the city layer",
          n_pos: int = 12, n_neg: int = 6, n_unclear: int = 0, **params) -> dict:
    """The test-set JSON that assist.testset.load reads. params are label_photos' keyword arguments."""
    if not NAME.fullmatch(name):
        raise ValueError(f"name {name!r}: use letters, digits, _ and - only")
    params = {**DEFAULTS, "where": {}, "on_foot_only": False, **params}
    labelled = label_photos(photos, points, **params)
    chosen = sample(labelled, n_pos, n_neg, n_unclear)
    found, picked = Counter(p["class"] for p in labelled), Counter(p["class"] for p in chosen)
    return {
        "name": name,
        "target": target,
        "labelled_by": f"auto from {source_layer} point positions (not checked by eye)",
        "source_layer": source_layer,
        "params": {**params, "n_pos": n_pos, "n_neg": n_neg, "n_unclear": n_unclear, "spacing_m": SPACING_M},
        "counts": {"photos": len(photos), "usable": len(labelled),
                   "layer_points": len(layer_points(points, params["where"])),
                   **{c: found[c] for c in VISIBLE}, **{f"chosen_{c}": picked[c] for c in VISIBLE}},
        "questions": {},  # an inventory item only checks whether the target is there
        "photos": [_entry(p) for p in chosen],
    }


def _entry(photo: dict) -> dict:
    """A chosen photo as assist.testset reads it, plus what a person needs to audit the label."""
    kept = {k: photo.get(k) for k in KEEP}
    kept["lat"], kept["lng"] = round(photo["lat"], 7), round(photo["lng"], 7)  # 7 decimals is about 1 cm
    return {"id": photo["id"], "target_visible": VISIBLE[photo["class"]], "answers": {}, "note": photo["note"],
            **kept}


def write(data: dict, out_dir: Path = FOLDER) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{data['name']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="test set name; written as <out>/<name>.json")
    ap.add_argument("--target", required=True, help='the asset as a short noun phrase, e.g. "streetlight pole"')
    ap.add_argument("--layer", required=True, help="the city's GeoJSON FeatureCollection of Point features")
    ap.add_argument("--photos", required=True, help="JSON list of photos with id, sequence, lat, lng, compass")
    ap.add_argument("--where", action="append", default=[], metavar="KEY=VALUE",
                    help="keep only layer points with this property value; repeat to require several")
    ap.add_argument("--n-pos", type=int, default=12)
    ap.add_argument("--n-neg", type=int, default=6)
    ap.add_argument("--n-unclear", type=int, default=0)
    ap.add_argument("--on-foot-only", action="store_true", help="skip photos not marked on_foot: true")
    for key, value in DEFAULTS.items():
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=value)
    ap.add_argument("--out", default=str(FOLDER))
    args = ap.parse_args(argv)
    layer = json.loads(Path(args.layer).read_text(encoding="utf-8"))
    photos = json.loads(Path(args.photos).read_text(encoding="utf-8"))
    try:
        data = build(args.name, args.target, photos, layer, source_layer=Path(args.layer).name, n_pos=args.n_pos,
                     n_neg=args.n_neg, n_unclear=args.n_unclear, where=parse_where(args.where),
                     on_foot_only=args.on_foot_only, **{k: getattr(args, k) for k in DEFAULTS})
    except ValueError as e:
        raise SystemExit(str(e))
    path = write(data, Path(args.out))
    c = data["counts"]
    print(f"photos: {c['photos']}, usable: {c['usable']}; layer points: {c['layer_points']}")
    print(f"labelled: {c['positive']} positive, {c['negative']} negative, {c['unclear']} unclear")
    print(f"chosen: {c['chosen_positive']} positive, {c['chosen_negative']} negative, {c['chosen_unclear']} unclear")
    print(f"written: {path}")
    return path


if __name__ == "__main__":
    main()
