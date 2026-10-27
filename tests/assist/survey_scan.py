"""A small scan for the export and report tests: six photos on one walk, a defect-type check, an inventory check, a
row another stage writes, an entrance and a rejected row. Built with pipeline.db, read back with eval.store."""
from dataclasses import dataclass

from eval import store
from pipeline import config, db
from pipeline.survey_items import Check, PathSpec, Standard


@dataclass(frozen=True)
class InventoryCheck(Check):
    """An inventory item (`inventory = true` in a standard's TOML, plan 4 Task 7): a "yes" is an asset point and
    there is no defect answer. pipeline.survey_items does not read the flag yet; the export reads it by name."""
    inventory: bool = True


def inventory(item_id: str, target: str) -> InventoryCheck:
    name = target[:1].upper() + target[1:]
    return InventoryCheck(id=item_id, find=target, target=target, ask=f"Is this a {target}?", report_when="",
                          label_yes=name, label_no=f"Not a {target}", label_unclear=f"{name}: unclear from the photo")


RAMP = Check(id="curb_ramp_warning", find="curb cut", target="curb ramp", ask="Does it have a detectable warning?",
             report_when="no", label_yes="Curb ramp with detectable warning",
             label_no="Curb ramp without detectable warning", label_unclear="Curb ramp: detectable warning unclear")
POLE = inventory("streetlight_pole", "streetlight pole")
CAN = inventory("litter_can", "litter can")  # in the standard, never found in this scan
STANDARD = Standard(code="US", path=PathSpec(surface="sidewalk", name="the sidewalk", describe="sidewalks",
                                             objects=(), fixed=()),
                    checks=(RAMP, POLE, CAN), tactile_defects=False)
CREATORS = ("alice", "bob", "carol", "dave", "erin", "frank")


def _frame(i: int) -> dict:
    taken = "2021-03-01T10:00:00Z" if i == 5 else f"2025-06-0{i + 1}T10:00:00Z"
    return {"idx": i, "clip": 0, "image_id": f"10{i}", "sequence_id": "s", "lat": round(47.6 + i * 1e-4, 6),
            "lng": -122.3, "compass": 0.0, "captured_at": taken, "creator": CREATORS[i], "width": 2048,
            "height": 1536}


def _issue(issue_type: str, answer: str | None, idx: int, status: str, **extra) -> dict:
    return {"issue_type": issue_type, "object_label": answer, "frame_start": idx, "frame_end": idx,
            "frame_idx": idx, "lat": round(47.6 + idx * 1e-4, 6), "lng": -122.3, "review_status": status,
            "captured_at": _frame(idx)["captured_at"], **extra}


def build(run: str = "r") -> int:
    """Write the scan into <run>/threshold.db and return its id."""
    conn = db.connect(config.run_dir(run) / "threshold.db")
    scan_id = db.create_scan(conn, "test", [-122.31, 47.59, -122.29, 47.61], jurisdiction="US")
    db.add_frames(conn, scan_id, [_frame(i) for i in range(6)])
    evidence = {"image": f"scan{scan_id}_issue1.jpg", "image_id": "101", "creator": "alice",
                "captured_at": _frame(1)["captured_at"], "link": "https://www.mapillary.com/app/?pKey=101",
                "license": "CC BY-SA 4.0", "blurred_regions": 0}
    rows = [_issue("curb_ramp_warning", "no", 1, "verified", evidence=evidence),  # confirmed defect
            _issue("curb_ramp_warning", "yes", 2, "auto"),
            _issue("curb_ramp_warning", "no", 3, "needs_review", review_reasons=["possible_defect"]),
            _issue("curb_ramp_warning", "no", 4, "rejected"),  # a person said the models were wrong
            _issue("streetlight_pole", "yes", 2, "auto"),
            _issue("streetlight_pole", "unclear", 5, "needs_review", review_reasons=["unclear"]),
            _issue("obstruction", "traffic cone", 0, "auto")]
    for row in rows:
        db.add_issue(conn, scan_id, row)
    db.add_entrance(conn, scan_id, {"frame_idx": 3, "place_name": "Cafe", "lat": 47.6003, "lng": -122.2999,
                                    "step_count": 2, "review_status": "verified"})
    db.update_scan(conn, scan_id, cost_usd=0.4567, processing_seconds=95.4, review_seconds=30.2, image_count=6)
    conn.close()
    return scan_id


def load(run: str = "r", scan_id: int | None = None) -> dict:
    scan_id = scan_id or build(run)
    conn = store.open_ro(config.run_dir(run) / "threshold.db")
    try:
        return store.load_scan(conn, scan_id)
    finally:
        conn.close()
