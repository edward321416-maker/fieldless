"""The survey report as a document a city official reads and prints (docs/specs/2026-10-29-survey-report-design.md):
a cover, contents, six sections and an appendix, in one self-contained HTML page (inline CSS and SVG, no scripts).

Muse Spark's sentences (assist.report.narrate) go where they were kept; everywhere else code writes its own sentence
from the counts, so the report always comes out. Every number in the page comes from the summary. The report states
what this survey found and what it cost; it does not compare itself with surveys on foot (user decision 2026-10-29,
docs/ui-rules.md U1). Every photo carries its creator, Mapillary link and licence (Mapillary's terms)."""
import html
import math

from assist import style

M_PER_DEG = 111_320.0
MAP_PAD = 24
MAP_MAX_H = 420
SCALE_BAR_H = 22
SCALES_M = (10, 20, 50, 100, 200, 500, 1000, 2000)
WALK_INK = "#475569"
STATUS_ORDER = ("auto", "needs_review", "field_check", "verified", "rejected")
STATUS_WORDS = {"auto": "found by the models", "needs_review": "awaiting review", "field_check": "check on site",
                "verified": "confirmed", "rejected": "rejected"}
HOW_MADE = ("SAM 3.1 found each survey item in public Mapillary street photos, and Muse Spark judged each one on the "
            "best photo against the item's question. Defect findings were judged again by Muse Spark Open. What the "
            "models could not settle is marked for review or a site visit.")
SECTIONS = (("summary", "1. Summary"), ("scope", "2. Scope and method"), ("findings", "3. Findings by item"),
            ("actions", "4. Actions"), ("ledger", "5. Photo ledger"), ("limits", "6. Limits"),
            ("appendix", "Appendix: data and credits"))

CSS = """
:root { color-scheme: light; --ink: #1d232a; --muted: #5b6672; --line: #d5dbe1; --head: #eef2f5; --red: #B91C1C;
        --paper: #ffffff; --desk: #e9edf1; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 12px; background: var(--desk); color: var(--ink);
       font: 14px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 820px; margin: 0 auto; background: var(--paper); padding: 40px 48px;
       box-shadow: 0 1px 4px rgba(0, 0, 0, .12); }
h1 { font-size: 28px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 0 0 12px; padding-bottom: 6px; border-bottom: 2px solid var(--ink); }
h3 { font-size: 15px; margin: 18px 0 6px; display: flex; align-items: center; gap: 8px; }
p { margin: 6px 0; }
.muted { color: var(--muted); }
.cover { min-height: 60vh; display: flex; flex-direction: column; justify-content: center; }
.cover .area { font-size: 20px; margin: 0 0 24px; }
.cover dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0 0 24px; }
.cover dt { color: var(--muted); }
.cover dd { margin: 0; }
.toc ol { list-style: none; padding-left: 0; }
.sec { margin-top: 36px; }
.spark { border-left: 3px solid var(--line); padding-left: 12px; }
.table { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 6px 0; }
th, td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: var(--head); }
.repair td:first-child { border-left: 3px solid var(--red); }
.map { border: 1px solid var(--line); margin: 8px 0; }
.ledger { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr)); gap: 16px; }
.entry { margin: 0; border: 1px solid var(--line); padding: 8px; }
.ph { position: relative; }
.ph img { display: block; width: 100%; height: auto; }
.ph .box { position: absolute; border: 2px solid; }
.entry figcaption { font-size: 12px; margin-top: 6px; }
.credit { color: var(--muted); }
a { color: #0b5cad; }
@media (max-width: 600px) { main { padding: 20px 16px; } }
@page { size: A4; margin: 14mm; }
@media print {
  body { background: var(--paper); padding: 0; }
  main { box-shadow: none; padding: 0; max-width: none; }
  .cover { min-height: 0; }
  .sec { break-before: page; }
  .entry, tr, h3 { break-inside: avoid; }
}
"""


def _h(text) -> str:
    return html.escape("" if text is None else str(text))


def _duration(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


def _table(headers, rows: list[list[str]], row_class: str = "") -> str:
    """A table whose cells are already HTML."""
    head = "".join(f'<th scope="col">{_h(h)}</th>' for h in headers)
    cls = f' class="{row_class}"' if row_class else ""
    body = "".join(f"<tr{cls}>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _marker(s: dict, item: str, size: int = 18) -> str:
    st = s.get("styles", {}).get(item)
    return style.marker_svg(st, size=size) if st else ""


def _name(title: str) -> str:
    """An item's name as the app writes it (web/src/items.ts itemName): its target with a capital (U12)."""
    return title[:1].upper() + title[1:]


def _color(s: dict, item: str) -> str:
    return (s.get("styles", {}).get(item) or {}).get("color", "#4D4D4D")


# --- code's own sentences ---------------------------------------------------------------------------------------

def summary_text(s: dict) -> str:
    """The summary in code's words, when Muse Spark's is missing or was dropped."""
    t, a = s["totals"], s["actions"]
    dates = s["photo_dates"]
    taken = f" taken {dates['from']} to {dates['to']}" if dates else ""
    found = [i["title"] for i in s["items"] if i["found"]]
    what = ", ".join(found) if found else "no survey item"
    if s.get("city_check"):  # a difference from a list is not an error in it: either side may be the one missing
        new = sum(c["not_in_city"] for c in a["city"])
        unconfirmed = sum(c["not_seen"] for c in a["city"])
        city = (f" and, against the city's lists, {new} {_plural(new, 'find')} the lists do not have and "
                f"{unconfirmed} listed {_plural(unconfirmed, 'point')} the photos did not confirm")
    else:
        city = ""
    return (f"This survey covered {s['km']} km of street from {s['photos']} public street photos{taken}. It recorded "
            f"{t['found']} findings ({what}). {len(a['repairs'])} possible repairs, {len(a['site_visits'])} findings "
            f"for a site visit{city} are listed in section 4 for a check.")


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


def item_text(i: dict) -> str:
    """One item's paragraph in code's words, when Muse Spark wrote none for it."""
    if not i["found"]:
        return f"The survey found no {i['title']} in the photos."
    results = "; ".join(f"{n} × {label}" for label, n in i["by_label"].items())
    return f"The survey recorded {i['found']} {i['title']} findings: {results}."


# --- map ------------------------------------------------------------------------------------------------------

def svg_map(walks: list[list[tuple[float, float]]], ledger: list[dict], styles: dict, width: int = 640) -> str:
    """The surveyed walks (photo positions joined in order) and one dot per finding in its item's colour, on plain
    paper: no map tiles, so the page prints and opens offline. A scale bar and a north arrow; dots stand at the photo
    positions."""
    points = [(e["lat"], e["lng"]) for e in ledger if e["lat"] is not None and e["lng"] is not None]
    every = [p for w in walks for p in w] + points
    if not every:
        return ""
    lats, lngs = [p[0] for p in every], [p[1] for p in every]
    kx = math.cos(math.radians((min(lats) + max(lats)) / 2)) * M_PER_DEG
    span_x = max((max(lngs) - min(lngs)) * kx, 50.0)
    span_y = max((max(lats) - min(lats)) * M_PER_DEG, 50.0)
    scale = min((width - 2 * MAP_PAD) / span_x, (MAP_MAX_H - 2 * MAP_PAD) / span_y)  # px per metre
    height = round(span_y * scale + 2 * MAP_PAD + SCALE_BAR_H)  # the scale bar sits below the drawing
    width = round(span_x * scale + 2 * MAP_PAD)  # a tall area keeps its shape instead of leaving blank paper

    def xy(lat: float, lng: float) -> tuple[float, float]:
        return MAP_PAD + (lng - min(lngs)) * kx * scale, MAP_PAD + (max(lats) - lat) * M_PER_DEG * scale

    parts = []
    for w in walks:
        if len(w) > 1:
            line = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(*p) for p in w))
            parts.append(f'<polyline points="{line}" fill="none" stroke="{WALK_INK}" stroke-width="3" '
                         'stroke-linecap="round" stroke-linejoin="round" opacity="0.6"/>')
    for e in ledger:
        if e["lat"] is None or e["lng"] is None:
            continue
        x, y = xy(e["lat"], e["lng"])
        ring = style.DEFECT_RED if e["defect"] else "#ffffff"
        fill = (styles.get(e["item"]) or {}).get("color", "#4D4D4D")
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{fill}" stroke="{ring}" stroke-width="2"/>')
    bar = max([m for m in SCALES_M if m * scale <= max(width - 2 * MAP_PAD, 120) / 2] or [SCALES_M[0]])
    y0 = height - 8
    parts.append(f'<line x1="{MAP_PAD}" y1="{y0}" x2="{MAP_PAD + bar * scale:.1f}" y2="{y0}" stroke="#1d232a" '
                 f'stroke-width="2"/><text x="{MAP_PAD}" y="{y0 - 4}" font-size="11" fill="#1d232a">{bar} m</text>')
    parts.append(f'<text x="{width - MAP_PAD}" y="{y0}" font-size="13" font-weight="700" text-anchor="end" '
                 'fill="#1d232a">N ↑</text>')
    return (f'<svg class="map" viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" role="img" '
            f'aria-label="Map of the surveyed walks and the findings">{"".join(parts)}</svg>')


# --- sections -------------------------------------------------------------------------------------------------

def _cover(s: dict) -> str:
    dates = s["photo_dates"]
    taken = f"{dates['from']} to {dates['to']}" if dates else "unknown"
    rows = [("Survey standard", s["standard"]), ("Photos taken", taken), ("Street surveyed", f"{s['km']} km"),
            ("Report date", s["made"]), ("Prepared by", "Fieldless, from public Mapillary street photos")]
    toc = "".join(f'<li><a href="#{key}">{_h(title)}</a></li>' for key, title in SECTIONS)
    return ('<section class="cover"><h1>Street Survey Report</h1>'
            f'<p class="area">{_h(s["area"])}</p><dl>'
            + "".join(f"<dt>{_h(k)}</dt><dd>{_h(v)}</dd>" for k, v in rows)
            + f'</dl><nav class="toc" aria-label="Contents"><b>Contents</b><ol>{toc}</ol></nav></section>')


def _section(key: str, body: str) -> str:
    title = dict(SECTIONS)[key]
    return f'<section class="sec" id="{key}"><h2>{_h(title)}</h2>{body}</section>'


def _summary(s: dict, notes: dict | None) -> str:
    text = (notes or {}).get("summary")
    body = (f'<div class="spark"><p>{_h(text)}</p><p class="muted">Written by Muse Spark from the survey\'s counts; '
            "every number in this report comes from the survey data.</p></div>" if text
            else f"<p>{_h(summary_text(s))}</p>")
    return _section("summary", body)


def _scope(s: dict) -> str:
    dates = s["photo_dates"]
    taken = f", taken {dates['from']} to {dates['to']}" if dates else ""
    questions = "".join(f"<li>{_marker(s, i['item'], 16)} <b>{_h(_name(i['title']))}</b>"
                        f"{': ' + _h(i['question']) if i['question'] else ''}</li>" for i in s["items"])
    body = (svg_map(s["walks"], s["ledger"], s.get("styles", {}))
            + '<p class="muted">Grey lines: the walks the photos were taken on. Dots: findings, at the position of '
              "their photo, in the item's colour; a red ring marks a possible defect.</p>"
            + f"<p><b>Area.</b> {_h(s['area'])}: {s['km']} km of street, {s['photos']} photos{_h(taken)}.</p>"
            + f"<p><b>Method.</b> {_h(HOW_MADE)}</p>"
            + f"<p><b>Survey items ({_h(s['standard'])} standard)</b> and the question asked of each photo:</p>"
            + f"<ul>{questions}</ul>"
            + f"<p><b>This survey's processing.</b> {_duration(s['processing_seconds'])} of processing; model cost "
              f"${s['cost_usd']:.2f}.</p>")
    return _section("scope", body)


def _status_cells(i: dict) -> str:
    """The found ones by status; rejections are not among them, so they come after, said apart."""
    counted = ", ".join(f"{i['by_review'][k]} {STATUS_WORDS[k]}" for k in STATUS_ORDER
                        if k != "rejected" and i["by_review"].get(k)) or "-"
    rejected = i["by_review"].get("rejected", 0)
    return f"{counted}; {rejected} more rejected by a person (not counted)" if rejected else counted


def _findings(s: dict, notes: dict | None) -> str:
    written = (notes or {}).get("items") or {}
    blocks = []
    for i in s["items"]:
        text = written.get(i["item"])
        para = f'<p class="spark">{_h(text)}</p>' if text else f"<p>{_h(item_text(i))}</p>"
        results = "; ".join(f"{n} × {label}" for label, n in i["by_label"].items()) or "-"
        city = (f"{i['city']['found']} of {i['city']['covered']} in the city's list" if i.get("city") else "-")
        table = _table(["Found", "Results", "Status", "City record"],
                       [[str(i["found"]), _h(results), _h(_status_cells(i)), _h(city)]])
        blocks.append(f'<article class="item" id="item-{_h(i["item"])}"><h3>{_marker(s, i["item"])} '
                      f"{_h(_name(i['title']))}</h3>{para}{table}</article>")
    cc = s.get("city_check")
    note = (f'<p class="muted">City record: {_h(cc["method"])} Measured {_h(cc["measured_at"])}. '
            f'<a href="{_h(cc["method_url"])}">Method</a></p>' if cc else
            '<p class="muted">No city record was checked against this survey.</p>')
    return _section("findings", "".join(blocks) + note)


def _photo_link(e: dict) -> str:
    return f'<a href="#f-{_h(e["row_id"])}">{_h(e["row_id"])}</a>'


def _actions(s: dict, notes: dict | None) -> str:
    a = s["actions"]
    repairs = (_table(["Item", "Finding", "Status", "Photo"],
                      [[_h(_name(e["title"])), _h(e["label"]), _h(e["status"]), _photo_link(e)]
                       for e in a["repairs"]],
                      row_class="repair") if a["repairs"] else "<p>None.</p>")
    visits = (_table(["Item", "Finding", "Status", "Photo"],
                     [[_h(_name(e["title"])), _h(e["label"]), _h(e["status"]), _photo_link(e)]
                      for e in a["site_visits"]],
                     row_class="visit") if a["site_visits"] else "<p>None.</p>")
    city = (_table(["Item", "In the photos, not in the city's list", "In the city's list, not found by this survey"],
                   [[_h(_name(c["title"])), str(c["not_in_city"]), str(c["not_seen"])] for c in a["city"]])
            if a["city"] else ("<p>None.</p>" if s.get("city_check") else
                               "<p>No city record was checked against this survey.</p>"))
    titles = {i["item"]: _name(i["title"]) for i in s["items"]}
    prio = (notes or {}).get("priorities") or []
    priorities = ("<ol>" + "".join(f"<li><b>{_h(titles.get(p['item'], p['item']))}</b>: {_h(p['reason'])}</li>"
                                   for p in prio) + '</ol><p class="muted">Ordered by Muse Spark from the counts.</p>'
                  if prio else "<p>See 4.1 to 4.3.</p>")
    body = (f"<h3>4.1 Repairs</h3>{repairs}<h3>4.2 Site visits</h3>{visits}"
            f"<h3>4.3 City records to check</h3>{city}<h3>4.4 Priorities</h3>{priorities}")
    return _section("actions", body)


def _entry(s: dict, e: dict, photo_base: str) -> str:
    if e["evidence"]:
        box = ""
        if e["box"]:
            x1, y1, x2, y2 = (v / 10 for v in e["box"])
            box = (f'<span class="box" style="left:{x1:.1f}%;top:{y1:.1f}%;width:{x2 - x1:.1f}%;'
                   f'height:{y2 - y1:.1f}%;border-color:{_color(s, e["item"])}"></span>')
        photo = (f'<div class="ph"><img src="{_h(photo_base + e["evidence"])}" loading="lazy" '
                 f'alt="{_h(_name(e["title"]))}: {_h(e["label"])}">{box}</div>')
    else:
        photo = "<p>no photo</p>"
    who = e["creator"] or "a Mapillary contributor"
    credit = (f'<span class="credit">Photo by {_h(who)}, <a href="{_h(e["photo_url"])}">Mapillary</a>, '
              f'{_h(e["license"])}</span>' if e["photo_url"] else f'<span class="credit">{_h(e["license"])}</span>')
    where = f"{e['lat']:.5f}, {e['lng']:.5f}" if e["lat"] is not None else "unknown"
    note = f"<br>Model note: {_h(e['note'])}" if e["note"] else ""
    taken = (e["captured_at"] or "")[:10] or "unknown"
    return (f'<figure class="entry" id="f-{_h(e["row_id"])}">{photo}<figcaption>{_marker(s, e["item"], 14)} '
            f"<b>{_h(_name(e['title']))}</b>: {_h(e['label'])} · {_h(e['status'])}<br>Taken {_h(taken)} · "
            f"photo position "
            f"{_h(where)}{note}<br>{credit}</figcaption></figure>")


def _ledger(s: dict, photo_base: str) -> str:
    entries = "".join(_entry(s, e, photo_base) for e in s["ledger"])
    return _section("ledger", f'<div class="ledger">{entries}</div>' if entries else "<p>No findings.</p>")


def _limits(lines: list[str]) -> str:
    return _section("limits", "<ul>" + "".join(f"<li>{_h(line)}</li>" for line in lines) + "</ul>")


def _appendix(s: dict) -> str:
    body = (f"<p>The survey's files come with its download: survey_scan{s['scan_id']}.geojson (every finding as a "
            f"point), survey_scan{s['scan_id']}_&lt;item&gt;.csv (one table per item) and the photos.</p>"
            "<p>Street photos: Mapillary contributors, CC BY-SA 4.0; people and licence plates are blurred. "
            "Survey by Fieldless.</p>")
    return _section("appendix", body)


def document(s: dict, notes: dict | None = None, photo_base: str = "evidence/", limits: list[str] | None = None) -> str:
    """The whole report as one HTML page. photo_base: where the evidence photos are, seen from the page
    ("evidence/" in the zip, "../../evidence/" in the app and on the static site). limits: the sentences of section
    6 (assist.report passes its own)."""
    parts = [_cover(s), _summary(s, notes), _scope(s), _findings(s, notes), _actions(s, notes),
             _ledger(s, photo_base), _limits(limits or []), _appendix(s)]
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>Street Survey Report — {_h(s['area'])}</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main></body></html>\n")
