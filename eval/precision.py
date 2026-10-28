"""Are the findings a survey recorded on its own right? Judged by eye on each evidence photo (docs/metrics.md,
"모델이 스스로 기록한 결과의 맞은 비율"):

    python -m eval.precision sheets --run demo --scan 6 [--carry <run>/precision_scan4.json --carry-scan 4]
    python -m eval.precision summary --run demo --scan 6

`sheets` draws every finding no person has reviewed (status auto) as its evidence photo with the model's box beside
a close crop, a few to a sheet per item, into <run>/precision_scan<N>/, and writes <run>/precision_scan<N>.json with
an empty value per finding to fill in: yes (right), no (wrong) or unclear (the photo cannot tell), and dup_of for a
second finding of one object. With --carry, a finding of the same item on the same photo with an overlapping box (an
entrance: the same photo and name) keeps the earlier judgement, so a re-run scan only needs its new findings looked
at. `summary` counts the values per item as a Markdown table.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from eval.store import load_scan, open_ro

VALUES = ("yes", "no", "unclear")
MIN_IOU = 0.5
FULL_W, ROW_H, CROP_W, TITLE_H = 560, 315, 420, 26


def targets(data: dict) -> list[dict]:
    """The findings to judge, issues first: {"kind", "id", "item", "image_id", "image", "box" (0-1000 or None),
    "title"}."""
    out = []
    for i in data["issues"]:
        if i["review_status"] == "auto":
            check = (i.get("model_outputs") or {}).get("check") or {}
            out.append({"kind": "issues", "id": i["id"], "item": i["issue_type"],
                        "image_id": i["evidence"]["image_id"], "image": i["evidence"]["image"],
                        "box": check.get("box") or None, "title": f"#{i['id']} {i['issue_type']}"})
    for e in data["entrances"]:
        if e["review_status"] == "auto":
            out.append({"kind": "entrances", "id": e["id"], "item": "entrance", "image_id": e["evidence"]["image_id"],
                        "image": e["evidence"]["image"], "box": None, "name": e.get("place_name"),
                        "title": f"entrance #{e['id']} {e.get('place_name')} steps={e.get('step_count')}"})
    return out


def iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def _same(old: dict, new: dict) -> bool:
    if (old["kind"], old["item"], old["image_id"]) != (new["kind"], new["item"], new["image_id"]):
        return False
    if new["kind"] == "entrances":
        return old.get("name") == new.get("name")
    return bool(old["box"] and new["box"]) and iou(old["box"], new["box"]) >= MIN_IOU


def blank(data: dict) -> dict:
    out = {"scan_id": data["scan"]["id"], "issues": {}, "entrances": {}}
    for t in targets(data):
        out[t["kind"]][str(t["id"])] = {"item": t["item"], "value": ""}
    return out


def carry(labels: dict, old_data: dict, new_data: dict, scan_id: int | None = None) -> dict:
    """blank(new_data) with the judgements in labels (made on old_data) copied to the same objects."""
    out = blank(new_data)
    if scan_id is not None:
        out["scan_id"] = scan_id
    old = targets(old_data)
    moved = {}  # (kind, old id) -> new id
    for t in targets(new_data):
        for o in old:
            judged = labels.get(o["kind"], {}).get(str(o["id"]))
            if judged and judged.get("value") and _same(o, t):
                moved[(o["kind"], o["id"])] = t["id"]
                entry = {k: v for k, v in judged.items() if k != "dup_of"}
                out[t["kind"]][str(t["id"])] = {**entry, "item": t["item"], "carried_from": o["id"]}
                break
    for kind in ("issues", "entrances"):  # a repeat stays a repeat when the object it repeats was carried too
        for entry in out[kind].values():
            first = labels[kind][str(entry["carried_from"])].get("dup_of") if "carried_from" in entry else None
            if first is not None and (kind, first) in moved:
                entry["dup_of"] = moved[(kind, first)]
    return out


def summary(labels: dict) -> list[dict]:
    rows: dict[str, dict] = {}
    entries = [*labels.get("issues", {}).values(), *({"item": "entrance", **e} for e in
                                                     labels.get("entrances", {}).values())]
    for e in entries:
        r = rows.setdefault(e["item"], {"item": e["item"], "n": 0, "yes": 0, "no": 0, "unclear": 0, "dup": 0,
                                        "open": 0})
        r["n"] += 1
        r[e["value"] if e.get("value") in VALUES else "open"] += 1
        r["dup"] += 1 if e.get("dup_of") is not None else 0
    total = {"item": "all", **{k: sum(r[k] for r in rows.values()) for k in ("n", "yes", "no", "unclear", "dup",
                                                                              "open")}}
    return [*rows.values(), total]


def markdown(rows: list[dict]) -> str:
    lines = ["| item | findings | right | wrong | unclear | repeats | not judged |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        cells = [r["item"], *(str(r[k]) for k in ("n", "yes", "no", "unclear", "dup", "open"))]
        if r["item"] == "all":
            cells = [f"**{c}**" for c in cells]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _row(path: Path, box, title: str) -> np.ndarray:
    img = cv2.imread(str(path))
    tile = np.full((ROW_H + TITLE_H, FULL_W + CROP_W, 3), 255, np.uint8)
    if img is None:
        cv2.putText(tile, f"{title}: no evidence photo", (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        return tile
    h, w = img.shape[:2]
    if box:
        x1, y1, x2, y2 = (int(box[0] * w / 1000), int(box[1] * h / 1000), int(box[2] * w / 1000),
                          int(box[3] * h / 1000))
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), max(2, w // 250))
        mx, my = max(120, int((x2 - x1) * 0.8)), max(120, int((y2 - y1) * 0.5))
        crop = img[max(0, y1 - my):min(h, y2 + my), max(0, x1 - mx):min(w, x2 + mx)]
        scale = min((CROP_W - 10) / crop.shape[1], ROW_H / crop.shape[0])
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale))))
        tile[TITLE_H:TITLE_H + crop.shape[0], FULL_W + 8:FULL_W + 8 + crop.shape[1]] = crop
    tile[TITLE_H:, :FULL_W] = cv2.resize(img, (FULL_W, ROW_H))
    cv2.putText(tile, title, (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    return tile


def sheets(data: dict, evidence_dir: Path, out_dir: Path, per_sheet: int = 4) -> dict:
    """Draws the sheets into out_dir and returns blank(data)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_item: dict[str, list[dict]] = {}
    for t in targets(data):
        by_item.setdefault(t["item"], []).append(t)
    for item, ts in by_item.items():
        for n in range(0, len(ts), per_sheet):
            rows = [_row(Path(evidence_dir) / t["image"], t["box"], t["title"]) for t in ts[n:n + per_sheet]]
            cv2.imwrite(str(out_dir / f"{item}_{n // per_sheet + 1:02d}.jpg"), np.vstack(rows),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])
    return blank(data)


def write_labels(labels: dict, path: Path) -> None:
    """Never overwrites a file that already holds judgements."""
    path = Path(path)
    if path.exists():
        raise SystemExit(f"{path} already exists: judge in it, or move it away first")
    path.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None) -> None:
    from pipeline import config

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("sheets", "summary"))
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--carry", help="a judged precision_scan<M>.json to copy judgements from")
    ap.add_argument("--carry-scan", type=int, help="the scan those judgements were made on (default: its scan_id)")
    ap.add_argument("--carry-run", help="the run that scan is in (default: --run)")
    args = ap.parse_args(argv)
    run_dir = config.run_dir(args.run)
    labels_path = run_dir / f"precision_scan{args.scan}.json"
    if args.command == "summary":
        print(markdown(summary(json.loads(labels_path.read_text(encoding="utf-8")))), end="")
        return
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, args.scan)
    finally:
        conn.close()
    labels = sheets(data, run_dir / "evidence", run_dir / f"precision_scan{args.scan}")
    if args.carry:
        judged = json.loads(Path(args.carry).read_text(encoding="utf-8"))
        old_dir = config.run_dir(args.carry_run) if args.carry_run else run_dir
        conn = open_ro(old_dir / "threshold.db")
        try:
            old = load_scan(conn, args.carry_scan or judged["scan_id"])
        finally:
            conn.close()
        labels = carry(judged, old, data, scan_id=args.scan)
    write_labels(labels, labels_path)
    rows = summary(labels)
    print(f"sheets: {run_dir / f'precision_scan{args.scan}'}")
    print(f"to judge: {rows[-1]['open']} of {rows[-1]['n']} (in {labels_path})")


if __name__ == "__main__":
    main()
