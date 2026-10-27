"""Rows -> API JSON (spec 5, 9, 15, 16). Display labels are made here, per request; they are never stored (spec 7.4
step 8). Every label is an icon plus text, so colour is never the only signal (spec 15), and the text states a fact:
never "violation", "illegal" or "accessible" (spec 9.4)."""
from datetime import datetime, timezone

from pipeline import geo, survey_items

OLD_PHOTO_YEARS = 3  # spec 9.3: photos older than this get an "old photo" badge
ISSUE_ICONS = {"missing_blocks": "✖", "cover_cut": "◼", "fixed_obstruction": "▮", "obstruction": "⛔"}
MAX_STEP_M = 50.0  # a longer jump between consecutive photos of one walk is a gap in coverage, not surveyed street
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
            "captured_at": evidence.get("captured_at"), "link": evidence["link"], "license": evidence["license"],
            "blurred": bool(evidence.get("blurred_regions"))}


def surveyed_km(frames: list[dict]) -> float:
    """Length of street the scan's photos cover: the path through each walk's photos in order (clip, idx)."""
    total, last = 0.0, {}
    for f in sorted(frames, key=lambda f: (f["clip"], f["idx"])):
        prev = last.get(f["clip"])
        if prev and None not in (prev["lat"], prev["lng"], f["lat"], f["lng"]):
            step = geo.haversine_m(prev["lat"], prev["lng"], f["lat"], f["lng"])
            if step <= MAX_STEP_M:
                total += step
        last[f["clip"]] = f
    return round(total / 1000, 3)


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


def standard_of(scan: dict) -> survey_items.Standard:
    """The scan's survey standard; scans made before standards existed are Korean."""
    return survey_items.load(scan.get("jurisdiction") or "KR")


def is_durable(row: dict, std: survey_items.Standard) -> bool:
    """A KR defect class or any check row: part of the street, likely the same today (spec 9.3)."""
    return row["issue_type"] in survey_items.KR_DURABLE or std.check(row["issue_type"]) is not None


def issue_label(row: dict, std: survey_items.Standard | None = None) -> dict:
    std = std or survey_items.load("KR")
    check = std.check(row["issue_type"])
    if check is not None:
        icon, text = check.label(row.get("object_label"))
        return {"icon": icon, "text": text}
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


def issue_order(row: dict, std: survey_items.Standard | None = None) -> tuple:
    """Defects first, then other durable rows (checked objects), then one-off obstructions; id order within."""
    std = std or survey_items.load("KR")
    return (not std.is_defect(row), not is_durable(row, std), row["id"])


def check_json(row: dict, std: survey_items.Standard) -> dict | None:
    check = std.check(row["issue_type"])
    if check is None:
        return None
    return {"id": check.id, "find": check.find, "question": check.ask, "answer": row.get("object_label") or "unclear",
            "report_when": check.report_when, "label_yes": check.label_yes, "label_no": check.label_no}


def second_json(row: dict) -> dict | None:
    """Spark Open's second opinion, when it answered (a failed call stores an error instead)."""
    so = (row.get("model_outputs") or {}).get("second_opinion") or {}
    return {"answer": so["answer"], "agree": so["agree"]} if "answer" in so else None


def entrance_json(row: dict, now: datetime | None = None) -> dict:
    return {"id": row["id"], "lat": row["lat"], "lng": row["lng"], "name": row.get("place_name") or "",
            "category": row.get("category") or "", "step_count": row.get("step_count"),
            "stairs_detected": row.get("stairs_detected"), "sill_possible": row.get("sill_possible"),
            "label": entrance_label(row), "review_status": row["review_status"],
            "review_reasons": row["review_reasons"], "evidence": evidence_json(row.get("evidence")),
            "captured_at": row.get("captured_at"), "old_photo": is_old(row.get("captured_at"), now)}


def issue_json(row: dict, now: datetime | None = None, std: survey_items.Standard | None = None) -> dict:
    std = std or survey_items.load("KR")
    return {"id": row["id"], "lat": row["lat"], "lng": row["lng"], "issue_type": row["issue_type"],
            "durable": is_durable(row, std), "defect": std.is_defect(row), "label": issue_label(row, std),
            "check": check_json(row, std), "second_opinion": second_json(row),
            "object_label": row.get("object_label"), "frame_start": row.get("frame_start"),
            "frame_end": row.get("frame_end"), "review_status": row["review_status"], "review_reasons": row["review_reasons"],
            "evidence": evidence_json(row.get("evidence")), "captured_at": row.get("captured_at"),
            "old_photo": is_old(row.get("captured_at"), now)}


def scan_json(scan: dict, entrances: list[dict], issues: list[dict], km: float | None = None) -> dict:
    std = standard_of(scan)
    live = [i for i in issues if i["review_status"] != "rejected"]
    defects = sum(std.is_defect(i) for i in live)
    obstructions = sum(i["issue_type"] == "obstruction" for i in live)
    needs_review = sum(r["review_status"] == "needs_review" for r in [*entrances, *live])
    with_steps = sum(entrance_label(e)["icon"] == "▲" for e in entrances)  # a possible sill is not counted
    checks = [{"id": c.id, "find": c.find, "label_yes": c.label_yes, "label_no": c.label_no,
               **{a: sum(i["issue_type"] == c.id and (i.get("object_label") or "unclear") == a for i in live)
                  for a in ("yes", "no", "unclear")}} for c in std.checks]
    return {**{k: scan[k] for k in SCAN_FIELDS},
            "standard": std.code, "checks": checks,
            "km": km,
            "usd_per_km": round(scan["cost_usd"] / km, 2) if km else None,
            "counts": {"defects": defects, "obstructions": obstructions, "entrances": len(entrances),
                       "entrances_with_steps": with_steps, "needs_review": needs_review}}
