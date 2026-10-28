"""The city-record check a demo survey shows in the app (docs/specs/2026-10-28-ui-redesign-design.md, section 4.1):
for each item with a city layer, how many of the photographed city points the survey found, which findings were
paired with one, and which rejections looked straight at an unfound one. eval/city.py does the pairing.

    python -m eval.city_check --run demo --scan 4

Writes <run>/city_check_scan<N>.json; the server attaches it to the scan as "city_check" when it is there.
"""
import argparse
import json
from datetime import date
from pathlib import Path

from eval import city
from eval.store import load_scan, open_ro

METHOD = "Rows paired one-to-one with the city's points by photo position and viewing direction (eval/city.py)."
METHOD_URL = "https://github.com/edward321416-maker/fieldless/blob/main/eval/city.py"
SEATTLE = {  # the layers data/seattle/city_eval_all.sh scores, relative to data/seattle
    "utility_pole": {"layer": "assets/poles.geojson", "name": "Seattle City Light poles", "inventory": True},
    "street_tree": {"layer": "assets/trees.geojson", "name": "SDOT street trees", "inventory": True,
                    "installed_field": "PLANTED_DATE"},
    "litter_can": {"layer": "assets/litter.geojson", "name": "SPU public litter cans", "inventory": True},
    "parking_pay_station": {"layer": "assets/paystations.geojson", "name": "SDOT pay stations", "inventory": True},
    "curb_ramp_warning": {"layer": "ramps_capitolhill.geojson", "name": "SDOT curb ramps", "inventory": False,
                          "installed_field": "INSTALL_DATE", "truth_field": "DETWARN_EXISTS", "truth_yes": "Y"},
}


def build(data: dict, layers: dict[str, dict], root: Path, measured_at: str, city_name: str = "Seattle") -> dict:
    """data as eval.store.load_scan returns it; layers maps item ids to their layer settings (SEATTLE's shape)."""
    items = {}
    for item, cfg in layers.items():
        layer = json.loads((Path(root) / cfg["layer"]).read_text(encoding="utf-8"))
        r = city.evaluate(data["issues"], data["frames"], layer, item, inventory=cfg["inventory"],
                          installed_field=cfg.get("installed_field"), truth_field=cfg.get("truth_field"),
                          truth_yes=cfg.get("truth_yes"), id_field="OBJECTID")
        items[item] = {"layer": cfg["name"], "found": r["found"], "covered": r["covered"],
                       "matched_ids": sorted(m["issue_id"] for m in r["matched"]),
                       "rejected_at_city_point": sorted(d["issue_id"] for d in r["rejected_at_city_point"])}
    waiting = any(r.get("review_status") == "needs_review" for r in [*data["issues"], *data["entrances"]])
    return {"scan_id": data["scan"]["id"], "city": city_name, "measured_at": measured_at, "reviewed": not waiting,
            "method": METHOD, "method_url": METHOD_URL, "items": items}


def main(argv=None) -> Path:
    from eval.glimmer_ledger import LEDGER
    from pipeline import config

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--root", default=str(LEDGER.parent / "seattle"), help="folder of the city layers")
    ap.add_argument("--measured-at", default=date.today().isoformat())
    args = ap.parse_args(argv)
    run_dir = config.run_dir(args.run)
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, args.scan)
    finally:
        conn.close()
    out = build(data, SEATTLE, Path(args.root), args.measured_at)
    path = run_dir / f"city_check_scan{args.scan}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for item, r in out["items"].items():
        print(f"{item}: {r['found']} of {r['covered']} (rejected at a city point: {len(r['rejected_at_city_point'])})")
    print(f"written: {path}")
    return path


if __name__ == "__main__":
    main()
