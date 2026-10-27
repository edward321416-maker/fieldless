"""Load a survey truth file exported by tools/survey_labeler.html (format threshold-survey-truth/1)."""
import json
from pathlib import Path

FORMAT = "threshold-survey-truth/1"
DEFECTS = ("missing_blocks", "cover_cut", "fixed_obstruction")


def parse_truth(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} file")
    if not isinstance(data.get("run"), str) or not isinstance(data.get("scan_id"), int):
        raise ValueError("run and scan_id are required")
    seconds = data.get("labeling_seconds")
    if not isinstance(seconds, int) or seconds < 0:
        raise ValueError("labeling_seconds must be a non-negative integer")
    photos = {}
    for image_id, p in (data.get("photos") or {}).items():
        if not isinstance(p, dict) or not isinstance(p.get("idx"), int):
            raise ValueError(f"photo {image_id}: idx is required")
        defects = list(p.get("defects") or [])
        if any(d.get("type") not in DEFECTS for d in defects):
            raise ValueError(f"photo {image_id}: unknown defect type")
        photos[str(image_id)] = {"idx": p["idx"], "defects": defects, "obstructions": list(p.get("obstructions") or []),
                                 "entrances": list(p.get("entrances") or [])}
    return {"run": data["run"], "scan_id": data["scan_id"], "labeled_by": data.get("labeled_by", ""),
            "labeling_seconds": seconds, "photos": photos}


def load_truth(path: Path) -> dict:
    return parse_truth(json.loads(Path(path).read_text(encoding="utf-8")))
