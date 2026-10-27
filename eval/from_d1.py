"""Convert D1's street_b labels (Claude, untimed) into a survey truth for a dry run on a scan of the same photos.

    python -m eval.from_d1 --run plan1-live --scan 1 --clips <threshold>/spike/clips --labels <threshold>/spike/s7_labels.json
"""
import argparse
import json
from pathlib import Path

from eval.store import load_scan, open_ro
from eval.truth import FORMAT, parse_truth
from pipeline import config

S7_CLASS = {"63": "missing_blocks", "64": "missing_blocks", "81": "cover_cut", "82": "cover_cut"}


def convert(d1_truth: dict, frames_json: dict, s7_labels: dict, run: str, scan_id: int, scan_image_ids: set[str]) -> dict:
    ids = [f["id"] for f in frames_json["frames"]]
    clip = d1_truth["clips"]["street_b.mp4"]
    photos: dict[str, dict] = {}

    def at(t: int) -> dict | None:
        image_id = ids[t]
        if image_id not in scan_image_ids:
            return None
        return photos.setdefault(image_id, {"idx": t, "defects": [], "obstructions": [], "entrances": []})

    for t, kind in S7_CLASS.items():
        if t in s7_labels["structural"] and (p := at(int(t))) is not None:
            p["defects"].append({"type": kind})
    for o in clip.get("obstacles", []):
        if o.get("on_tactile"):
            for t in range(o["t_start"], o["t_end"] + 1):
                if (p := at(t)) is not None:
                    p["obstructions"].append({"label": o["label"]})
    for e in clip.get("entrances", []):
        if (p := at(e["t"])) is not None:
            p["entrances"].append({"steps": e["step_count"], "sill_unsure": False})
    return {"format": FORMAT, "run": run, "scan_id": scan_id, "labeled_by": "Claude (D1 labels, untimed)",
            "labeling_seconds": 0, "photos": photos}


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--clips", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    args = ap.parse_args(argv)
    conn = open_ro(config.run_dir(args.run) / "threshold.db")
    try:
        scan_ids = {f["image_id"] for f in load_scan(conn, args.scan)["frames"]}
    finally:
        conn.close()
    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    truth = convert(read(args.clips / "truth.json"), read(args.clips / "street_b" / "frames.json"),
                    read(args.labels), args.run, args.scan, scan_ids)
    parse_truth(truth)  # same validation as a labeler export
    out = config.data_dir() / "truth" / f"survey_truth_d1claude_scan{args.scan}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
    return out


if __name__ == "__main__":
    main()
