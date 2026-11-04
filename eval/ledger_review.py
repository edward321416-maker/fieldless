"""Human-reviewed ledger changes: the raw source snapshot each Asset Check row carries, the check of a reviewer's
journal against it, and the files a city's record keeper takes.

    LedgerSource   row["ledger_source"], made by eval.asset_check from the city's own record (make_source).
    LedgerJournal  {"schema_version": 1, "scan_id": N, "decisions": [...]}, written by the app's review pane.
    validate_journal(check, journal) -> the approved changes (raises LedgerError, listing every problem, otherwise)
    changes_csv / observations_csv(check, journal) -> the two files a record keeper opens
    python -m eval.ledger_apply ...  applies the approved changes to a NEW copy of the city's GeoJSON folder

What a decision is worth: a reviewer typed a name, a reason and a time into the app. The name is the reviewer's own
statement, never an authenticated municipal approval, and nothing here writes to a city's system. A photo's date is
a photo date: it is never written into a city's inspection or verification date, and no field but the one yes/no field
a city's file names (`[items.<id>.compare]`) can change.
"""
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from pipeline import geo

SCHEMA_VERSION = 1
SOURCE_VERSION = 1
NEIGHBOR_M = 15.0  # same item, different record, this close by the city's positions ...
NEIGHBOR_S = 10.0  # ... and photographed this close in time: ask the reviewer, do not decide for them
LAYER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.geojson")
PROTECTED_PARTS = ("date", "verif", "inspect", "install", "updated")
PROTECTED_TOKENS = {"id", "objectid", "compkey", "unitid", "lat", "latitude", "lng", "lon", "long", "longitude",
                    "x", "y", "geom", "geometry", "coordinates", "shape"}
CONFIG_FIELDS = ("record_field", "checked_field", "installed_field", "place_field", "condition_field")


def canonical_json(value) -> str:
    """The one JSON text a value hashes as: sorted keys, no spaces, characters as they are."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_of(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def properties_sha256(props: dict) -> str:
    """The hash of ALL of a city record's original properties, so a record edited since the export no longer matches."""
    return sha256_of(props)


def layer_basename(layer) -> str | None:
    """The file name of a city layer when its path is a plain relative path ending in a plain *.geojson name."""
    if not isinstance(layer, str) or not layer.strip():
        return None
    parts = PurePosixPath(layer.replace("\\", "/")).parts
    if not parts or parts[0] == "/" or any(p in ("..", ".") or ":" in p for p in parts):
        return None
    return parts[-1] if LAYER_NAME.fullmatch(parts[-1]) else None


def is_protected(field, cfg: dict) -> bool:
    """A field a reviewer may never change: ids, positions, dates (a photo date is not an inspection date) and any
    field the city's file uses to name, place or date a record."""
    if not isinstance(field, str) or not field:
        return True
    low = field.lower()
    if any(part in low for part in PROTECTED_PARTS):
        return True
    if set(re.split(r"[^a-z0-9]+", low)) & PROTECTED_TOKENS:
        return True
    named = {field_name for key in CONFIG_FIELDS if isinstance(field_name := cfg.get(key), str)}
    return field in named or field in (cfg.get("id_fields") or [])


def record_text(value) -> str:
    """A record id the way the Asset Check shows it."""
    return str(value)


def editable_fields(cfg: dict, props: dict, unique: bool) -> dict:
    """The fields a reviewer may change for one record: the configured yes/no field, only when the record's own
    value is one of the two raw values and the record's id names exactly one record of its layer."""
    compare = cfg.get("compare")
    if not unique or not isinstance(compare, dict):
        return {}
    field, yes, no = compare.get("field"), compare.get("yes"), compare.get("no")
    if is_protected(field, cfg) or not isinstance(yes, str) or not isinstance(no, str) or yes == no:
        return {}
    before = props.get(field)
    if not isinstance(before, str) or before not in (yes, no):
        return {}
    return {field: {"before": before, "yes": yes, "no": no, "question": str(compare.get("ask") or ""),
                    "suggested": None}}


def photo_of(evidence: dict | None) -> dict | None:
    """What of a row's photo the review key binds: its Mapillary link (it names the photo) and its capture time."""
    if not evidence:
        return None
    return {"link": evidence.get("link"), "captured_at": evidence.get("captured_at")}


def position_sha256(geometry) -> str | None:
    """The hash of a record's exact source point, [lng, lat] as the city's file holds it (not a rounded display
    position). None for a record without a point, which a snapshot then cannot bind."""
    coords = geometry.get("coordinates") if isinstance(geometry, dict) and geometry.get("type") == "Point" else None
    if not isinstance(coords, list) or len(coords) < 2 or not all(
            isinstance(c, (int, float)) and not isinstance(c, bool) for c in coords[:2]):
        return None
    try:
        return sha256_of([coords[0], coords[1]])
    except ValueError:
        return None


def review_key(scan_id, item: str, layer: str, record_key: str, record_id: str, props_sha: str,
               evidence: dict | None, box, position: str | None = None) -> str:
    """The key a decision names its row by. A snapshot without a position hash (made before positions were bound)
    keys as it always did."""
    return sha256_of({"v": SOURCE_VERSION, "scan": scan_id, "item": item, "layer": layer, "record_key": record_key,
                      "record_id": record_id, "properties_sha256": props_sha, "photo": photo_of(evidence),
                      "box": box, **({"position_sha256": position} if position else {})})


def make_source(scan_id, item: str, cfg: dict, feature: dict, features: list[dict], evidence: dict | None,
                box) -> dict | None:
    """The LedgerSource of one city record (feature), or None when the row cannot be traced to a safe layer file and
    a record id (a city file without a layer, an unsafe path, a record without its id). features: every feature of
    the layer."""
    props = feature["properties"]
    layer = layer_basename(cfg.get("layer"))
    key = cfg.get("record_field")
    if layer is None or not isinstance(key, str) or not props.get(key):
        return None
    try:
        sha = properties_sha256(props)
    except (TypeError, ValueError):
        return None
    record_id = record_text(props[key])
    same = sum(record_text((f.get("properties") or {}).get(key)) == record_id for f in features)
    position = position_sha256(feature.get("geometry"))
    return {"version": SOURCE_VERSION, "layer": layer, "record_key": key, "record_id": record_id,
            "properties_sha256": sha, **({"position_sha256": position} if position else {}),
            "review_key": review_key(scan_id, item, layer, key, record_id, sha, evidence, box, position),
            "editable_fields": editable_fields(cfg, props, same == 1),
            "identity_warnings": []}


def suggest(source: dict, attribute: dict | None) -> None:
    """Where the photo contradicts the record's yes/no field, offer the other raw value as the reviewer's starting
    point. It is a hint to read, never a decision."""
    if not source or not attribute or attribute.get("state") != "different":
        return
    for spec in source["editable_fields"].values():
        spec["suggested"] = spec["no"] if spec["before"] == spec["yes"] else spec["yes"]


def seconds_of(value) -> float | None:
    """A photo's capture time in seconds, from an ISO text or epoch seconds or milliseconds; None when unreadable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    if isinstance(value, str) and value.strip():
        try:
            when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()
    return None


def neighbor_pairs(rows: list[dict]) -> dict[str, list[dict]]:
    """For rows that carry a LedgerSource: by review key, each other record of the SAME item that is at most
    NEIGHBOR_M metres away by the city's positions and photographed at most NEIGHBOR_S seconds apart, as
    {"record_id", "metres", "seconds"}. A prompt to check that the reviewer is looking at the right object, never a
    verdict that two records are one."""
    found = []
    for row in rows:
        src = row.get("ledger_source")
        when = seconds_of((row.get("evidence") or {}).get("captured_at"))
        if src and when is not None and isinstance(row.get("lat"), (int, float)) and isinstance(row.get("lng"), (int, float)):
            found.append((row, src, when))
    out: dict[str, list[dict]] = {src["review_key"]: [] for _, src, _ in found}
    for n, (a, src_a, when_a) in enumerate(found):
        for b, src_b, when_b in found[n + 1:]:
            if a["item"] != b["item"] or src_a["record_id"] == src_b["record_id"]:
                continue
            metres = geo.haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
            gap = abs(when_a - when_b)
            if metres <= NEIGHBOR_M and gap <= NEIGHBOR_S:
                for mine, theirs in ((src_a, src_b), (src_b, src_a)):
                    out[mine["review_key"]].append({"record_id": theirs["record_id"], "metres": metres, "seconds": gap})
    return out


def warning_line(near: dict) -> str:
    return (f"Record {near['record_id']} of the same item is {near['metres']:.1f} m away and was photographed "
            f"{near['seconds']:.0f} s from this one: check these are two different objects.")


def neighbor_warnings(rows: list[dict]) -> dict[str, list[str]]:
    """neighbor_pairs as one line each, for the reviewer to read."""
    return {key: [warning_line(n) for n in near] for key, near in neighbor_pairs(rows).items()}


def add_neighbor_warnings(rows: list[dict]) -> dict[str, list[dict]]:
    """Put each row's warnings in its LedgerSource. Returns the structured pairs they were made from, by review key."""
    pairs = neighbor_pairs(rows)
    for row in rows:
        if src := row.get("ledger_source"):
            src["identity_warnings"] = [warning_line(n) for n in pairs.get(src["review_key"], [])]
    return pairs


# --- the journal ---------------------------------------------------------------------------------------------------

ASSERTION = "Self-asserted by the reviewer in the app; not an authenticated municipal approval."
APPLICATION_STATUS = "Prepared only. Not applied to any official ledger."
DATE_MEANING = "Date of the photo (an observation). Not the city's inspection or verification date."
IDENTITIES = ("confirmed", "uncertain", "different")
EVIDENCES = ("clear", "unclear")
DECISIONS = ("change", "observe", "hold")
JOURNAL_KEYS = {"schema_version", "scan_id", "decisions"}
DECISION_KEYS = {"review_key", "record_id", "item", "identity", "evidence", "decision", "field", "proposed_value",
                 "neighbors_checked", "reviewer", "note", "reviewed_at"}
OPTIONAL_KEYS = {"field", "proposed_value"}
REVIEWER_MAX, NOTE_MAX = 200, 2000
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "\n")
# The columns, their order and the three fixed statements are the ones the app's own files have
# (web/src/ledgerReview.ts); tests/fixtures/ledger_interop holds files both sides must reproduce byte for byte.
CHANGE_COLUMNS = ("review_key", "record_id", "item", "layer", "field", "before", "after", "identity", "evidence",
                  "neighbors_checked", "reviewer", "reviewer_assertion", "note", "reviewed_at", "application_status",
                  "photo_observed_on", "date_meaning", "photo_creator", "photo_link", "photo_license", "scan_id",
                  "record_key", "properties_sha256")
OBSERVATION_COLUMNS = ("review_key", "record_id", "item", "layer", "photo_observed_on", "date_meaning", "photo_creator",
                       "photo_link", "photo_license", "identity", "evidence", "neighbors_checked", "reviewer",
                       "reviewer_assertion", "note", "reviewed_at", "application_status", "scan_id", "record_key",
                       "properties_sha256")
BOM = "\ufeff"  # one, at the start of the CSV text: Excel then reads non-ASCII names and notes as UTF-8


class LedgerError(ValueError):
    """A check or journal that must not be used. problems: every reason found, so a reviewer fixes them together."""

    def __init__(self, problems):
        self.problems = [str(p) for p in problems]
        super().__init__("; ".join(self.problems))


def _text(value, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _time_ok(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _photo_day(evidence: dict) -> str:
    seconds = seconds_of(evidence.get("captured_at"))
    return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat() if seconds is not None \
        else str(evidence.get("captured_at") or "")[:10]


def _rows_by_key(check) -> tuple[dict[str, dict], list[str]]:
    """The check's rows that carry a LedgerSource, by review key, after the key of each is recomputed from the row:
    a check edited after it was made (a box, a photo, a snapshot hash) no longer matches its keys."""
    problems: list[str] = []
    if not isinstance(check, dict) or not isinstance(check.get("rows"), list):
        return {}, ["check is not an Asset Check with rows"]
    scan = check.get("scan_id")
    rows: dict[str, dict] = {}
    for row in check["rows"]:
        src = row.get("ledger_source") if isinstance(row, dict) else None
        if not src:
            continue
        try:
            expected = review_key(scan, row["item"], src["layer"], src["record_key"], src["record_id"],
                                  src["properties_sha256"], row.get("evidence"), row.get("box"),
                                  src.get("position_sha256"))
        except (KeyError, TypeError, ValueError):
            problems.append("check does not match its review keys: a row has a malformed ledger_source")
            continue
        if src.get("review_key") != expected:
            problems.append(f"check does not match its review keys (record {src.get('record_id')})")
        elif expected in rows:
            problems.append(f"check has a duplicate review_key (record {src['record_id']})")
        else:
            rows[expected] = row
    return rows, problems


def _warned(rows: dict[str, dict], key: str) -> list[str]:
    """The neighbour warnings of a row as the check states them, else as its own rows imply."""
    stored = [w for w in rows[key]["ledger_source"].get("identity_warnings") or [] if isinstance(w, str)]
    return stored or neighbor_warnings(list(rows.values())).get(key, [])


def _decision(n: int, d, rows: dict, check: dict, seen: set, fields: set, problems: list[str]) -> dict | None:
    """One decision as a normalised dict, or None with its problems added."""
    if not isinstance(d, dict):
        problems.append(f"decision {n} is not an object")
        return None
    label = f"decision {n}"
    fresh = [f"{label}: unknown key {k!r}" for k in sorted(set(d) - DECISION_KEYS)]
    fresh += [f"{label}: missing key {k!r}" for k in sorted(DECISION_KEYS - OPTIONAL_KEYS - set(d))]
    key = d.get("review_key")
    row = rows.get(key) if isinstance(key, str) else None
    if row is None:
        problems += fresh + [f"{label}: unknown review_key (not in this Asset Check, or its source has changed)"]
        return None
    src = row["ledger_source"]
    label = f"decision {n} ({src['record_id']})"
    if key in seen:
        fresh.append(f"{label}: duplicate review_key")
    seen.add(key)
    if d.get("record_id") != src["record_id"] or d.get("item") != row["item"]:
        fresh.append(f"{label}: record_id or item does not match its row")
    kind = d.get("decision")
    if kind != "change" and (d.get("field") is not None or d.get("proposed_value") is not None):
        fresh.append(f"{label}: field and proposed_value must be null unless the decision is a change")
    for name, allowed in (("identity", IDENTITIES), ("evidence", EVIDENCES), ("decision", DECISIONS)):
        if d.get(name) not in allowed:
            fresh.append(f"{label}: {name} must be one of {', '.join(allowed)}")
    if not _text(d.get("reviewer"), REVIEWER_MAX):
        fresh.append(f"{label}: reviewer is required (text, at most {REVIEWER_MAX} characters)")
    if not _text(d.get("note"), NOTE_MAX):
        fresh.append(f"{label}: note is required (the reason, text, at most {NOTE_MAX} characters)")
    if not _time_ok(d.get("reviewed_at")):
        fresh.append(f"{label}: reviewed_at must be an ISO-8601 time with a time zone")
    if not isinstance(d.get("neighbors_checked"), bool):
        fresh.append(f"{label}: neighbors_checked must be true or false")
    evidence = row.get("evidence") or {}
    if kind in ("change", "observe"):
        if d.get("identity") != "confirmed":
            fresh.append(f"{label}: identity must be confirmed to {kind}")
        if d.get("evidence") != "clear":
            fresh.append(f"{label}: evidence must be clear to {kind}")
        if not (_text(evidence.get("captured_at"), 100) or isinstance(evidence.get("captured_at"), (int, float))) \
                or not _text(evidence.get("link"), 2000):
            fresh.append(f"{label}: this row has no photo evidence to {kind} from")
        if warnings := _warned(rows, key):
            if d.get("neighbors_checked") is not True:
                fresh.append(f"{label}: neighbors_checked must be true: {warnings[0]}")
    if kind == "change":
        field = d.get("field")
        spec = src.get("editable_fields", {}).get(field) if isinstance(field, str) else None
        if not isinstance(spec, dict) or is_protected(field, {}):
            fresh.append(f"{label}: {field!r} is not an editable field of this record")
        else:
            value = d.get("proposed_value")
            if not isinstance(value, str) or value not in (spec["yes"], spec["no"]):
                fresh.append(f"{label}: proposed_value {value!r} is not an allowed value "
                             f"({spec['yes']!r} or {spec['no']!r})")
            elif value == spec["before"]:
                fresh.append(f"{label}: no change, the record already says {spec['before']!r}")
            else:
                spot = (src["layer"], src["record_id"], field)
                if spot in fields:
                    fresh.append(f"{label}: conflicting changes to {field} of record {src['record_id']}")
                fields.add(spot)
    problems += fresh
    if fresh:
        return None
    out = {"review_key": key, "scan_id": check["scan_id"], "item": row["item"], "layer": src["layer"],
           "record_key": src["record_key"], "record_id": src["record_id"],
           "properties_sha256": src["properties_sha256"], "position_sha256": src.get("position_sha256"),
           "identity": d["identity"], "evidence": d["evidence"],
           "neighbors_checked": d["neighbors_checked"], "reviewer": d["reviewer"], "reviewer_assertion": ASSERTION,
           "note": d["note"], "reviewed_at": d["reviewed_at"], "application_status": APPLICATION_STATUS,
           "decision": kind}
    if kind != "hold":
        out |= {"photo_observed_on": _photo_day(evidence), "date_meaning": DATE_MEANING, "photo_link": evidence.get("link"),
                "photo_creator": evidence.get("creator") or "", "photo_license": evidence.get("license") or ""}
    if kind == "change":
        out |= {"field": d["field"], "before": src["editable_fields"][d["field"]]["before"],
                "after": d["proposed_value"]}
    return out


def review(check, journal) -> dict[str, list[dict]]:
    """Check a journal against an Asset Check. Returns {"changes", "observations", "holds"}, each decision as a
    normalised dict, or raises LedgerError naming every problem: one invalid decision rejects the whole journal."""
    rows, problems = _rows_by_key(check)
    if not isinstance(journal, dict):
        raise LedgerError(problems + ["journal is not an object"])
    problems += [f"journal: unknown key {k!r}" for k in sorted(set(journal) - JOURNAL_KEYS)]
    version = journal.get("schema_version")
    if version != SCHEMA_VERSION or isinstance(version, bool):
        problems.append(f"journal: schema_version must be {SCHEMA_VERSION}")
    scan = journal.get("scan_id")
    if isinstance(check, dict) and (scan != check.get("scan_id") or isinstance(scan, bool)):
        problems.append(f"journal: scan_id {scan!r} is not the check's scan {check.get('scan_id')!r}")
    decisions = journal.get("decisions")
    if not isinstance(decisions, list):
        raise LedgerError(problems + ["journal: decisions must be a list"])
    seen: set = set()
    fields: set = set()
    done = [_decision(n, d, rows, check, seen, fields, problems) for n, d in enumerate(decisions, 1)]
    if problems:
        raise LedgerError(problems)
    return {"changes": [d for d in done if d["decision"] == "change"],
            "observations": [d for d in done if d["decision"] == "observe"],
            "holds": [d for d in done if d["decision"] == "hold"]}


def validate_journal(check: dict, journal: dict) -> list[dict]:
    """The approved changes of a journal (decision "change"), or LedgerError."""
    return review(check, journal)["changes"]


def observations(check: dict, journal: dict) -> list[dict]:
    """The photo observations of a journal (decision "observe"): what a photo showed, on the photo's date."""
    return review(check, journal)["observations"]


def holds(check: dict, journal: dict) -> list[dict]:
    return review(check, journal)["holds"]


def cell(value) -> str:
    """One CSV cell as text a spreadsheet will not run: a leading = + - @ tab or line break gets an apostrophe."""
    if value is None:
        return ""
    text = "true" if value is True else "false" if value is False else str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def to_csv(columns: tuple[str, ...], rows: list[dict]) -> str:
    """The CSV text, with its one BOM. Encode it as plain "utf-8": a second BOM would show in the first header."""
    buf = io.StringIO(newline="")
    out = csv.writer(buf)
    out.writerow(columns)
    out.writerows([cell(row.get(c)) for c in columns] for row in rows)
    return BOM + buf.getvalue()


def changes_csv(check: dict, journal: dict) -> str:
    """The approved changes as a table: before and after as the city's file holds them, who said so, and the photo."""
    return to_csv(CHANGE_COLUMNS, validate_journal(check, journal))


def observations_csv(check: dict, journal: dict) -> str:
    """The photo observations as a table. The date is the photo's, never an inspection or verification date."""
    return to_csv(OBSERVATION_COLUMNS, observations(check, journal))


# --- the trusted city file ------------------------------------------------------------------------------------------


def city_problems(items: list[dict], town) -> list[str]:
    """What a city's own file says against each approved decision: the item is one of the city's, its layer and id key are
    the city's for that item, and a change is to the one yes/no field the city names, between its two raw values. The
    downloaded check says what a reviewer was offered; only this says what the city allows. town: a city file as
    eval.city_check.load_city returns it, loaded by the caller from the repository, never from a journal or a check."""
    cfgs = town.get("items") if isinstance(town, dict) else None
    if not isinstance(cfgs, dict) or not cfgs:
        return ["the city file has no items"]
    out: list[str] = []
    for it in items:
        label = f"record {it['record_id']} ({it['item']})"
        cfg = cfgs.get(it["item"])
        if not isinstance(cfg, dict):
            out.append(f"{label}: {it['item']!r} is not an item of the city file")
            continue
        if layer_basename(cfg.get("layer")) != it["layer"]:
            out.append(f"{label}: layer {it['layer']!r} is not the city file's layer for this item")
        if cfg.get("record_field") != it["record_key"]:
            out.append(f"{label}: record key {it['record_key']!r} is not the city file's id field")
        if it["decision"] != "change":
            continue
        compare = cfg.get("compare")
        field = it["field"]
        if not isinstance(compare, dict) or compare.get("field") != field:
            out.append(f"{label}: {field!r} is not the yes/no field the city file has configured")
            continue
        yes, no = compare.get("yes"), compare.get("no")
        if is_protected(field, cfg):
            out.append(f"{label}: {field!r} is a protected field (an id, a position or a date)")
        elif not isinstance(yes, str) or not isinstance(no, str) or yes == no:
            out.append(f"{label}: the city file does not name two different yes/no values for {field}")
        elif it["before"] not in (yes, no) or it["after"] not in (yes, no):
            out.append(f"{label}: the city file's yes/no values for {field} are {yes!r} and {no!r}")
        elif it["before"] == it["after"]:
            out.append(f"{label}: no change, the record already says {it['before']!r}")
    return out
