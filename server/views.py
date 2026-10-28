"""Rows -> API JSON (spec 5, 9, 15, 16). Display labels are made here, per request; they are never stored (spec 7.4
step 8). Every label is an icon plus text, so colour is never the only signal (spec 15), and the text states a fact:
never "violation", "illegal" or "accessible" (spec 9.4)."""
from datetime import datetime, timezone

from pipeline import config, geo, second_opinion, survey_items

OLD_PHOTO_YEARS = 3  # spec 9.3: photos older than this get an "old photo" badge
ISSUE_ICONS = {"missing_blocks": "✖", "cover_cut": "◼", "fixed_obstruction": "▮", "obstruction": "⛔"}
MAX_STEP_M = 50.0  # a longer jump between consecutive photos of one walk is a gap in coverage, not surveyed street
SCAN_FIELDS = ("id", "status", "stage_status", "bbox", "image_count", "captured_from", "captured_to", "cost_usd",
               "processing_seconds", "review_seconds", "error")
# What the pipeline's stages find besides a standard's checks, described like a check for the map and its legend, so
# the web app names no survey item itself (docs/ui-rules.md U2). KR tactile defects come first, as in the standard.
STAGE_ITEMS = {
    "missing_blocks": {"target": "missing tactile blocks", "map_icon": "tiles"},
    "cover_cut": {"target": "tactile line cut by a cover", "map_icon": "cover"},
    "fixed_obstruction": {"target": "fixed object on tactile paving", "map_icon": "block"},
    "obstruction": {"target": "object in the walking path", "map_icon": "cone"},
    "entrance": {"target": "shop entrance", "map_icon": "door"},
}


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
    if row["review_status"] in ("needs_review", "field_check") and "possible_sill" in (row.get("review_reasons") or []):
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
    elif kind == "obstruction":
        text = f"Blocked: {what or 'obstacle'}"
    else:  # a check of a standard that has since changed: say what the row is, not that something blocks
        return {"icon": "•", "text": kind.replace("_", " ")}
    return {"icon": ISSUE_ICONS[kind], "text": text}


def issue_order(row: dict, std: survey_items.Standard | None = None) -> tuple:
    """Defects first, then other durable rows (checked objects), then one-off obstructions; id order within."""
    std = std or survey_items.load("KR")
    return (not std.is_defect(row), not is_durable(row, std), row["id"])


MODEL_NAMES = {config.SPARK_MODEL: "Muse Spark", second_opinion.MODEL: "Muse Spark Open",
               second_opinion.GLIMMER_MODEL: "Muse Glimmer (open weights, served on Modal)"}


def model_name(model_id: str) -> str:
    return MODEL_NAMES.get(model_id, model_id)


def box_of(row: dict) -> list[float] | None:
    """The model's box around the object, [x1, y1, x2, y2] in 0-1000 of the photo (the evidence photo is the same photo,
    only resized), or None when it marked none."""
    box = ((row.get("model_outputs") or {}).get("check") or {}).get("box")
    if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
        return [float(v) for v in box]
    return None


def items_json(std: survey_items.Standard) -> list[dict]:
    """Every kind of finding the scan can show, in the order the map gives them colours: the standard's checks, then
    the stages' own (KR tactile defects only where the standard has them)."""
    out = [{"id": c.id, "target": c.target, "map_icon": c.map_icon, "kind": "check", "inventory": c.inventory}
           for c in std.checks]
    stage = [k for k in STAGE_ITEMS if std.tactile_defects or k not in ("missing_blocks", "cover_cut", "fixed_obstruction")]
    return out + [{"id": k, **STAGE_ITEMS[k], "kind": "stage", "inventory": False} for k in stage]


def check_json(row: dict, std: survey_items.Standard) -> dict | None:
    check = std.check(row["issue_type"])
    if check is None:
        return None
    said = (row.get("model_outputs") or {}).get("check") or {}
    answered_by = said.get("model") or config.SPARK_MODEL
    return {"id": check.id, "find": check.find, "target": check.target, "question": check.ask,
            "note": said.get("note") or None,  # the model's own one-line reason, shown as it wrote it
            "answer": row.get("object_label") or "unclear", "report_when": check.report_when,
            "label_yes": check.label_yes, "label_no": check.label_no, "inventory": check.inventory,
            "model": model_name(answered_by)}


def second_json(row: dict) -> dict | None:
    """The other model's answer, when it gave one: Spark Open's second opinion on a defect, or Spark's audit of a
    judge's answer (a failed call stores an error instead)."""
    outputs = row.get("model_outputs") or {}
    so = outputs.get("second_opinion") or outputs.get("audit") or {}
    if "answer" not in so:
        return None
    return {"answer": so["answer"], "agree": so["agree"], "model": model_name(so.get("model") or second_opinion.MODEL)}


def entrance_json(row: dict, now: datetime | None = None, heading: float | None = None) -> dict:
    """heading: the compass direction of the row's photo (frame.compass), when the caller knows it."""
    return {"id": row["id"], "lat": row["lat"], "lng": row["lng"], "heading": heading, "name": row.get("place_name") or "",
            "category": row.get("category") or "", "step_count": row.get("step_count"),
            "stairs_detected": row.get("stairs_detected"), "sill_possible": row.get("sill_possible"),
            "label": entrance_label(row), "review_status": row["review_status"],
            "review_reasons": row["review_reasons"], "evidence": evidence_json(row.get("evidence")),
            "captured_at": row.get("captured_at"), "old_photo": is_old(row.get("captured_at"), now)}


def issue_json(row: dict, now: datetime | None = None, std: survey_items.Standard | None = None,
               heading: float | None = None) -> dict:
    std = std or survey_items.load("KR")
    return {"id": row["id"], "lat": row["lat"], "lng": row["lng"], "heading": heading, "box": box_of(row),
            "issue_type": row["issue_type"],
            "durable": is_durable(row, std), "defect": std.is_defect(row), "label": issue_label(row, std),
            "check": check_json(row, std), "second_opinion": second_json(row),
            "object_label": row.get("object_label"), "frame_start": row.get("frame_start"),
            "frame_end": row.get("frame_end"), "review_status": row["review_status"], "review_reasons": row["review_reasons"],
            "evidence": evidence_json(row.get("evidence")), "captured_at": row.get("captured_at"),
            "old_photo": is_old(row.get("captured_at"), now)}


def scan_json(scan: dict, entrances: list[dict], issues: list[dict], km: float | None = None,
              city_check: dict | None = None, dropped: int = 0) -> dict:
    """city_check: the offline comparison with the city's own records (eval, city_check_scan<N>.json), if one was
    made for this scan; left out otherwise."""
    std = standard_of(scan)
    live = [i for i in issues if i["review_status"] != "rejected"]
    defects = sum(std.is_defect(i) for i in live)
    obstructions = sum(i["issue_type"] == "obstruction" for i in live)
    needs_review = sum(r["review_status"] == "needs_review" for r in [*entrances, *live])
    field_check = sum(r["review_status"] == "field_check" for r in [*entrances, *live])  # a person could not tell
    verified = sum(r["review_status"] == "verified" for r in [*entrances, *live])
    rejected = sum(i["review_status"] == "rejected" for i in issues)
    with_steps = sum(entrance_label(e)["icon"] == "▲" for e in entrances)  # a possible sill is not counted
    checks = [{"id": c.id, "find": c.find, "target": c.target, "report_when": c.report_when,
               "label_yes": c.label_yes, "label_no": c.label_no, "inventory": c.inventory,
               "map_icon": c.map_icon, "question": c.question or c.ask,
               **{a: sum(i["issue_type"] == c.id and (i.get("object_label") or "unclear") == a for i in live)
                  for a in ("yes", "no", "unclear")}} for c in std.checks]
    return {**{k: scan[k] for k in SCAN_FIELDS},
            "standard": std.code, "checks": checks, "items": items_json(std),
            **({"city_check": city_check} if city_check is not None else {}),
            "km": km,
            "usd_per_km": round(scan["cost_usd"] / km, 2) if km else None,
            "counts": {"defects": defects, "obstructions": obstructions, "entrances": len(entrances),
                       "entrances_with_steps": with_steps, "needs_review": needs_review,
                       "field_check": field_check, "verified": verified, "rejected": rejected,
                       "dropped": dropped}}  # dropped: picks the models set aside as not the target
