"""Rows -> API JSON (spec 5, 9, 15, 16). Display labels are made here, per request; they are never stored (spec 7.4
step 8). Every label is an icon plus text, so colour is never the only signal (spec 15), and the text states a fact:
never "violation", "illegal" or "accessible" (spec 9.4)."""
from datetime import datetime, timezone

OLD_PHOTO_YEARS = 3  # spec 9.3: photos older than this get an "old photo" badge
DURABLE = ("missing_blocks", "cover_cut", "fixed_obstruction")  # spec 9.3: the three classes the user chose
ISSUE_ICONS = {"missing_blocks": "✖", "cover_cut": "◼", "fixed_obstruction": "▮", "obstruction": "⛔"}
SCAN_FIELDS = ("id", "status", "stage_status", "bbox", "image_count", "captured_from", "captured_to", "cost_usd",
               "processing_seconds", "review_seconds", "error")


def is_old(captured_at: str | None, now: datetime | None = None) -> bool:
    if not captured_at:
        return False
    taken = datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - taken).days > 365 * OLD_PHOTO_YEARS


def evidence_json(evidence: dict | None) -> dict | None:
    """The blurred evidence photo with its Mapillary attribution, or None if no photo was stored."""
    if not evidence or "image" not in evidence:
        return None
    return {"url": f"/api/evidence/{evidence['image']}", "creator": evidence.get("creator"),
            "captured_at": evidence.get("captured_at"), "link": evidence["link"], "license": evidence["license"]}


def entrance_label(row: dict) -> dict:
    """spec 9.1: no steps, possible sill, or n steps. A sill seen only by Spark stays "possible" until reviewed."""
    count = row.get("step_count")
    if count is None:
        return {"icon": "?", "text": "Not analysed"}
    if row["review_status"] == "needs_review" and "possible_sill" in (row.get("review_reasons") or []):
        return {"icon": "?", "text": "Possible sill"}
    if count == 0:
        return {"icon": "✓", "text": "No steps"}
    return {"icon": "▲", "text": "1 step" if count == 1 else f"{count} steps"}


def issue_label(row: dict) -> dict:
    kind, what = row["issue_type"], row.get("object_label")
    if kind == "missing_blocks":
        text = "Tactile blocks missing"
    elif kind == "cover_cut":
        text = "Tactile line cut by a cover"
    elif kind == "fixed_obstruction":
        text = f"Fixed object on tactile paving: {what or 'object'}"
    else:
        text = f"Blocked: {what or 'obstacle'}"
    return {"icon": ISSUE_ICONS.get(kind, "⛔"), "text": text}


def issue_order(row: dict) -> tuple:
    """Durable defects first (spec 7.3), then one-off obstructions, each in id order."""
    return (row["issue_type"] not in DURABLE, row["id"])


def entrance_json(row: dict, now: datetime | None = None) -> dict:
    return {"id": row["id"], "lat": row["lat"], "lng": row["lng"], "name": row.get("place_name") or "",
            "category": row.get("category") or "", "step_count": row.get("step_count"),
            "stairs_detected": row.get("stairs_detected"), "sill_possible": row.get("sill_possible"),
            "label": entrance_label(row), "review_status": row["review_status"],
            "review_reasons": row["review_reasons"], "evidence": evidence_json(row.get("evidence")),
            "captured_at": row.get("captured_at"), "old_photo": is_old(row.get("captured_at"), now)}


def issue_json(row: dict, now: datetime | None = None) -> dict:
    return {"id": row["id"], "lat": row["lat"], "lng": row["lng"], "issue_type": row["issue_type"],
            "durable": row["issue_type"] in DURABLE, "label": issue_label(row),
            "object_label": row.get("object_label"), "frame_start": row.get("frame_start"),
            "frame_end": row.get("frame_end"), "review_status": row["review_status"], "review_reasons": row["review_reasons"],
            "evidence": evidence_json(row.get("evidence")), "captured_at": row.get("captured_at"),
            "old_photo": is_old(row.get("captured_at"), now)}


def scan_json(scan: dict, entrances: list[dict], issues: list[dict], hand_seconds: float | None = None) -> dict:
    live = [i for i in issues if i["review_status"] != "rejected"]
    defects = sum(i["issue_type"] in DURABLE for i in live)
    needs_review = sum(r["review_status"] == "needs_review" for r in [*entrances, *live])
    with_steps = sum(entrance_label(e)["icon"] == "▲" for e in entrances)  # a possible sill is not counted
    return {**{k: scan[k] for k in SCAN_FIELDS},
            "hand_seconds": hand_seconds,
            "counts": {"defects": defects, "obstructions": len(live) - defects, "entrances": len(entrances),
                       "entrances_with_steps": with_steps, "needs_review": needs_review}}
