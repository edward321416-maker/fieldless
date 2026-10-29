"""The city-record check a demo survey shows in the app (docs/specs/2026-10-28-ui-redesign-design.md, section 4.1):
for each item with a city layer, how many of the photographed city points the survey found, which findings were
paired with one, and which rejections looked straight at an unfound one. eval/city.py does the pairing.

    python -m eval.city_check --run demo --scan 4

Writes <run>/city_check_scan<N>.json; the server attaches it to the scan as "city_check" when it is there. Each item
also names the city records its findings paired with and the photographed ones it missed, in the fields the city's
file lists (eval/cities/<city>.toml), so assist.export can write them (docs/specs/2026-10-29-city-fields-design.md).
"""
import argparse
import json
import tomllib
from datetime import date
from pathlib import Path

from eval import city
from eval.store import load_scan, open_ro

METHOD = "Rows paired one-to-one with the city's points by photo position and viewing direction (eval/city.py)."
METHOD_URL = "https://github.com/edward321416-maker/fieldless/blob/main/eval/city.py"
CITIES = Path(__file__).parent / "cities"


def load_city(name: str) -> dict:
    """A city's file: its name, the folder of its layers and, per survey item, the layer of its own records and the
    fields that name one."""
    return tomllib.loads((CITIES / f"{name}.toml").read_text(encoding="utf-8"))


def _singular(text: str) -> str:
    words = text.lower().split()
    if words and words[-1].endswith("s"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def extra_layer(city: dict, target: str) -> dict | None:
    """The city's layer for an asset that is not a survey item yet ([extra] in the city's file), if the target names
    it: compared lower case and singular, so "Bike Racks" finds "bike rack"."""
    want = _singular(target.strip())
    if not want:
        return None
    for key, cfg in (city.get("extra") or {}).items():
        if want in {_singular(n) for n in cfg["names"]}:
            return {"id": key, "layer": cfg["layer"], "name": cfg["name"], "id_fields": list(cfg["id_fields"]),
                    "root": city["root"]}
    return None


SEATTLE = load_city("seattle")["items"]  # the layers data/seattle/city_eval_all.sh scores, relative to data/seattle


def build(data: dict, layers: dict[str, dict], root: Path, measured_at: str, city_name: str = "Seattle") -> dict:
    """data as eval.store.load_scan returns it; layers maps item ids to their layer settings (SEATTLE's shape)."""
    items = {}
    for item, cfg in layers.items():
        layer = json.loads((Path(root) / cfg["layer"]).read_text(encoding="utf-8"))
        r = city.evaluate(data["issues"], data["frames"], layer, item, inventory=cfg["inventory"],
                          installed_field=cfg.get("installed_field"), truth_field=cfg.get("truth_field"),
                          truth_yes=cfg.get("truth_yes"), id_field="OBJECTID")
        points = {p["id"]: p for p in city.load_points(layer, "OBJECTID")}
        fields = list(cfg.get("id_fields", []))

        def record(pid):
            return {k: points[pid]["props"].get(k) for k in fields}

        items[item] = {"layer": cfg["name"], "found": r["found"], "covered": r["covered"],
                       "matched_ids": sorted(m["issue_id"] for m in r["matched"]),
                       "rejected_at_city_point": sorted(d["issue_id"] for d in r["rejected_at_city_point"]),
                       "id_fields": fields,
                       "pairs": [{"issue_id": m["issue_id"], "in_view": m["in_view"], "dist_m": m["dist_m"],
                                  "city": record(m["point_id"])} for m in r["matched"]],
                       "missed": [{"lat": points[pid]["lat"], "lng": points[pid]["lng"], "city": record(pid)}
                                  for pid in r["missed_covered"]]}
        if cfg.get("truth_field") and cfg.get("truth_yes") and cfg.get("truth_no"):
            # the city keeps the survey's own question as a field: the export writes the answer in it
            items[item]["answer_field"] = {"name": cfg["truth_field"], "yes": cfg["truth_yes"], "no": cfg["truth_no"]}
    waiting = any(r.get("review_status") == "needs_review" for r in [*data["issues"], *data["entrances"]])
    return {"scan_id": data["scan"]["id"], "city": city_name, "measured_at": measured_at, "reviewed": not waiting,
            "method": METHOD, "method_url": METHOD_URL, "items": items}


def main(argv=None) -> Path:
    from eval.glimmer_ledger import LEDGER
    from pipeline import config

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--city", default="seattle", help="a file in eval/cities, without .toml")
    ap.add_argument("--root", help="folder of the city layers (default: the city file's root, next to the ledger)")
    ap.add_argument("--measured-at", default=date.today().isoformat())
    args = ap.parse_args(argv)
    town = load_city(args.city)
    run_dir = config.run_dir(args.run)
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, args.scan)
    finally:
        conn.close()
    out = build(data, town["items"], Path(args.root or LEDGER.parent / town["root"]), args.measured_at, town["city"])
    path = run_dir / f"city_check_scan{args.scan}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for item, r in out["items"].items():
        print(f"{item}: {r['found']} of {r['covered']} (rejected at a city point: {len(r['rejected_at_city_point'])})")
    print(f"written: {path}")
    return path


if __name__ == "__main__":
    main()
