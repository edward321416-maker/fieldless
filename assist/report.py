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
- Muse Spark may add a short plain-language paragraph (--narrate). It sees only the summary's facts, and code drops
  the paragraph if it names an item the summary does not have or writes a number the summary does not hold: the
  numbers in a report a city acts on come from code. Off by default, so the report needs no API key.
"""
import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from assist import export
from eval import store
from pipeline import config, survey_items
from pipeline.cost import CostMeter
from pipeline.meta_api import MetaApi, text_part
from pipeline.survey_items import Standard
from server import views

NARRATE_CAP_USD = 0.10
MAX_PRIORITIES = 5
NUMBER = re.compile(r"\d+(?:\.\d+)?")
HOW_MADE = ("Made from public Mapillary street photos: SAM 3.1 found each survey item in the photos, Muse Spark "
            "judged each one on the best photo, and a person confirms what the models found.")
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
    "type": "object", "additionalProperties": False, "required": ["summary", "priorities"],
    "properties": {
        "summary": {"type": "string"},
        "priorities": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["item", "reason"],
            "properties": {"item": {"type": "string"}, "reason": {"type": "string"}}}},
    },
}
NARRATE_PROMPT = """You write the opening paragraph of a street survey report for a city surveyor.
The JSON below is the whole survey; use nothing else.
- summary: 3 to 4 plain sentences on what the survey found and what still needs a person. Do not write numbers or
  dates; say "most", "a few" or "none" instead. State facts; never say "violation", "illegal" or "accessible".
- priorities: up to {max_priorities} items a surveyor should look at first. "item" is the item's exact "item" value
  from the JSON; "reason" is one sentence taken from the JSON, also without numbers.
Return JSON only.

{facts}"""


def _date(stamp: str | None) -> str | None:
    return stamp[:10] if stamp else None


def _item_entry(item: str, check, rows: list[tuple[dict, dict]]) -> dict:
    """Counts for one item. rows: (row, feature properties) for every row of the item, rejected ones included."""
    live = [(r, p) for r, p in rows if r["review_status"] != "rejected"]
    defects = [p for _, p in live if p["defect"]]
    kind = ("entrance" if item == export.ENTRANCE else "inventory" if export.is_inventory(check)
            else "check" if check is not None else "other")
    return {"item": item, "title": export.title(item, check), "kind": kind, "found": len(live),
            "photos": len({p["photo_id"] for _, p in live if p["photo_id"]}),
            "by_answer": dict(Counter(str(p["answer"]) for _, p in live if p["answer"] is not None)),
            "by_label": dict(Counter(p["label"] for _, p in live)),
            "by_review": dict(Counter(r["review_status"] for r, _ in rows)),
            "defects_confirmed": sum(p["review_status"] == "verified" for p in defects),
            "defects_unconfirmed": sum(p["review_status"] != "verified" for p in defects)}


def summary(scan_data: dict, standard: Standard, area: str | None = None, now: datetime | None = None) -> dict:
    """Everything the report states, as data: per item counts by answer, label and review status, defects confirmed
    and not yet confirmed, photos, km and cost (the app's rules), processing and review seconds, photo dates.
    Items of the standard come first, found or not; then any other item the scan holds. `now` dates "old" photos."""
    scan, frames = scan_data["scan"], scan_data["frames"]
    by_idx = {f["idx"]: f for f in frames}
    grouped: dict[str, list] = {c.id: [] for c in standard.checks}  # every item of the standard, even if not found
    for item, row in export.rows(scan_data):
        props = export.feature(item, row, by_idx.get(row.get("frame_idx")), standard, scan["id"])["properties"]
        grouped.setdefault(item, []).append((row, props))
    items = [_item_entry(item, standard.check(item), rows) for item, rows in grouped.items()]
    titles = {i["item"]: i["title"] for i in items}
    defects = [{**p, "title": titles[item]} for item, rows in grouped.items() for _, p in rows
               if p["defect"] and p["review_status"] == "verified"]
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
        "items": items, "defects": defects,
        "totals": {"found": sum(i["found"] for i in items),
                   "defects_confirmed": sum(i["defects_confirmed"] for i in items),
                   "defects_unconfirmed": sum(i["defects_unconfirmed"] for i in items),
                   "needs_review": sum(r["review_status"] == "needs_review" for r in live),
                   "field_check": sum(r["review_status"] == "field_check" for r in live),
                   "unclear": sum(i["by_answer"].get("unclear", 0) for i in items),
                   "rejected": sum(i["by_review"].get("rejected", 0) for i in items)},
    }


# --- text pieces shared by Markdown and HTML -------------------------------------------------------------------

def _duration(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} min"


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
            f"Model cost ${s['cost_usd']:.2f}{per_km}; {_duration(s['processing_seconds'])} processing and "
            f"{_duration(s['review_seconds'])} of a person's review.")


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
    lines += ["## Items", "", "| " + " | ".join(ITEM_HEADERS) + " |", "|" + "---|" * len(ITEM_HEADERS)]
    lines += ["| " + " | ".join(_md(c) for c in _item_cells(i)) + " |" for i in s["items"]]
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


# --- HTML ------------------------------------------------------------------------------------------------------

CSS = """
:root { color-scheme: light; --ink: #1d232a; --muted: #5b6672; --line: #d5dbe1; --head: #eef2f5; --warn: #9a3412; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px; background: #ffffff; color: var(--ink);
       font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 960px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 8px; }
h2 { font-size: 16px; margin: 24px 0 8px; }
p { margin: 6px 0; }
.muted { color: var(--muted); }
.table { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: var(--head); }
.defect { color: var(--warn); font-weight: 600; }
.spark { border-left: 3px solid var(--line); padding-left: 12px; }
details { margin-top: 16px; }
summary { cursor: pointer; font-weight: 600; }
a { color: #0b5cad; }
@media print { body { padding: 0; } details { display: none; } }
"""


def _h(text) -> str:
    return html.escape("" if text is None else str(text))


def _html_photo(p: dict) -> str:
    if not p["photo_url"]:
        return "no photo"
    text, url, credit = _attribution(p)
    return f'<a href="{_h(url)}">{_h(text)}</a> {_h(credit)}'


def _table(headers, rows: list[list[str]]) -> str:
    """A table whose cells are already HTML."""
    head = "".join(f'<th scope="col">{_h(h)}</th>' for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def to_html(s: dict, features: list[dict], notes: dict | None = None) -> str:
    """The same report as one self-contained HTML page (inline CSS, no scripts), with every finding listed at the
    end. Every photo link carries its attribution."""
    parts = [f"<h1>Street survey — {_h(s['area'])}</h1>", f"<p>{_h(HOW_MADE)}</p>",
             f'<p class="muted">{_h(_overview(s))}</p>']
    if notes and notes.get("summary"):
        prio = "".join(f"<li><b>{_h(p['item'])}</b>: {_h(p['reason'])}</li>" for p in notes.get("priorities", []))
        parts.append('<section class="spark"><h2>Summary (written by Muse Spark from the counts below)</h2>'
                     f"<p>{_h(notes['summary'])}</p>{f'<ul>{prio}</ul>' if prio else ''}</section>")
    parts.append("<h2>Items</h2>" + _table(ITEM_HEADERS, [[_h(c) for c in _item_cells(i)] for i in s["items"]]))
    parts.append("<h2>Confirmed defects</h2>")
    if s["defects"]:
        parts.append(_table(["Item", "Finding", "Photo"],
                            [[_h(d["title"]), f'<span class="defect">{_h(d["label"])}</span>', _html_photo(d)]
                             for d in s["defects"]]))
    else:
        parts.append("<p>No defect has been confirmed by a person yet.</p>")
    if _waiting(s):
        parts.append(f"<p>{_h(_waiting(s))}</p>")
    parts.append("<h2>Limits</h2><ul>" + "".join(f"<li>{_h(line)}</li>" for line in _limits(s)) + "</ul>")
    rows = [[_h(p["item"]), _h(p["label"]), _h(p["review_status"]), "yes" if p["defect"] else "",
             _html_photo(p)] for p in (f["properties"] for f in features)]
    parts.append(f"<details><summary>All findings ({len(rows)})</summary>"
                 + _table(["Item", "Finding", "Review status", "Defect", "Photo"], rows) + "</details>")
    parts.append('<p class="muted">Street photos: Mapillary contributors, CC BY-SA 4.0. Survey by Fieldless.</p>')
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>Street survey — {_h(s['area'])}</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main></body></html>\n")


# --- Muse Spark paragraph --------------------------------------------------------------------------------------

def facts(s: dict) -> dict:
    """What Spark may read: the counts, without photo ids, links or coordinates (their digits would let an invented
    number pass the check)."""
    keep = ("area", "standard", "photos", "photo_dates", "old_photos", "km", "cost_usd", "usd_per_km",
            "processing_seconds", "review_seconds", "totals")
    items = [{k: i[k] for k in ("item", "title", "kind", "found", "by_answer", "by_review", "defects_confirmed",
                                "defects_unconfirmed")} for i in s["items"]]
    return {**{k: s[k] for k in keep}, "items": items}


def check_narration(answer: dict, known: dict) -> list[str]:
    """Problems that make Spark's paragraph unusable: a priority naming an item the summary does not have, or a
    number in the text that the facts do not hold."""
    items = {i["item"] for i in known["items"]}
    problems = [f"unknown item {p['item']!r}" for p in answer.get("priorities", []) if p["item"] not in items]
    allowed = {float(n) for n in NUMBER.findall(json.dumps(known))}
    text = " ".join([answer.get("summary", ""), *(p["reason"] for p in answer.get("priorities", []))])
    problems += [f"number {n} is not in the summary" for n in NUMBER.findall(text) if float(n) not in allowed]
    return problems


def narrate(api, s: dict) -> dict:
    """Muse Spark's plain summary and up to five priorities, written from the summary's facts only. Returns
    {"summary", "priorities"}; when code finds a problem, summary is empty and "dropped" lists why."""
    known = facts(s)
    prompt = NARRATE_PROMPT.format(max_priorities=MAX_PRIORITIES, facts=json.dumps(known, ensure_ascii=False))
    answer = api.spark_json([text_part(prompt)], "survey_report_summary", NARRATE_SCHEMA)
    answer = {"summary": answer.get("summary", "").strip(), "priorities": answer.get("priorities", [])[:MAX_PRIORITIES]}
    problems = check_narration(answer, known)
    if problems:
        return {"summary": "", "priorities": [], "dropped": problems}
    return answer


# --- files and command -----------------------------------------------------------------------------------------

def write(scan_data: dict, standard: Standard, out_dir: Path, area: str | None = None,
          notes: dict | None = None) -> dict:
    """report.md, report.html and the export files in out_dir. Returns their paths."""
    out_dir = Path(out_dir)
    paths = export.write(scan_data, standard, out_dir)
    s = summary(scan_data, standard, area=area)
    paths["md"] = out_dir / "report.md"
    paths["html"] = out_dir / "report.html"
    paths["md"].write_text(to_markdown(s, notes), encoding="utf-8")
    paths["html"].write_text(to_html(s, export.features(scan_data, standard), notes), encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--standard", help="survey standard code, e.g. US (default: the scan's own)")
    ap.add_argument("--area", help="area name for the title (default: the scan id)")
    ap.add_argument("--narrate", action="store_true",
                    help=f"add a Muse Spark summary paragraph (needs MODEL_API_KEY; at most ${NARRATE_CAP_USD:.2f})")
    ap.add_argument("--out", type=Path, help="output folder (default: <run>/report_scan<N>/)")
    args = ap.parse_args(argv)
    run_dir = config.run_dir(args.run)
    conn = store.open_ro(run_dir / "threshold.db")
    try:
        data = store.load_scan(conn, args.scan)
    finally:
        conn.close()
    standard = survey_items.load(args.standard) if args.standard else views.standard_of(data["scan"])
    notes = None
    if args.narrate:
        api = MetaApi(run_dir / "calls.jsonl", CostMeter(NARRATE_CAP_USD))
        notes = narrate(api, summary(data, standard, area=args.area))
        for problem in notes.get("dropped", []):
            print(f"Muse Spark paragraph dropped: {problem}")
        print(f"model spend: ${api.meter.spent_usd:.2f}")
    paths = write(data, standard, args.out or run_dir / f"report_scan{args.scan}", area=args.area, notes=notes)
    for kind, path in paths.items():
        for name, p in (path.items() if isinstance(path, dict) else [(None, path)]):
            print(f"{kind}{f' {name}' if name else ''}: {p}")
    return paths


if __name__ == "__main__":
    main()
