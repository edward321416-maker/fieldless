"""A one-page survey report for one scan, written next to the export files (assist/export.py):

    python -m assist.report --run R --scan N [--standard US] [--area "Capitol Hill"] [--narrate] [--out DIR]

It writes report.md, report.html, survey_scan<N>.geojson and survey_scan<N>_<item>.csv into
<run>/report_scan<N>/ (or --out) and prints the paths. The database is opened read-only.

Why it is built this way:
- A surveyor hands files over, not a database or a web map. The report says what was surveyed, what was found per
  item, which defects a person confirmed (with the photo of each), and what photos cannot settle.
- No item is special-cased: the table has one row per item of the standard plus any other item the scan holds,
  and every count is taken the same way for each.
- Counts, labels, defects, km and cost come from code with the app's own rules (server.views: issue_label,
  surveyed_km, the cost per km of scan_json, is_old; Standard.is_defect), so the report and the map never disagree.
  The standard is the scan's own unless --standard names another.
- Every photo the report points to names its creator, its Mapillary link and the licence (Mapillary's terms).
- Muse Spark writes the report's sentences (--narrate): a summary, a paragraph per item and the priorities. It sees
  only the summary's facts, and code drops any piece that names an item the survey does not have, holds a digit or
  judges ("violation", "accessible"): the numbers in a report a city acts on come from code, and a dropped piece is
  replaced by code's own sentence. --save keeps the sentences in the database for the app (db.report_text). Off by
  default, so the report needs no API key.
"""
import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from assist import export, report_doc, style
from eval import store
from pipeline import config, db, survey_items
from pipeline.cost import CostMeter
from pipeline.meta_api import MetaApi, text_part
from pipeline.survey_items import Standard
from server import views

NARRATE_CAP_USD = 0.10
MAX_PRIORITIES = 5
DIGIT = re.compile(r"\d")
BANNED = re.compile(r"\b(?:non-compliant|compliant|violations?|illegal|inaccessible|accessible)\b", re.IGNORECASE)
HOW_MADE = ("Made from public Mapillary street photos: SAM 3.1 found each survey item in the photos and Muse Spark "
            "judged each one on the best photo. What the models could not settle is marked for review or a site "
            "visit.")
LIMITS_FIXED = (
    "A photo settles only what it shows: measurements (slopes, widths, heights) and anything hidden or out of frame "
    "need a visit.",
    "An item found zero times is not shown to be absent; only the streets the photos cover were surveyed.",
    "Each finding is placed at the position of its photo, not of the object itself.",
    "Inventory items list what was found and are never counted as defects.",
)
ITEM_HEADERS = ("Item", "Kind", "Found", "Results", "Confirmed / to review / check on site / rejected",
                "Defects confirmed / to confirm")

NARRATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "items", "priorities"],
    "properties": {
        "summary": {"type": "string"},
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["item", "paragraph"],
            "properties": {"item": {"type": "string"}, "paragraph": {"type": "string"}}}},
        "priorities": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["item", "reason"],
            "properties": {"item": {"type": "string"}, "reason": {"type": "string"}}}},
    },
}
NARRATE_PROMPT = """You write the prose of a street survey report for a city official. The JSON below is the whole
survey; use nothing else.
- summary: 3 to 4 plain sentences: what was surveyed, what was found, and what needs action first.
- items: for each item in the JSON, in its order, one paragraph of 2 to 3 sentences on what was found and what to
  check or repair. "item" is the item's exact "item" value. Where the item has a city list ("city"), say only where
  the survey and the list differ; never say the list is wrong or out of date, since photos miss things and other
  agencies keep their own lists.
- priorities: up to {max_priorities} actions the city should take first. "item" is an exact "item" value; "reason" is
  one sentence.
Rules: never write a digit (no numbers, no dates); say "most", "a few", "one" or "none" instead. State facts only;
never say "violation", "illegal", "compliant" or "accessible". Do not name the place or the survey, and do not write
about costs or how this survey compares with other surveys.
Return JSON only.

{facts}"""


def text_problem(text: str) -> str | None:
    """Why a sentence from the model cannot go into the report, or None: a digit (every number comes from code) or a
    word that judges instead of describing."""
    if DIGIT.search(text):
        return "a number"
    banned = BANNED.search(text)
    return f"the word {banned.group(0).lower()!r}" if banned else None


def _date(stamp: str | None) -> str | None:
    return stamp[:10] if stamp else None


def _item_entry(item: str, check, rows: list[tuple[dict, dict]], dropped: int = 0) -> dict:
    """Counts for one item. rows: (row, feature properties) for every row of the item, rejected ones included."""
    live = [(r, p) for r, p in rows if r["review_status"] != "rejected"]
    defects = [p for _, p in live if p["defect"]]
    kind = ("entrance" if item == export.ENTRANCE else "inventory" if export.is_inventory(check)
            else "check" if check is not None else "other")
    question = ((getattr(check, "question", "") or getattr(check, "ask", "") or "").strip() or None
                if check is not None else views.STAGE_ITEMS.get(item, {}).get("question"))
    title = export.title(item, check) if check is not None else views.STAGE_ITEMS.get(item, {}).get("target", item)
    return {"item": item, "title": title, "kind": kind, "question": question,
            "found": len(live),
            "photos": len({p["photo_id"] for _, p in live if p["photo_id"]}),
            "by_answer": dict(Counter(str(p["answer"]) for _, p in live if p["answer"] is not None)),
            "by_label": dict(Counter(p["label"] for _, p in live)),
            "by_review": dict(Counter(r["review_status"] for r, _ in rows)),
            "defects_confirmed": sum(p["review_status"] == "verified" for p in defects),
            "defects_unconfirmed": sum(p["review_status"] != "verified" for p in defects),
            "dropped": dropped, "city": None}


def _note(item: str, row: dict) -> str | None:
    """The model's one-line note on the photo it judged: Spark's note for a check, the reading's notes for an
    entrance."""
    outputs = row.get("model_outputs") or {}
    source = outputs.get("reading") if item == export.ENTRANCE else outputs.get("check")
    note = (source or {}).get("notes" if item == export.ENTRANCE else "note")
    return (note or "").strip() or None


def _ledger_entry(item: str, title: str, row: dict, feat: dict) -> dict:
    p = feat["properties"]
    lng, lat = (feat["geometry"] or {}).get("coordinates") or (None, None)
    return {"item": item, "title": title, "row_id": p["row_id"], "label": p["label"], "status": p["status"],
            "review_status": p["review_status"], "answer": p["answer"], "defect": p["defect"],
            "photo_id": p["photo_id"], "photo_url": p["photo_url"], "creator": p["creator"],
            "license": p["license"], "captured_at": p["captured_at"],
            "evidence": Path(p["evidence"]).name if p["evidence"] else None,
            "box": None if item == export.ENTRANCE else views.box_of(row), "note": _note(item, row),
            "lat": lat, "lng": lng}


def _walks(frames: list[dict]) -> list[list[tuple[float, float]]]:
    """Each walk's photo positions in photo order, for the report's map."""
    out: dict[int, list] = {}
    for f in sorted(frames, key=lambda f: f["idx"]):
        if f.get("lat") is not None and f.get("lng") is not None:
            out.setdefault(f.get("clip", 0), []).append((f["lat"], f["lng"]))
    return list(out.values())


def _missed(city_check: dict, item: str) -> dict:
    """The city's records of an item that the photos showed but the survey did not find, by the city's own ids
    (id_fields, e.g. a tree's UNITID), so staff can look each one up. Only the report lists them; Spark never reads
    them (facts)."""
    c = city_check["items"][item]
    missed = [m.get("city") or {} for m in c.get("missed") or []]
    return {"id_fields": list(c.get("id_fields") or sorted({k for m in missed for k in m})), "missed": missed}


def summary(scan_data: dict, standard: Standard, area: str | None = None, now: datetime | None = None) -> dict:
    """Everything the report states, as data: per item counts by answer, label and review status, defects confirmed
    and not yet confirmed, photos, km and cost (the app's rules), processing and review seconds, photo dates; the
    ledger of every finding a person did not reject, the actions it calls for, and the walks for the map.
    Items of the standard come first, found or not; then any other item the scan holds. `now` dates "old" photos
    and the report."""
    scan, frames = scan_data["scan"], scan_data["frames"]
    by_idx = {f["idx"]: f for f in frames}
    grouped: dict[str, list] = {c.id: [] for c in standard.checks}  # every item of the standard, even if not found
    for item, row in export.rows(scan_data):
        feat = export.feature(item, row, by_idx.get(row.get("frame_idx")), standard, scan["id"])
        grouped.setdefault(item, []).append((row, feat["properties"], feat))
    set_aside = scan_data.get("dropped") or []
    for d in set_aside:
        grouped.setdefault(d["issue_type"], [])
    items = [_item_entry(item, standard.check(item), [(r, p) for r, p, _ in rows],
                         sum(d["issue_type"] == item for d in set_aside))
             for item, rows in grouped.items()]
    city_check = scan_data.get("city_check")
    for i in items:
        c = (city_check or {}).get("items", {}).get(i["item"])
        if c:
            matched = set(c.get("matched_ids") or [])
            kept = [r for r, _, _ in grouped[i["item"]] if r["review_status"] != "rejected"]
            i["city"] = {"found": c["found"], "covered": c["covered"], "not_seen": max(0, c["covered"] - c["found"]),
                         "not_in_city": sum(r["id"] not in matched for r in kept)}
    titles = {i["item"]: i["title"] for i in items}
    defects = [{**p, "title": titles[item]} for item, rows in grouped.items() for _, p, _ in rows
               if p["defect"] and p["review_status"] == "verified"]
    ledger = [_ledger_entry(item, titles[item], row, feat) for item, rows in grouped.items()
              for row, _, feat in rows if row["review_status"] != "rejected"]
    actions = {"repairs": [e for e in ledger if e["defect"]],
               "site_visits": [e for e in ledger if e["review_status"] == "field_check" or e["answer"] == "unclear"],
               "city": [{"item": i["item"], "title": i["title"], **i["city"], **_missed(city_check, i["item"])}
                        for i in items if i["city"] and (i["city"]["not_seen"] or i["city"]["not_in_city"])]}
    km = views.surveyed_km(frames)
    app = views.scan_json(scan, scan_data["entrances"], scan_data["issues"], km=km)
    dates = sorted(f["captured_at"] for f in frames if f.get("captured_at"))
    live = [r for _, r in export.rows(scan_data) if r["review_status"] != "rejected"]
    return {
        "scan_id": scan["id"], "area": area or f"scan {scan['id']}", "standard": standard.code,
        "bbox": scan.get("bbox"), "photos": len(frames),
        "photo_dates": {"from": _date(dates[0]), "to": _date(dates[-1])} if dates else None,
        "old_photos": sum(views.is_old(f.get("captured_at"), now) for f in frames),
        "old_photo_years": views.OLD_PHOTO_YEARS,
        "km": km, "cost_usd": round(scan["cost_usd"], 2), "usd_per_km": app["usd_per_km"],
        "processing_seconds": round(scan["processing_seconds"]), "review_seconds": round(scan["review_seconds"]),
        "items": items, "defects": defects, "city_check": city_check, "ledger": ledger, "actions": actions,
        "walks": _walks(frames), "made": (now or datetime.now(timezone.utc)).date().isoformat(),
        "styles": style.item_styles(views.items_json(standard)),
        "totals": {"found": sum(i["found"] for i in items),
                   "defects_confirmed": sum(i["defects_confirmed"] for i in items),
                   "defects_unconfirmed": sum(i["defects_unconfirmed"] for i in items),
                   "needs_review": sum(r["review_status"] == "needs_review" for r in live),
                   "field_check": sum(r["review_status"] == "field_check" for r in live),
                   "unclear": sum(i["by_answer"].get("unclear", 0) for i in items),
                   "rejected": sum(i["by_review"].get("rejected", 0) for i in items),
                   "dropped": len(set_aside), "dropped_audited": sum(bool(d.get("audited")) for d in set_aside)},
    }


# --- text pieces of the Markdown report -------------------------------------------------------------------

def _duration(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} min"


def _headers(s: dict) -> tuple[str, ...]:
    """The items table's headers: a "City record" column only when the survey has a city-record check."""
    return (*ITEM_HEADERS, "City record") if s.get("city_check") else ITEM_HEADERS


def _city_cell(s: dict, i: dict) -> str:
    c = i.get("city")
    return f"{c['found']} of {c['covered']} ({s['city_check']['city']})" if c else "-"


def _row_cells(s: dict, i: dict) -> list[str]:
    return [*_item_cells(i), *([_city_cell(s, i)] if s.get("city_check") else [])]


def _city_note(s: dict) -> str | None:
    """How the city-record numbers were measured, under the items table."""
    cc = s.get("city_check")
    return f"City record: {cc['method']} Measured {cc['measured_at']}." if cc else None


def _item_cells(i: dict) -> list[str]:
    """One row of the items table as plain text. Results are counted by label, so every item reads the same way;
    inventory items and entrances are never defects, so their defect cell is a dash, not a zero."""
    results = "; ".join(f"{n} × {label}" for label, n in i["by_label"].items()) or "-"
    review = " / ".join(str(i["by_review"].get(k, 0)) for k in ("verified", "needs_review", "field_check", "rejected"))
    never = i["kind"] in ("inventory", "entrance")
    defects = "-" if never else f"{i['defects_confirmed']} / {i['defects_unconfirmed']}"
    return [_item_name(i), i["kind"], str(i["found"]), results, review, defects]


def _overview(s: dict) -> str:
    dates = s["photo_dates"]
    taken = f", taken {dates['from']} to {dates['to']}" if dates else ""
    per_km = f" (${s['usd_per_km']:.2f} per km)" if s["usd_per_km"] is not None else ""
    return (f"Scan {s['scan_id']}, standard {s['standard']}: {s['photos']} photos{taken}, {s['km']} km of street. "
            f"Model cost ${s['cost_usd']:.2f}{per_km}; {_duration(s['processing_seconds'])} processing.")


def _item_name(i: dict) -> str:
    return i["title"] if i["title"] == i["item"] else f"{i['title']} ({i['item']})"


def _limits(s: dict) -> list[str]:
    out = []
    if s["photo_dates"]:
        out.append(f"Photos were taken {s['photo_dates']['from']} to {s['photo_dates']['to']}. Older than "
                   f"{s['old_photo_years']} years: {s['old_photos']} of {s['photos']}; the street may have changed "
                   f"since.")
    t = s["totals"]
    if t["unclear"] or t["needs_review"]:
        out.append(f"Unclear from the photo: {t['unclear']}. Awaiting a person's review: {t['needs_review']}. "
                   f"Both are in the GeoJSON and CSV files with their review status.")
    if t["dropped"]:
        out.append(f"Set aside by the models as not the target: {t['dropped']} (in the dropped CSV, with their "
                   f"photos); {t['dropped_audited']} of them went to a person as a random spot check.")
    if t["field_check"]:
        out.append(f"A person could not tell from the photo either: {t['field_check']}. These need a site visit "
                   f"(status \"Check on site\" in the GeoJSON and CSV files).")
    return out + list(LIMITS_FIXED)


def _waiting(s: dict) -> str | None:
    n = s["totals"]["defects_unconfirmed"]
    return f"Possible defects awaiting a person's confirmation: {n}." if n else None


def _attribution(p: dict) -> tuple[str, str, str]:
    """(link text, url, credit) for one photo: creator, Mapillary link and licence, as Mapillary's terms require."""
    who = p["creator"] or "a Mapillary contributor"
    taken = f", taken {_date(p['captured_at'])}" if p["captured_at"] else ""
    return f"Mapillary photo {p['photo_id']}", p["photo_url"], f"by {who}, {p['license']}{taken}"


# --- Markdown --------------------------------------------------------------------------------------------------

def _md(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _md_photo(p: dict) -> str:
    if not p["photo_url"]:
        return "no photo"
    text, url, credit = _attribution(p)
    return f"[{text}]({url}) {credit}"


def to_markdown(s: dict, notes: dict | None = None) -> str:
    lines = [f"# Street survey — {s['area']}", "", HOW_MADE, "", _overview(s), ""]
    if notes and notes.get("summary"):
        lines += ["## Summary (written by Muse Spark from the counts below)", "", notes["summary"], ""]
        lines += [f"- {_md(p['item'])}: {_md(p['reason'])}" for p in notes.get("priorities", [])]
        lines += [""] if notes.get("priorities") else []
    headers = _headers(s)
    lines += ["## Items", "", "| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_md(c) for c in _row_cells(s, i)) + " |" for i in s["items"]]
    lines += ["", _city_note(s)] if _city_note(s) else []
    lines += ["", "## Confirmed defects", ""]
    if s["defects"]:
        lines += ["| Item | Finding | Photo |", "|---|---|---|"]
        lines += [f"| {_md(d['title'])} | {_md(d['label'])} | {_md(_md_photo(d))} |" for d in s["defects"]]
    else:
        lines.append("No defect has been confirmed by a person yet.")
    lines += ["", _waiting(s)] if _waiting(s) else []
    lines += ["", "## Limits", ""] + [f"- {line}" for line in _limits(s)]
    lines += ["", "Street photos: Mapillary contributors, CC BY-SA 4.0. Survey by Fieldless."]
    return "\n".join(lines) + "\n"


# --- Muse Spark paragraph --------------------------------------------------------------------------------------

def facts(s: dict) -> dict:
    """What Spark may read: the counts, without the place, photo ids, links or coordinates."""
    keep = ("standard", "photos", "photo_dates", "old_photos", "km", "totals")
    items = [{k: i[k] for k in ("item", "title", "kind", "question", "found", "by_answer", "by_review",
                                "defects_confirmed", "defects_unconfirmed", "city")} for i in s["items"]]
    actions = {k: len(v) for k, v in s["actions"].items()}
    return {**{k: s[k] for k in keep}, "items": items, "actions": actions}


def narrate(api, s: dict) -> dict:
    """Muse Spark's summary, one paragraph per item and up to five priorities, written from the summary's facts.
    Each piece is checked on its own (text_problem, known item ids); a piece that fails is left out and "dropped"
    says why, so the report puts code's own sentence in its place."""
    known = facts(s)
    prompt = NARRATE_PROMPT.format(max_priorities=MAX_PRIORITIES, facts=json.dumps(known, ensure_ascii=False))
    answer = api.spark_json([text_part(prompt)], "survey_report_text", NARRATE_SCHEMA)
    items = {i["item"] for i in s["items"]}
    out: dict = {"summary": "", "items": {}, "priorities": []}
    dropped: list[str] = []
    summary_text = (answer.get("summary") or "").strip()
    if summary_text and (problem := text_problem(summary_text)):
        dropped.append(f"summary: {problem}")
    else:
        out["summary"] = summary_text
    for entry in answer.get("items") or []:
        text = (entry.get("paragraph") or "").strip()
        if entry.get("item") not in items:
            dropped.append(f"unknown item {entry.get('item')!r}")
        elif problem := text_problem(text):
            dropped.append(f"{entry['item']}: {problem}")
        elif text:
            out["items"][entry["item"]] = text
    for entry in (answer.get("priorities") or [])[:MAX_PRIORITIES]:
        reason = (entry.get("reason") or "").strip()
        if entry.get("item") not in items:
            dropped.append(f"unknown item {entry.get('item')!r}")
        elif problem := text_problem(reason):
            dropped.append(f"priority {entry['item']}: {problem}")
        else:
            out["priorities"].append({"item": entry["item"], "reason": reason})
    if dropped:
        out["dropped"] = dropped
    return out


# --- files and command -----------------------------------------------------------------------------------------

def document(scan_data: dict, standard: Standard, area: str | None = None, notes: dict | None = None,
             photo_base: str = "evidence/", now: datetime | None = None) -> str:
    """The report page for one scan (assist.report_doc), with the saved sentences when there are any."""
    s = summary(scan_data, standard, area=area, now=now)
    return report_doc.document(s, notes, photo_base=photo_base, limits=_limits(s))


def write(scan_data: dict, standard: Standard, out_dir: Path, area: str | None = None,
          notes: dict | None = None) -> dict:
    """report.md, report.html and the export files in out_dir. Returns their paths."""
    out_dir = Path(out_dir)
    paths = export.write(scan_data, standard, out_dir)
    s = summary(scan_data, standard, area=area)
    paths["md"] = out_dir / "report.md"
    paths["html"] = out_dir / "report.html"
    paths["md"].write_text(to_markdown(s, notes), encoding="utf-8")
    paths["html"].write_text(report_doc.document(s, notes, photo_base="evidence/", limits=_limits(s)),
                             encoding="utf-8")
    return paths


def load_city_check(run_dir: Path, scan_id: int) -> dict | None:
    """The offline comparison with the city's records (eval, city_check_scan<N>.json), if one was made."""
    path = Path(run_dir) / f"city_check_scan{scan_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def make_text(conn, run_dir: Path, scan_id: int, api, area: str | None = None) -> dict:
    """Muse Spark's sentences for one finished scan, checked (narrate) and saved (db.report_text), so the report
    never asks the model again. The spend goes on api's meter, which the caller charges to the scan."""
    data = store.load_scan(conn, scan_id)
    data["city_check"] = load_city_check(run_dir, scan_id)
    notes = narrate(api, summary(data, views.standard_of(data["scan"]), area=area))
    db.set_report_text(conn, scan_id, notes, config.SPARK_MODEL)
    return notes


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--standard", help="survey standard code, e.g. US (default: the scan's own)")
    ap.add_argument("--area", help="area name for the title (default: the scan id)")
    ap.add_argument("--narrate", action="store_true",
                    help=f"have Muse Spark write the report's sentences (needs MODEL_API_KEY; at most "
                         f"${NARRATE_CAP_USD:.2f})")
    ap.add_argument("--save", action="store_true",
                    help="with --narrate: keep the sentences in the database for the app, and add the spend to the "
                         "scan's cost")
    ap.add_argument("--out", type=Path, help="output folder (default: <run>/report_scan<N>/)")
    args = ap.parse_args(argv)
    if args.save and not args.narrate:
        ap.error("--save needs --narrate")
    run_dir = config.run_dir(args.run)
    conn = db.connect(run_dir / "threshold.db") if args.save else store.open_ro(run_dir / "threshold.db")
    try:
        data = store.load_scan(conn, args.scan)
        data["city_check"] = load_city_check(run_dir, args.scan)
        standard = survey_items.load(args.standard) if args.standard else views.standard_of(data["scan"])
        notes = db.report_text(conn, args.scan)
        if args.narrate:
            api = MetaApi(run_dir / "calls.jsonl", CostMeter(NARRATE_CAP_USD))
            notes = narrate(api, summary(data, standard, area=args.area))
            for problem in notes.get("dropped", []):
                print(f"Muse Spark sentence dropped: {problem}")
            print(f"model spend: ${api.meter.spent_usd:.2f}")
            if args.save:
                db.set_report_text(conn, args.scan, notes, config.SPARK_MODEL)
                db.update_scan(conn, args.scan, cost_usd=data["scan"]["cost_usd"] + api.meter.spent_usd)
    finally:
        conn.close()
    paths = write(data, standard, args.out or run_dir / f"report_scan{args.scan}", area=args.area, notes=notes)
    for kind, path in paths.items():
        for name, p in (path.items() if isinstance(path, dict) else [(None, path)]):
            print(f"{kind}{f' {name}' if name else ''}: {p}")
    return paths


if __name__ == "__main__":
    main()
