"""Evaluate one scan against a survey truth file and print the numbers for docs/metrics.md.

    python -m eval.run_eval --run plan1-live --scan 1 --truth <data>/truth/survey_truth_plan1-live_scan1.json

Writes <data>/runs/<run>/eval_scan<id>.json."""
import argparse
import json
from pathlib import Path

from eval.metrics import DEFECTS, evaluate
from eval.store import load_scan, open_ro
from eval.truth import load_truth
from pipeline import config


def to_markdown(r: dict) -> str:
    lines = [f"Scan {r['scan_id']}", "", "| Class | Truth spots | Found | Predicted | Hits |", "|---|---|---|---|---|"]
    for kind in (*DEFECTS, "obstruction"):
        m = r["defects"][kind] if kind in DEFECTS else r["obstructions"]
        lines.append(f"| {kind} | {m['truth']} | {m['found']} | {m['predicted']} | {m['hits']} |")
    e, v, s = r["entrances"], r["review"], r["speed"]
    lines += ["",
              f"- Entrances: truth {e['truth']}, predicted {e['predicted']}, matched {e['matched']}, "
              f"steps exact {e['steps_exact']}, within one {e['steps_within_one']}, sill agree {e['sill_agree']}",
              f"- Review: {v['flagged']} of {v['items']} items flagged ({v['flagged_share']}), median "
              f"{v['review_seconds_median']} s per action, defects confirmed {v['defects_verified']}, "
              f"rejected {v['defects_rejected']}",
              f"- Person labeling: {s['human_seconds']} s for {s['photos']} photos, {s['km']} km "
              f"({s['human_seconds_per_km']} s/km)",
              f"- Threshold: {s['processing_seconds']:.0f} s processing + {s['review_seconds']:.0f} s review "
              f"({s['threshold_seconds_per_km']} s/km), ${s['cost_usd']:.2f} (${s['usd_per_km']}/km)"
              + ("" if s["reviewed"] else " - not reviewed yet")]
    for kind in DEFECTS:
        m = r["defects"][kind]
        if m["false"] or m["missed"]:
            lines.append(f"- {kind}: false {m['false']}, missed {m['missed']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--truth", required=True)
    args = ap.parse_args(argv)
    run_dir = config.run_dir(args.run)
    conn = open_ro(run_dir / "threshold.db")
    try:
        result = evaluate(load_scan(conn, args.scan), load_truth(Path(args.truth)))
    finally:
        conn.close()
    out = run_dir / f"eval_scan{args.scan}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(to_markdown(result))
    return out


if __name__ == "__main__":
    main()
