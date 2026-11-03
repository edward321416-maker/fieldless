"""The Record Update List: what a city's asset team takes from a survey, one page to read and one table to open
(eval.asset_check makes the rows, eval.record_check compares them).

Why it says what it says:
- Each row says what the city could do with it, and how many records are candidates for desk review is the first thing
  on the page. That is not a count of site visits avoided: nobody measured one. A row is a suggestion: the city decides.
- A record seen in a photo has its object seen, not its attributes checked. Where the city's list has a yes or no a photo
  can answer, a record whose answer the photos left open is a visit, and the row says which attribute and why.
- "Seen in photo" is an observation, not the city's inspection, so the table has its own date column and never fills
  the city's date fields. "Not determined" does not mean missing.
- A record keeps the city's position. An object not on the list carries the camera's position, rated Unknown (within
  50 ft) in the words of city GIS metadata: it is a place to look, not a new record.
- Every photo names its creator, the Mapillary logo, a link and the licence (Mapillary's terms). Photos are the blurred
  evidence photos only.
- The page states what was compared. It does not rate the city's list, and it does not compare itself with surveys on
  foot (docs/ui-rules.md U1).
"""
import base64
import csv
import html
import io
import json
from datetime import datetime
from pathlib import Path

from assist.export import _cell
from server import views

NOT_DETERMINED, NOT_LISTED, SEEN = "Not determined", "Not on the city list", "Seen in photo"
PANORAMA = "Second look, panorama"
FIX, DATE, SAME, VISIT, COMPARE = ("Correct the record", "Update the date", "No change", "Visit on site",
                                   "Compare with the list")
SUMMARY_FIELDS = ("city", "items", "totals", "actions", "no_visit", "compare_unsure", "left_out", "trust")
# a list made before they existed has none of them
LATER_FIELDS = ("credit", "visits_with_photo", "date_gap_years", "desk_review_candidates", "attribute_checks",
                "visits_attribute_unresolved")
COLUMNS = (("action", "What the city could do with this row"),
           ("asset", "What the object is"),
           ("city_list", "The city list the record is on"),
           ("city_record", "The record's number on that list"),
           ("city_location", "The record's address, in the city's words"),
           ("city_says", "What the city's record says, where this row suggests a change"),
           ("photo_shows", "What the photo shows, where this row suggests a change"),
           ("status", f"{SEEN}, {NOT_DETERMINED} or {NOT_LISTED}"),
           ("how_seen", "Survey, Second look, or Second look, panorama (a flat view cut from a 360-degree photo)"),
           ("models_unsure", "yes when the models could not settle what the object is"),
           ("city_date", "The city's own date for the record, as year and month, from the field its list keeps for "
                         "checking a record; empty where the list's date only says when the list was updated"),
           ("seen_in_photo_on", "The date of the photo. This is an observation, not an inspection"),
           ("photo_newer_than_city_date", "yes when the photo is newer than the city's date"),
           ("city_condition", "The record's condition, in the city's words"),
           ("lat", "Latitude"),
           ("lng", "Longitude"),
           ("position_source", "Where the position comes from"),
           ("position_accuracy", "How far to trust the position"),
           ("photo_url", "The photo on Mapillary"),
           ("photo_by", "The photo's creator"),
           ("license", "The photo's licence"),
           ("evidence_file", "The blurred photo in this download"),
           ("nearby_photo_link", "Where the photos did not settle a record: a survey photo on Mapillary taken near the "
                                 "record's position. It may not show the record"),
           ("nearby_photo_m", "Metres from the record's position to where that photo was taken"),
           ("nearby_photo_by", "That photo's creator"),
           ("nearby_photo_license", "That photo's licence"),
           ("attribute_field", "Where the city's list has a yes or no a photo can answer: the field it is in"),
           ("attribute_state", "agree, different, unsettled or not_checked: how the check of that field ended. Seen in "
                               "photo says the object is there, not that this field was checked"),
           ("attribute_note", "The city's side of that field and why it is still open, where it is"),
           ("action_if_attribute_agrees", "Where a record is there in a photo but its field is open: what this row would "
                                          "suggest if the field agreed with the photo, so its date is not lost"),
           ("visit_reason", "Why a Visit on site row needs a newer photo or a field check"))
ROOT = Path(__file__).resolve().parent.parent
LOGOS = (ROOT / "web" / "public" / "mapillary-logo.png", ROOT / "web" / "dist" / "mapillary-logo.png")
ACTIONS = {FIX: "Look at the photo, then correct the record if it holds.",
           DATE: "Review the city's date. The photo is newer.",
           SAME: "Keep the record as it is.",
           VISIT: "The photos do not settle this. It needs a newer photo or a field check.",
           COMPARE: "Compare with nearby records, then decide."}
ORDER = (FIX, DATE, SAME, VISIT, COMPARE)

CSS = """
:root { --paper: #fff; --ink: #1b2430; --muted: #566273; --rule: #d9dde3; --panel: #f1f3f5; --accent: #1d4ed8;
  --seen: #17694a; --seen-bg: #dcefe6; --new: #8a5200; --new-bg: #f8ead2; --unk: #4d5968; --unk-bg: #e4e7eb;
  --fix-bg: #dbe6fb; }
* { box-sizing: border-box; }
body { background: var(--paper); color: var(--ink); margin: 0;
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 32px 16px 56px; display: grid; gap: 40px; }
.sample { font-size: 14px; color: var(--muted); border-bottom: 1px solid var(--rule); padding-bottom: 12px; margin: 0; }
h1 { font-size: clamp(28px, 5vw, 40px); line-height: 1.15; margin: 0 0 8px; }
h2 { font-size: 21px; margin: 0 0 12px; } h3 { font-size: 17px; margin: 8px 0; }
p { margin: 0; max-width: 68ch; } .meta { color: var(--muted); } .lead { font-size: 21px; line-height: 1.4; margin-top: 16px; }
.bar { display: flex; height: 18px; border-radius: 4px; overflow: hidden; margin-top: 20px; background: var(--unk-bg); }
.bar span { background: var(--seen); }
.key { display: flex; flex-wrap: wrap; gap: 8px 24px; margin-top: 8px; font-size: 14px; color: var(--muted); }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
.counts { min-width: 620px; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--rule); text-align: right; vertical-align: top; }
th:first-child, thead th:first-child { text-align: left; }
thead th { font-size: 13px; font-weight: 600; color: var(--muted); vertical-align: bottom; }
tbody th { font-weight: 600; } tbody th small { display: block; font-weight: 400; color: var(--muted); font-size: 13px; }
tfoot td, tfoot th { font-weight: 700; border-bottom: 0; }
.next { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; }
.next li { display: grid; grid-template-columns: 56px 1fr; gap: 12px; align-items: baseline; }
.next strong { display: block; }
.next b { font-size: 24px; font-variant-numeric: tabular-nums; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 20px; }
.card { min-width: 0; border: 1px solid var(--rule); border-radius: 8px; overflow: hidden; }
figure { position: relative; margin: 0; line-height: 0; } figure img { width: 100%; display: block; }
.box { position: absolute; border: 2px solid #ffd33d; box-shadow: 0 0 0 1px #000; }
.body { padding: 12px 14px 14px; }
.chip { display: inline-block; font-size: 13px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
.chip.seen { background: var(--seen-bg); color: var(--seen); } .chip.visit { background: var(--new-bg); color: var(--new); }
.chip.unknown { background: var(--unk-bg); color: var(--unk); } .chip.fix { background: var(--fix-bg); color: var(--accent); }
dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0 0 8px; font-size: 14px; }
dt { color: var(--muted); } dd { margin: 0; text-align: right; overflow-wrap: anywhere; } .action { font-size: 15px; font-weight: 600; }
.credit { font-size: 12px; color: var(--muted); margin-top: 8px; } .logo { height: 11px; vertical-align: -1px; }
a { color: var(--accent); }
.trust { display: grid; gap: 10px; margin: 0; padding-left: 20px; }
.columns th, .columns td { text-align: left; font-size: 14px; padding: 6px 12px; }
.columns th { font-family: ui-monospace, Consolas, monospace; font-weight: 400; white-space: nowrap; }
@media print { main { max-width: none; padding: 0; gap: 24px; } .card { break-inside: avoid; } }
"""


def table_rows(check: dict) -> list[dict]:
    """The rows as a city's table holds them: the COLUMNS, with the photo's attribution in its own cells."""
    out = []
    for row in check["rows"]:
        ev = row.get("evidence") or {}
        near = row.get("nearby_photo") or {}
        out.append({**{k: row.get(k, "") for k, _ in COLUMNS}, "photo_url": ev.get("link", ""),
                    "photo_by": ev.get("creator") or "", "license": ev.get("license", ""),
                    "evidence_file": f"evidence/{ev['image']}" if ev.get("image") else "",
                    "nearby_photo_link": near.get("link", ""), "nearby_photo_m": near.get("dist_m", ""),
                    "nearby_photo_by": near.get("creator") or "", "nearby_photo_license": near.get("license", "")})
    return out


def table_csv(check: dict) -> bytes:
    buf = io.StringIO(newline="")
    out = csv.writer(buf)
    names = [k for k, _ in COLUMNS]
    out.writerow(names)
    out.writerows([_cell(row[k]) for k in names] for row in table_rows(check))
    return buf.getvalue().encode("utf-8-sig")  # Excel then reads non-ASCII addresses correctly; QGIS and pandas skip it


def table_geojson(check: dict) -> bytes:
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [row["lng"], row["lat"]]},
                 "properties": {k: v for k, v in row.items() if k not in ("lat", "lng")}} for row in table_rows(check)]
    return json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=1).encode("utf-8")


def summary(check: dict) -> dict:
    return {**{k: check[k] for k in SUMMARY_FIELDS}, **{k: check[k] for k in LATER_FIELDS if check.get(k) is not None}}


def for_app(check: dict) -> dict:
    """The check as the app reads it: each photo with the address the server gives it."""
    return {**summary(check), "scan_id": check["scan_id"], "rows": [{**row, "evidence": views.evidence_json(row.get("evidence"))}
                                       for row in check["rows"]], "unreached": check.get("unreached", []),
            "unreached_fields": check.get("unreached_fields", {})}


def photos(check: dict) -> set[str]:
    return {row["evidence"]["image"] for row in check["rows"] if row.get("evidence")}


def _logo() -> str:
    path = next((p for p in LOGOS if p.is_file()), None)
    if path is None:
        return "Mapillary"
    return (f'<img class="logo" alt="Mapillary" src="data:image/png;base64,'
            f'{base64.b64encode(path.read_bytes()).decode()}">')


def _month(text: str) -> str:
    return datetime.strptime(text, "%Y-%m").strftime("%B %Y")


def _months(months: list[str]) -> str:
    names = [_month(m) for m in months]
    return " and ".join(names) if len(names) < 3 else f"{', '.join(names[:-1])} and {names[-1]}"


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def seen_how(totals: dict) -> str:
    """How the seen records came to be seen, so the page's count lines up with the survey's."""
    words = (("Survey", "by the survey"), ("Second look", "on a second look"),
             (PANORAMA, "on a second look in panoramas"))
    return ", ".join(f"{totals['seen_by'][how]} {text}" for how, text in words if totals["seen_by"].get(how))


def examples(check: dict) -> list[dict]:
    """A row to show for each action that has a photo, each of a different asset where it can be (docs/design-rules.md
    R2): a photo of what each kind of row is. Rows the models were unsure of are not shown as examples."""
    shown: list[dict] = []
    counts = check.get("actions", {})
    for action in sorted((FIX, DATE, SAME, COMPARE), key=lambda a: -counts.get(a, 0)):
        rows = [r for r in check["rows"] if r["action"] == action and r.get("evidence") and r["models_unsure"] != "yes"]
        pick = next((r for r in rows if r["asset"] not in {s["asset"] for s in shown}), rows[0] if rows else None)
        if pick:
            shown.append(pick)
    return shown


def _card(row: dict, logo: str, photo_base: str) -> str:
    e, ev, box = html.escape, row["evidence"], row.get("box")
    mark = (f'<span class="box" style="left:{box[0] / 10}%;top:{box[1] / 10}%;width:{(box[2] - box[0]) / 10}%;'
            f'height:{(box[3] - box[1]) / 10}%"></span>') if box and len(box) == 4 else ""
    kind = {FIX: "fix", COMPARE: "unknown"}.get(row["action"], "seen")
    cells = [("City record", row["city_record"]), ("City says", row["city_says"]), ("Photo shows", row["photo_shows"]),
             ("City date", row["city_date"]), ("How seen", row["how_seen"]), ("Seen in photo on", row["seen_in_photo_on"])]
    facts = "".join(f"<dt>{k}</dt><dd>{e(v)}</dd>" for k, v in cells if v)
    edited = "people and plates blurred, resized" if ev.get("blurred_regions") else "resized"
    if row["how_seen"] == PANORAMA:
        edited = f"cut from a 360-degree photo, {edited}"
    return f"""<article class="card">
  <figure><img src="{e(photo_base + ev['image'])}" alt="Street photo: {e(row['asset'])}" loading="lazy">{mark}</figure>
  <div class="body">
    <span class="chip {kind}">{e(row['action'])}</span>
    <h3>{e(row['asset'])}</h3>
    <dl>{facts}</dl>
    <p class="action">{e(ACTIONS[row['action']])}</p>
    <p class="credit">Photo by {e(ev.get('creator') or 'a Mapillary contributor')} {logo} <a href="{e(ev['link'])}">View on Mapillary</a>, <a href="https://creativecommons.org/licenses/by-sa/4.0/">{e(ev['license'])}</a>. Edited: {edited}.</p>
  </div>
</article>"""


def nearby_line(row: dict, logo: str) -> str:
    """A linked survey photo near a record the photos did not settle, with its credit. It settled nothing."""
    near = row.get("nearby_photo")
    if not near:
        return ""
    e = html.escape
    return (f'<p class="credit">A survey photo taken {max(1, round(near["dist_m"]))} m from the record\'s position. '
            f'It may not show the record. Photo by {e(near.get("creator") or "a Mapillary contributor")} {logo} '
            f'<a href="{e(near["link"])}">View on Mapillary</a>, <a href="https://creativecommons.org/licenses/by-sa/4.0/">{e(near["license"])}</a>.</p>')


def _dollars(usd: float) -> str:
    return f"${usd:.2f}"


def _trust(check: dict, survey_cost: float | None = None) -> list[str]:
    trust, totals = check["trust"], check["totals"]
    lines = [f"<strong>Photo dates.</strong> The seen records are from photos taken in {_months(trust['photo_months'])}. "
             "A seen record was there on the photo's date, and may have changed since."]
    if finds := trust.get("findings"):
        lines.append(f"<strong>Findings.</strong> An AI assistant checked {finds['n']} findings against their photos: "
                     f"{finds['yes']} right, {finds['no']} wrong and {finds['unclear']} could not be told.")
    if totals.get("seen_unsure"):
        lines.append(f"<strong>Models unsure.</strong> The models were unsure of {totals['seen_unsure']} of the "
                     f"{totals['seen']} seen records. Those rows say so in the table.")
    lines.append("<strong>Candidates for desk review.</strong> A record is a candidate here when the models were sure of "
                 "it, the AI assistant's check, where there was one, agreed, it was there on the photo's date, and, "
                 "where its city list has a yes or no a photo can answer, that check was settled: either agreement "
                 "with the city or a difference the two models agree on, for human review. It is not a measured "
                 "count of avoided site visits.")
    if attributes := check.get("attribute_checks"):
        left = attributes["unsettled"] + attributes["not_checked"]
        lines.append(f"<strong>Seen in a photo is not the same as checked.</strong> {left} of the {attributes['required']} "
                     f"seen records with an attribute to check had it left open: {attributes['unsettled']} unsettled and "
                     f"{attributes['not_checked']} not checked. Each is a visit row that says which attribute and why, "
                     "because a newer photo or a field check is needed.")
    if compared := trust.get("record_check"):
        lines.append(f"<strong>Records compared.</strong> Asked about {_count(compared['asked'], 'seen record')} whose "
                     f"city list holds a yes or no a photo can answer. The models agreed with the city on "
                     f"{compared['agree']}, two models disagreed with it on {compared['differ']}, and "
                     f"{compared['unsettled']} stayed unsettled. An AI assistant looked at the photos of those that "
                     f"differed and sent {compared['sent_back']} back. This is a prototype of model agreement with "
                     "the city's list, not an independent evaluation.")
    lines.append("<strong>Pairing.</strong> A finding is paired with a city record by the photo's position and viewing "
                 "direction, not by a model comparing the two.")
    for c in trust["controls"]:
        lines.append(f"<strong>Control spots: {html.escape(c['photos'].lower())}.</strong> {_count(c['asked'], 'spot')} with "
                     f"no city record were asked the same way. Two models agreed on an object at {c['both_yes']} of them."
                     f"{' ' + html.escape(c['note']) if c.get('note') else ''}")
    if trust.get("looked_at"):
        lines.append(f"<strong>Second looks.</strong> An AI assistant looked at the {trust['looked_at']} records two models "
                     f"had settled and sent {trust['sent_back']} back to Not determined, because the box sat on something "
                     "farther away than the record.")
    if more := trust.get("second_looks_usd"):
        began = f"The survey cost {_dollars(survey_cost)}. The" if survey_cost is not None else "The"
        end = " more." if survey_cost is not None else "."
        lines.append(f"<strong>Cost.</strong> {began} second looks behind this list, run separately, cost "
                     f"{_dollars(more)}{end}")
    if trust.get("outside_view"):
        lines.append(f"<strong>Left out.</strong> {_count(trust['outside_view'], 'finding')} paired with a city record "
                     "outside the photo's view are counted on neither side.")
    left = check["left_out"]
    if left["repeats"] or left["wrong"]:
        lines.append(f"<strong>Left off the list.</strong> Among the objects not on the city's lists, an AI assistant "
                     f"judged {_count(left['repeats'], 'repeat')} and {_count(left['wrong'], 'wrong object')}.")
    lines.append(f"<strong>Position.</strong> Rows from the city's lists keep the city's position. The "
                 f"{check['actions'].get(COMPARE, 0)} objects not on the lists carry the camera's position, rated Unknown (within 50 ft), "
                 "so some may be records already listed.")
    lines.append("<strong>Scope.</strong> This compares photos with the city's public lists. It changes nothing in the "
                 "city's records, and it is not an inspection. Every row is a suggestion for the city to decide on.")
    return lines


def document(check: dict, area: str | None = None, km: float | None = None, photo_count: int | None = None,
             photo_base: str = "evidence/", survey_cost: float | None = None) -> str:
    """The page, in one file (inline CSS, no scripts). photo_base: where the page finds the evidence photos.
    survey_cost: what the survey itself cost, the scan's own figure, to say beside what the second looks cost."""
    e, totals, logo = html.escape, check["totals"], _logo()
    place = area or check["city"]
    title = f"{place} Record Update List"
    actions, visits = check["actions"], totals["in_view"] - check["no_visit"]
    share = check["no_visit"] / totals["in_view"] * 100 if totals["in_view"] else 0
    meta = [check["city"], *([f"{km:.2f} km of walked streets"] if km else []),
            *([f"{photo_count} public Mapillary photos"] if photo_count else []),
            f"Compared with {_count(len(check['items']), 'city asset list')}"]
    unsettled = (f" The models or the AI assistant could not settle {check['compare_unsure']} of these."
                 if check["compare_unsure"] else "")
    what = {FIX: "The photo and the city's record say different things. Two models agree on the photo.",
            DATE: "Seen in a photo newer than the city's verification date.",
            SAME: "Seen in a photo, with no verification date to update.",
            VISIT: f"{_count(visits, 'city record')} the photos did not settle. Not determined does not mean missing."
                   + (f" {check['visits_attribute_unresolved']} of them were seen in a photo and still have an attribute left open."
                      if check.get("visits_attribute_unresolved") else ""),
            COMPARE: f"On no city list. Compare each with nearby records before adding anything.{unsettled}"}
    shown = [a for a in ORDER if actions.get(a)]
    steps = "\n".join(f"<li><b>{actions[a]}</b><span><strong>{a}</strong>{e(what[a])}</span></li>" for a in shown)
    heads = "".join(f'<th scope="col">{a}</th>' for a in shown)
    table = "\n".join(
        f'<tr><th scope="row">{e(i["asset"])}<small>{e(i["city_list"])}</small></th><td>{i["in_view"]}</td>'
        + "".join(f'<td>{i["actions"].get(a) or "&ndash;"}</td>' for a in shown) + "</tr>"
        for i in check["items"])
    foot = "".join(f"<td>{actions[a]}</td>" for a in shown)
    cards = "".join(_card(r, logo, photo_base) for r in examples(check))
    unknown = next((r for r in check["rows"] if r["status"] == NOT_DETERMINED and r.get("nearby_photo")),
                   next((r for r in check["rows"] if r["status"] == NOT_DETERMINED), None))
    undecided = (f"""<article class="card"><div class="body"><span class="chip visit">{VISIT}</span>
    <h3>{e(unknown['asset'])}</h3><dl><dt>City record</dt><dd>{e(unknown['city_record'])}</dd></dl>
    <p class="action">{ACTIONS[VISIT]}</p>{nearby_line(unknown, logo) if unknown else ""}</div></article>""" if unknown else "")
    columns = "".join(f"<tr><th scope=\"row\">{k}</th><td>{e(v)}</td></tr>" for k, v in COLUMNS)
    candidates = check["no_visit"]
    tail = (f"{candidates} {'is a candidate' if candidates == 1 else 'are candidates'} for desk review and "
            f"{visits} {'needs' if visits == 1 else 'need'} a newer photo or a field check.")
    in_area = totals.get("in_area")
    lead = (e(f"Of {in_area:,} city records in the survey's box or its photos' view, the photos reached {totals['in_view']:,}: {tail} "
              f"The other {in_area - totals['in_view']:,} had no survey photo in view. This list says nothing about them.")
            if in_area is not None else f"Of {totals['in_view']} city records in view of the photos, {tail}")
    trust = "".join(f"<li>{line}</li>" for line in _trust(check, survey_cost))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="icon" href="data:,">
<title>{e(title)}</title><style>{CSS}</style></head><body>
<main>
<p class="sample">Prepared by Fieldless from public street photos. It is not a {e(check['city'])} city document, and the city has not reviewed it.</p>

<header>
  <h1>{e(title)}</h1>
  <p class="meta">{' &middot; '.join(e(m) for m in meta)}</p>
  <p class="lead">{lead}</p>
  <p class="meta">Candidates for desk review are records to look at on a screen first. This is not a measured count of avoided site visits.</p>
  <div class="bar" role="img" aria-label="{check['no_visit']} of {totals['in_view']} city records are candidates for desk review"><span style="width:{share:.1f}%"></span></div>
  <div class="key"><span>{totals['seen']} seen in photo: {e(seen_how(totals))}</span><span>{totals['not_determined']} not determined</span></div>
</header>

<section aria-labelledby="next">
  <h2 id="next">What the asset team could do</h2>
  <ul class="next">{steps}</ul>
</section>

<section aria-labelledby="by-asset">
  <h2 id="by-asset">Results by asset</h2>
  <div class="tablewrap"><table class="counts">
    <thead><tr><th scope="col">Asset</th><th scope="col">City records in view</th>{heads}</tr></thead>
    <tbody>{table}</tbody>
    <tfoot><tr><th scope="row">Total</th><td>{totals['in_view']}</td>{foot}</tr></tfoot>
  </table></div>
</section>

<section aria-labelledby="rows">
  <h2 id="rows">Examples from the table</h2>
  <div class="cards">{cards}{undecided}</div>
</section>

<section aria-labelledby="trust">
  <h2 id="trust">How far to trust this</h2>
  <ul class="trust">{trust}</ul>
</section>

<section aria-labelledby="file">
  <h2 id="file">The table that comes with it</h2>
  <p>One row per object, {len(check['rows'])} rows, as CSV and GeoJSON. Record numbers, addresses and dates come from the city's own lists.</p>
  <div class="tablewrap" style="margin-top:12px"><table class="columns"><tbody>{columns}</tbody></table></div>
</section>

<p class="meta" style="font-size:13px">Street photos: Mapillary contributors, <a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>. {e(check.get('credit') or f"City data: {check['city']} open data.")}</p>
</main></body></html>
"""


def write(check: dict, out_dir: Path, area: str | None = None, km: float | None = None,
          photo_count: int | None = None, survey_cost: float | None = None) -> None:
    """update_list.html, .csv and .geojson, for the survey's zip."""
    out_dir = Path(out_dir)
    (out_dir / "update_list.html").write_text(document(check, area, km, photo_count, survey_cost=survey_cost),
                                              encoding="utf-8")
    (out_dir / "update_list.csv").write_bytes(table_csv(check))
    (out_dir / "update_list.geojson").write_bytes(table_geojson(check))
