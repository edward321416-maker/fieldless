"""Export a scan's photos, in idx order, for tools/survey_labeler.html.

    python -m eval.manifest --run plan1-live --scan 1

writes <data>/truth/manifest_<run>_scan<id>.json and prints its path."""
import argparse
import json
from pathlib import Path

from eval.store import load_scan, open_ro
from pipeline import config

FORMAT = "threshold-survey-manifest/1"


def build(run: str, scan_id: int, frames: list[dict]) -> dict:
    return {"format": FORMAT, "run": run, "scan_id": scan_id,
            "frames": [{"idx": f["idx"], "image_id": f["image_id"], "captured_at": f["captured_at"],
                        "creator": f["creator"]} for f in sorted(frames, key=lambda f: f["idx"])]}


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    args = ap.parse_args(argv)
    conn = open_ro(config.run_dir(args.run) / "threshold.db")
    try:
        data = build(args.run, args.scan, load_scan(conn, args.scan)["frames"])
    finally:
        conn.close()
    out = config.data_dir() / "truth" / f"manifest_{args.run}_scan{args.scan}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
    return out


if __name__ == "__main__":
    main()
