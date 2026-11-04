"""One area's Asset Check: every city record the photos had in view and every object the city's lists do not have, one
row each, with what the photos settled.

    python -m eval.asset_check --run demo --scan 17 --city seattle

It adds no model judgement of its own. It reads what the survey's city check and the later looks already wrote
(eval.second_look, eval.pano_look), and blurs the photos those looks used, as a survey blurs its evidence photos. A
record the photos did not settle stays "Not determined", which does not mean missing.

A later look can be sent back: <run>/look_review_scan<N>.json names the rows an AI assistant looked at photo by photo,
and the ones whose box sat on something other than the record. Those rows go back to "Not determined".

Writes <run>/asset_check_scan<N>.json. The server attaches it to the scan; assist.asset_check turns it into the page
and the table a city's asset team takes. Nothing here writes to a city's records.

Each row of a city record also carries `ledger_source`: that record's own properties as the city's file held them (hash,
id, the one yes/no field a reviewer may change), which eval.ledger_review checks a reviewer's changes against.
"""
import argparse
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval import attribute_state, evidence_quality, ledger_review, precision
from eval.city import box_of
from eval.city_check import load_city
from eval.second_look import is_seen
from eval.store import load_scan, open_ro
from pipeline import config, evidence, survey_items
from pipeline.cost import CostMeter
from pipeline.meta_api import MetaApi
from pipeline.parallel import run_parallel

SEEN, NOT_DETERMINED, NOT_LISTED = "Seen in photo", "Not determined", "Not on the city list"
STATUSES = (SEEN, NOT_DETERMINED, NOT_LISTED)
BY_SURVEY, BY_LOOK, BY_PANORAMA = "Survey", "Second look", "Second look, panorama"
FIX, DATE, SAME, VISIT, COMPARE = ("Correct the record", "Update the date", "No change", "Visit on site",
                                   "Compare with the list")
NOT_SETTLED = "Not settled what this is"
REASONS = {"unclear": "the photo is too unclear, cropped or far away to answer it",
           "not_visible": "the part of the object that answers it is not in view",
           "error": "the model's answer failed", "not_asked": "the check was not run",
           "models_disagree": "two models did not agree", "second_model_error": "the second model did not answer",
           "sent_back": "an AI assistant looked at the photo and sent the difference back",
           "no_check": "the photo of this record was not asked about this field",
           "city_value_blank": "the city list has no value for this field, so there is nothing to compare",
           "no_photo": "no photo box was kept to ask about",
           evidence_quality.HOLD_REASON: "an AI visual audit found the photo does not show this detail well enough to "
                                         "read it (not independent ground truth)"}
NEEDS_NEWER = "A newer photo or a field check is needed."
CITY_POSITION = {"position_source": "City record", "position_accuracy": "City record"}
CAMERA_POSITION = {"position_source": "Camera position of the Mapillary photo",
                   "position_accuracy": "Unknown (within 50 ft)"}
COST_CAP_USD = 0.25  # two SAM requests a photo, for the photos of one area's later looks
BLUR_PER_PHOTO_USD = 2 * config.SAM_PER_IMAGE  # evidence.make_evidence asks SAM 3.1 for people and for plates
WORKERS = 4


def month(value) -> str:
    """A date of the city's as YYYY-MM. The layers hold epoch milliseconds or text."""
    if isinstance(value, (int, float)) and value > 1e11:
        return datetime.fromtimestamp(value / 1000, timezone.utc).strftime("%Y-%m")
    return str(value)[:7] if value and str(value).strip() else ""


def record_key(city: dict) -> str:
    return json.dumps(city, sort_keys=True)


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _confirmed(job: dict) -> dict | None:
    """The first answer of a job that both models said yes to."""
    return next((a for a in job["answers"] if (a.get("open") or {}).get("answer") == "yes"), None)


def read_looks(run_dir: Path, scan: int, frames: list[dict]) -> tuple[dict[str, dict], dict]:
    """What the later looks settled, by record ({"how", "view", "box", "photo"}), and what they say about themselves."""
    creators = {f["image_id"]: f.get("creator") for f in frames}
    review = _read(run_dir / f"look_review_scan{scan}.json") or {}
    sent_back = {(r["image_id"], str(r["record"])) for r in review.get("sent_back", [])}
    looks: dict[str, dict] = {}

    def add(job: dict, how: str, view: dict, spark: dict, photo: Path) -> None:
        if any((view["image_id"], str(v)) in sent_back for v in job["city"].values()):
            return
        looks.setdefault(record_key(job["city"]), {
            "how": how, "view": {**view, "creator": view.get("creator") or creators.get(view["image_id"])},
            "box": spark.get("box") or None, "photo": photo})

    flat_controls = flat_false = pano_controls = pano_false = 0
    first = _read(run_dir / f"second_look_scan{scan}.json")
    if first:
        flat_controls += first["summary"]["controls"]
        flat_false += first["summary"]["controls_both_yes"]
        for job in first["spots"]:
            if job["kind"] == "record" and is_seen(job):
                add(job, BY_LOOK, job["view"], job["spark"], config.photo_cache() / f"{job['view']['image_id']}.jpg")
    pilot = _read(run_dir / f"second_look_pilot_scan{scan}.json")
    if pilot:
        flat_controls += pilot["summary"]["controls"]
        flat_false += pilot["summary"]["controls_seen"]
        for job in pilot["jobs"]:
            if job["kind"] == "record" and (answer := _confirmed(job)):
                add(job, BY_LOOK, answer["view"], answer["spark"], config.photo_cache() / f"{answer['view']['image_id']}.jpg")
    newer = _read(run_dir / f"pano_look_scan{scan}.json")
    if newer:
        pano_controls, pano_false = newer["summary"]["controls"], newer["summary"]["controls_seen"]
        for job in newer["jobs"]:
            if job["kind"] == "record" and (answer := _confirmed(job)):
                view = answer["view"]
                add(job, BY_PANORAMA, view, answer["spark"], run_dir / "pano_views" / f"{view['image_id']}_{view['yaw']:.0f}.jpg")
    controls = [{"photos": "Survey-date photos", "asked": flat_controls, "both_yes": flat_false},
                {"photos": "Panoramas", "asked": pano_controls, "both_yes": pano_false,
                 **({"note": review["newer_control_note"]} if review.get("newer_control_note") else {})}]
    return looks, {"controls": [c for c in controls if c["asked"]], "looked_at": review.get("looked_at", 0),
                   "sent_back": len(sent_back)}


def read_nearby(run_dir: Path, scan: int, frames: list[dict]) -> dict[str, dict]:
    """The survey photo the second look chose for each record, by record: what a person can look at before going to a
    place the photos did not settle. It shows the place, and it may not show the record: the direction it was chosen by
    can be off, so nothing here marks a spot on it. Only linked, never served, so no blurred copy of it is made."""
    creators = {f["image_id"]: f.get("creator") for f in frames}
    near: dict[str, dict] = {}
    for job in (_read(run_dir / f"second_look_scan{scan}.json") or {}).get("spots", []):
        view = job.get("view")
        if job["kind"] == "record" and view:
            near.setdefault(record_key(job["city"]), {
                "link": evidence.mapillary_link(view["image_id"]), "creator": creators.get(view["image_id"]),
                "captured_at": view["captured_at"], "license": evidence.LICENSE, "dist_m": view.get("dist_m")})
    return near


def read_differs(run_dir: Path, scan: int) -> tuple[dict, dict | None]:
    """The records a photo contradicts by (item, record), without those the AI assistant looked at and sent back
    (look_review_scan<N>.json, "not_different"), and what eval.record_check says about itself."""
    checked = _read(run_dir / f"record_check_scan{scan}.json")
    if not checked:
        return {}, None
    review = _read(run_dir / f"look_review_scan{scan}.json") or {}
    sent_back = {(r["item"], str(r["record"])) for r in review.get("not_different", [])}
    differs = {(r["item"], r["record"]): {"city_says": r["city_says"], "photo_shows": r["photo_shows"]}
               for r in checked["rows"] if r["differs"] and (r["item"], r["record"]) not in sent_back}
    return differs, {**checked["summary"], "differ": len(differs), "sent_back": len(sent_back)}


def read_attributes(run_dir: Path, scan: int) -> dict[tuple[str, str], dict]:
    """Where each asked record's attribute check ended, by (item, record): agree, different or unsettled and why
    (eval.attribute_state). A difference the AI assistant looked at and sent back is unsettled, not agreed: it says the
    models were wrong about the photo, not that the city's field is right. Nothing is read where no check was run."""
    checked = _read(run_dir / f"record_check_scan{scan}.json")
    if not checked:
        return {}
    review = _read(run_dir / f"look_review_scan{scan}.json") or {}
    sent_back = {(r["item"], str(r["record"])) for r in review.get("not_different", [])}
    out = {}
    for r in checked["rows"]:
        key, state = (r["item"], str(r["record"])), attribute_state.state_of(r)
        if state["state"] == attribute_state.DIFFERENT and key in sent_back:
            state = {"state": attribute_state.UNSETTLED, "reason": "sent_back"}
        out[key] = state
    return out


def second_look_costs(run_dir: Path, scan: int) -> dict:
    """What the later looks behind a survey's Asset Check cost, by part. Each look wrote the cost of its run
    (cost_usd) beside its answers; blurring wrote none, so it is SAM's two requests for each look photo made. The
    survey's own cost is not here: it is the scan's."""
    def spent(name: str, *keys: str) -> float:
        value = _read(run_dir / name)
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        return float(value) if isinstance(value, (int, float)) else 0.0

    photos = len(list((run_dir / "evidence").glob(f"scan{scan}_look*.jpg")))
    parts = {"second_look": spent(f"second_look_scan{scan}.json", "cost_usd"),
             "sample": spent(f"second_look_pilot_scan{scan}.json", "summary", "cost_usd"),
             "panoramas": spent(f"pano_look_scan{scan}.json", "cost_usd"),
             "record_check": spent(f"record_check_scan{scan}.json", "cost_usd"),
             "blur": photos * BLUR_PER_PHOTO_USD}
    return {**{k: round(v, 4) for k, v in parts.items()}, "total": round(sum(parts.values()), 4)}


def cost_trust(costs: dict) -> dict:
    """The trust entries that say what the later looks cost: none when they cost nothing."""
    return {"second_looks": costs, "second_looks_usd": costs["total"]} if costs.get("total") else {}


def auto_labels(data: dict, labels: dict | None) -> dict | None:
    """The AI assistant's check of the findings that are still the models' own: the rows nobody has reviewed or rejected
    since (status auto), the rows eval.precision draws. Its judgement of any other row stays in the file and still says
    whether the models were sure of that row (action_of), but the row is no finding the models made on their own."""
    if not labels:
        return labels
    own = {"issues": {str(i["id"]) for i in data["issues"] if i["review_status"] == "auto"},
           "entrances": {str(e["id"]) for e in data.get("entrances", []) if e["review_status"] == "auto"}}
    return {**labels, **{kind: {k: v for k, v in labels.get(kind, {}).items() if k in own[kind]} for kind in own}}


def evidence_of(looks: dict[str, dict], scan: int) -> dict[Path, str]:
    """The evidence file each look's photo goes to, numbered by photo so two records in one photo share it."""
    return {photo: f"scan{scan}_look{n}_{next(v['view']['image_id'] for v in looks.values() if v['photo'] == photo)}.jpg"
            for n, photo in enumerate(sorted({v["photo"] for v in looks.values()}, key=str))}


def blur_looks(looks: dict[str, dict], run_dir: Path, scan: int, api=None, offline: bool = False) -> float:
    """Give every look its blurred evidence photo, with Mapillary's attribution. A failed blur raises, so an unblurred
    photo is never stored. Photos already blurred are not sent again: <run>/look_evidence_scan<N>.json keeps what each
    blur found. offline: blur nothing and ask nothing; stop if a photo is not blurred yet. Returns what the blurring
    spent."""
    names = evidence_of(looks, scan)
    folder = run_dir / "evidence"
    made_path = run_dir / f"look_evidence_scan{scan}.json"
    made = _read(made_path) or {}
    missing = [p for p, name in names.items() if name not in made or not (folder / name).is_file()]
    if missing and offline:
        raise SystemExit(f"--offline: {len(missing)} look photos are not blurred yet, and nothing is sent to be: "
                         + ", ".join(names[p] for p in missing))
    meter = CostMeter(COST_CAP_USD)
    if missing and api is None:
        api = MetaApi(run_dir / "calls_second_look.jsonl", meter)

    def blur(photo: Path) -> None:
        view = next(v["view"] for v in looks.values() if v["photo"] == photo)
        made[names[photo]] = evidence.make_evidence(api, photo, view, folder / names[photo])

    try:
        run_parallel(blur, missing, WORKERS)
    finally:
        made_path.write_text(json.dumps(made, ensure_ascii=False, indent=1), encoding="utf-8")
    for look in looks.values():
        look["evidence"] = made[names[look["photo"]]]
    return meter.spent_usd


def asset_name(standard, item: str) -> str:
    target = standard.check(item).target.strip()
    return target[:1].upper() + target[1:]


def sure_of(row: dict, label: dict | None) -> bool:
    """The models were sure, and the assistant's check, when there is one, agrees."""
    return row["models_unsure"] != "yes" and (label is None or label.get("value") == "yes")


def action_of(row: dict, label: dict | None, differs: dict | None, verified: bool,
              attribute: dict | None = None) -> str | None:
    """What a city does with a row. None: the AI assistant found the object wrong or a repeat, so it leaves the list.
    A visit is always a city record, so one number means the same on every surface; an object on no list is to compare,
    sure of or not. verified: the list's date says when the city last verified the record, not just when the list was
    written. attribute: how the check of the city's yes/no field for the record ended, where its list has one. An
    object that is there says nothing about that field, so a record whose field is unsettled or was never checked is a
    visit too, and keeps the status and the dates it was seen with."""
    if row["status"] == NOT_LISTED:
        return None if label and (label.get("dup_of") is not None or label.get("value") == "no") else COMPARE
    if row["status"] == NOT_DETERMINED or not sure_of(row, label):
        return VISIT
    if differs:
        return FIX
    if attribute and attribute["state"] in attribute_state.OPEN:
        return VISIT
    return DATE if verified and row["photo_newer_than_city_date"] == "yes" else SAME


def said(row: dict, action: str, differs: dict | None, cfg: dict, sure: bool = True) -> dict:
    """The two sides of a change. The city's own date stays in its column: a photo date never fills it."""
    if action == COMPARE and not sure:
        return {"city_says": "", "photo_shows": NOT_SETTLED}
    if action == FIX:
        return {"city_says": differs["city_says"], "photo_shows": differs["photo_shows"]}
    if action == DATE:
        return {"city_says": f"{cfg['checked_field']} {row['city_date']}",
                "photo_shows": f"Seen in a photo of {row['seen_in_photo_on']}"}
    return {"city_says": "", "photo_shows": ""}


def attribute_cells(attribute: dict, cfg: dict) -> dict:
    """The attribute check as the table says it: which field, how it ended, and the city's side with the reason."""
    says, state, reason = attribute["says"], attribute["state"], attribute["reason"]
    why = {attribute_state.AGREE: "a model reading the photo agrees",
           attribute_state.DIFFERENT: "two models reading the photo say otherwise"}.get(state) or REASONS[reason]
    return {"attribute_field": cfg["compare"]["field"], "attribute_state": state,
            "attribute_note": f"{says}: {why}" if says else why[:1].upper() + why[1:]}


def visit_reason(row: dict, label: dict | None, attribute: dict | None, cfg: dict) -> str:
    """Why a row is a visit, in words: the photos alone do not settle it."""
    if row["status"] == NOT_DETERMINED:
        return f"No photo settled whether this record is there. {NEEDS_NEWER}"
    if not sure_of(row, label) or not attribute:
        return f"The models were not sure what the object is. {NEEDS_NEWER}"
    named = f"the city's \"{attribute['says']}\"" if attribute["says"] else f"the city's {cfg['compare']['field']}"
    return f"The record is there in a photo, but {named} is not settled: {REASONS[attribute['reason']]}. {NEEDS_NEWER}"


def visit_causes(row: dict, label: dict | None, attribute: dict | None, hold: dict | None) -> list[str]:
    """Why a visit row is one, before the identity gate adds its own cause: the photos did not settle the record, the
    models were not sure what the object is, an audit held the source, or the record's attribute is open."""
    causes = ["not_determined"] if row["status"] == NOT_DETERMINED else [] if sure_of(row, label) else ["models_unsure"]
    if hold:
        causes.append(evidence_quality.HOLD_REASON)
    elif attribute and attribute["state"] in attribute_state.OPEN and not causes:
        causes.append("attribute_open")
    return causes


VISIT_PRIORITY = ("not_determined", "models_unsure", "identity_unresolved", evidence_quality.HOLD_REASON, "attribute_open")


def apply_identity_gate(rows: list[dict], pairs: dict[str, list[dict]]) -> None:
    """Mark the city-record rows of one item with their identity state, and route those whose identity the photos cannot
    settle to Visit on site. pairs: eval.ledger_review.add_neighbor_warnings' pairs. A row that was a candidate keeps its
    models' answer in row["quality_gate"]; one that already was a visit stays one, with the identity reason added. Both
    rows of a pair keep their own ids, photos and answers: nothing is merged."""
    for row in rows:
        base = row.pop("_visit")
        if not row["city_record"]:
            continue
        src = row.get("ledger_source")
        near = sorted(pairs.get(src["review_key"], []), key=lambda n: (n["metres"], n["record_id"])) if src else []
        if not near:
            row.update(identity_state=evidence_quality.NOT_FLAGGED, identity_reason="", competing_record_ids=[])
        else:
            reason = evidence_quality.identity_reason(row["city_record"], near)
            row.update(identity_state=evidence_quality.UNRESOLVED, identity_reason=reason,
                       competing_record_ids=sorted({n["record_id"] for n in near}))
            gate = row.get("quality_gate")
            before = gate["action_before"] if gate else row["action"]
            if before != VISIT:
                if not gate:
                    gate = {"action_before": before, "causes": [], "model_answer": {
                        "city_says": row["city_says"], "photo_shows": row["photo_shows"],
                        **({"attribute_state": row["attribute_state"], "attribute_note": row["attribute_note"]}
                           if "attribute_state" in row else {})}}
                row["quality_gate"] = {**gate, "causes": ["identity_unresolved", *gate["causes"]]}
                row.update(action=VISIT, city_says="", photo_shows="",
                           visit_reason=f"{reason} {evidence_quality.NEXT_STEP} Until then it is not a candidate for "
                                        "desk review.")
            else:
                row["visit_reason"] = f"{row.get('visit_reason', '')} {reason} {evidence_quality.NEXT_STEP}".strip()
                if gate:
                    row["quality_gate"] = {**gate, "causes": ["identity_unresolved", *gate["causes"]]}
            row.pop("action_if_attribute_agrees", None)  # an unresolved identity is no conditional candidate either
        if row["action"] == VISIT:
            causes = {*base, *(["identity_unresolved"] if near else [])}
            row["visit_causes"] = [c for c in VISIT_PRIORITY if c in causes]


def gate_summary(rows: list[dict], policy: dict, no_visit: int) -> dict:
    """What the two gates did to the counts, in the numbers every surface shares. A row held on both grounds is counted
    once; the candidates before the gates are the candidates after plus the rows moved out of them."""
    city = [r for r in rows if r["city_record"]]
    unresolved = [r for r in city if r["identity_state"] == evidence_quality.UNRESOLVED]
    moved = [r for r in city if "quality_gate" in r and r["quality_gate"]["action_before"] != VISIT]
    causes = [set(r["quality_gate"]["causes"]) for r in moved]
    visits = [r for r in city if r["action"] == VISIT]
    primary = Counter(next(c for c in VISIT_PRIORITY if c in r["visit_causes"]) for r in visits)
    held = sum(r["evidence_quality"] == evidence_quality.UNCLEAR for r in city)
    competing = {(r["item"], r["city_record"]): r["competing_record_ids"] for r in unresolved}
    return {"identity": {"unresolved_rows": len(unresolved),
                         "moved_out_of_candidates": sum("identity_unresolved" in c for c in causes),
                         "already_visit": sum(r["action"] == VISIT and r["identity_state"] == evidence_quality.UNRESOLVED
                                              and ("quality_gate" not in r or r["quality_gate"]["action_before"] == VISIT)
                                              for r in city),
                         "groups": evidence_quality.groups_of(competing),
                         "rule": {"max_metres": ledger_review.NEIGHBOR_M, "max_seconds": ledger_review.NEIGHBOR_S}},
            "evidence_quality": {"unclear": held, "not_reviewed": len(city) - held,
                                 "policy": {"policy_id": policy["policy_id"], "provenance": policy["provenance"],
                                            "holds_in_policy": len(policy["holds"]), "applied": held}},
            "moved_out_of_candidates": len(moved), "candidates_before_gate": no_visit + len(moved),
            "by_cause": {"identity_only": sum("audit_hold" not in c for c in causes),
                         "hold_only": sum("identity_unresolved" not in c for c in causes),
                         "identity_and_hold": sum({"identity_unresolved", "audit_hold"} <= c for c in causes)},
            "visit_causes": {"primary": {c: primary[c] for c in VISIT_PRIORITY if primary[c]},
                             "overlap": {"identity_and_attribute": sum(
                                 "identity_unresolved" in r["visit_causes"] and bool(
                                     {"audit_hold", "attribute_open"} & set(r["visit_causes"])) for r in visits)}}}


def attribute_counts(rows: list[dict], reasons: Counter) -> dict:
    """How the attribute checks of these rows ended; reasons: why the open ones are open."""
    states = Counter(r["attribute_state"] for r in rows)
    return {"required": len(rows), **{state: states[state] for state in attribute_state.STATES},
            "open_by": dict(reasons)}


def tally(rows: list[dict]) -> dict:
    status = Counter(r["status"] for r in rows)
    return {"in_view": status[SEEN] + status[NOT_DETERMINED], "seen": status[SEEN],
            "seen_by": dict(Counter(r["how_seen"] for r in rows if r["status"] == SEEN)),
            "not_determined": status[NOT_DETERMINED], "not_listed": status[NOT_LISTED],
            "not_listed_unsure": sum(r["status"] == NOT_LISTED and r["models_unsure"] == "yes" for r in rows),
            "seen_unsure": sum(r["status"] == SEEN and r["models_unsure"] == "yes" for r in rows),
            "newer": sum(r["photo_newer_than_city_date"] == "yes" for r in rows)}


def attribute_of(item: str, record: str, props: dict, cfg: dict, box, ev, attributes: dict) -> dict:
    """How the check of a seen record's yes/no field ended, with what the city says in its own words. A record the check
    was not run for is not checked, and why: the city's value is blank, no photo box was kept, or it was never asked."""
    compare = cfg["compare"]
    value = {compare["yes"]: "yes", compare["no"]: "no"}.get(props.get(compare["field"]))
    state = attributes.get((item, record))
    if state is None:
        reason = "city_value_blank" if not value else "no_photo" if not (box and ev) else "no_check"
        state = {"state": attribute_state.NOT_CHECKED, "reason": reason}
    return {**state, "says": compare.get(f"says_{value}", "") if value else ""}


def item_rows(item: str, info: dict, cfg: dict, features: list[dict], name: str, issues: list[dict],
              looks: dict[str, dict], nearby: dict[str, dict], attributes: dict | None = None,
              scan_id=None) -> list[dict]:
    keys = info["id_fields"]
    by_key = {tuple(str(f["properties"].get(k)) for k in keys): f for f in features if f.get("geometry")}
    base = {"item": item, "asset": name}

    def record_row(city: dict, status: str, how: str = "", issue: dict | None = None, look: dict | None = None) -> dict:
        feature = by_key[tuple(str(city.get(k)) for k in keys)]
        props = feature["properties"]
        lng, lat = feature["geometry"]["coordinates"][:2]
        # a list's date that is not a verification (it says when the list was updated) is no date to compare
        last = month(props.get(cfg["checked_field"])) if cfg.get("checked_field") and cfg.get("verified") else ""
        ev = (issue or {}).get("evidence") or (look or {}).get("evidence")
        seen_on = str((ev or {}).get("captured_at") or "")[:10]
        box = box_of(issue) if issue else (look or {}).get("box")
        record = str(city.get(cfg["record_field"]) or city.get(keys[0]))
        # a record seen in a photo has its object checked; its yes/no field, where the list has one, is a second check
        needs = {"attribute": attribute_of(item, record, props, cfg, box, ev, attributes or {})} \
            if status == SEEN and "compare" in cfg else {}
        # the city's own record as it was read, for a reviewer's change to be checked against (eval.ledger_review)
        source = ledger_review.make_source(scan_id, item, cfg, feature, features, ev, box)
        return {**base, "city_list": info["layer"], "city_record": record,
                "city_location": str(props.get(cfg.get("place_field")) or ""), "status": status, "how_seen": how,
                "issue_id": issue["id"] if issue else None,
                "models_unsure": "yes" if issue and issue.get("object_label") == "unclear" else "",
                "city_date": last, "seen_in_photo_on": seen_on,
                "photo_newer_than_city_date": "yes" if last and seen_on and seen_on[:7] > last else "",
                "city_condition": str(props.get(cfg.get("condition_field")) or "").strip(),
                "lat": round(lat, 6), "lng": round(lng, 6), **CITY_POSITION,
                "box": box, "evidence": ev, **needs, **({"ledger_source": source} if source else {})}

    rows = [record_row(pair["city"], SEEN, BY_SURVEY, issue=next(i for i in issues if i["id"] == pair["issue_id"]))
            for pair in info["pairs"] if pair.get("in_view")]
    for missed in info["missed"]:
        key = record_key(missed["city"])
        look = looks.get(key)
        rows.append(record_row(missed["city"], SEEN, look["how"], look=look) if look
                    else {**record_row(missed["city"], NOT_DETERMINED),
                          **({"nearby_photo": nearby[key]} if key in nearby else {})})
    paired = {pair["issue_id"] for pair in info["pairs"]}
    for issue in issues:
        if issue["issue_type"] == item and issue["id"] not in paired:
            ev = issue.get("evidence")
            rows.append({**base, "city_list": "", "city_record": "", "city_location": "", "status": NOT_LISTED,
                         "how_seen": "", "issue_id": issue["id"],
                         "models_unsure": "yes" if issue.get("object_label") == "unclear" else "",
                         "city_date": "", "seen_in_photo_on": str((ev or {}).get("captured_at") or "")[:10],
                         "photo_newer_than_city_date": "", "city_condition": "", "lat": round(issue["lat"], 6),
                         "lng": round(issue["lng"], 6), **CAMERA_POSITION, "box": box_of(issue), "evidence": ev})
    return rows


def years_between(city_date: str, photo_date: str) -> float:
    """From the city's YYYY-MM to a photo's YYYY-MM-DD, in years."""
    return (int(photo_date[:4]) - int(city_date[:4])) + (int(photo_date[5:7]) - int(city_date[5:7])) / 12


def id_text(value) -> str:
    """A city id as the app writes it: nothing for none, a whole number without its .0."""
    if value is None:
        return ""
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def unreached(check_file: dict, layers: dict[str, list[dict]], bbox: list[float]) -> tuple[dict[str, int], list[list], dict]:
    """The city's records in the survey's box that no photo had in view, as [item, lng, lat, [its ids]], how many each
    item has, and each item's id fields, named once. The ids are those a record in view is shown by (city_check's
    id_fields), nothing more. A photo at the box's edge can see a record past it, so a list's records in the box or in
    view are those in view plus those unreached. The survey says nothing about an unreached record."""
    west, south, east, north = bbox
    counts, out, fields = {}, [], {}
    for item, info in check_file["items"].items():
        keys = info["id_fields"]
        seen = {tuple(str(c["city"].get(k)) for k in keys) for c in [*info["pairs"], *info["missed"]]
                if c.get("in_view", True)}
        points = [(f["properties"], f["geometry"]["coordinates"][:2]) for f in layers[item]
                  if (f.get("geometry") or {}).get("type") == "Point"]
        inside = [(props, lng, lat) for props, (lng, lat) in points
                  if west <= lng <= east and south <= lat <= north and tuple(str(props.get(k)) for k in keys) not in seen]
        out += [[item, round(lng, 6), round(lat, 6), [id_text(props.get(k)) for k in keys]] for props, lng, lat in inside]
        counts[item] = len(inside)
        fields[item] = list(keys)
    return counts, out, fields


def build(data: dict, check_file: dict, town: dict, layers: dict[str, list[dict]], standard, looks: dict[str, dict],
          trust: dict, labels: dict | None = None, differs: dict | None = None,
          nearby: dict[str, dict] | None = None, attributes: dict | None = None,
          quality_policy: dict | None = None) -> dict:
    """data as eval.store.load_scan returns it; layers: each item's GeoJSON features; looks and trust as read_looks
    returns them, each look with its evidence; labels: the AI assistant's check of the findings (eval.precision);
    differs: the records a photo contradicts, by (item, record) (eval.record_check); nearby: as read_nearby returns it;
    attributes: where each asked record's attribute check ended, by (item, record), as read_attributes returns it.
    An item whose city file names a yes/no field a photo can answer ([items.<id>.compare]) needs that check for every
    record it saw: a record without it is a visit, however sure the models are that the object is there.
    quality_policy: the audit holds (eval.evidence_quality; the checked-in policy when none is given). Before any count
    is made, a row whose identity the photos cannot tell from a close record of the same item, or whose exact source an
    audit held, is a visit, with the reason, and the models' own answer kept beside it (row["quality_gate"])."""
    policy = evidence_quality.load_policy() if quality_policy is None else evidence_quality.check_policy(quality_policy)
    rows, listed, items = [], [], []
    left_out = Counter(repeats=0, wrong=0)
    open_by, unresolved = Counter(), 0
    issues = [i for i in data["issues"] if i["review_status"] != "rejected"]
    judged = (labels or {}).get("issues", {})
    for item, info in check_file["items"].items():
        name, cfg = asset_name(standard, item), town["items"][item]
        found = item_rows(item, info, cfg, layers[item], name, issues, looks, nearby or {}, attributes,
                          data["scan"]["id"])
        rows += found
        kept, item_open = [], Counter()
        for row in found:
            label = judged.get(str(row.pop("issue_id")))
            differ = (differs or {}).get((item, row["city_record"]))
            attribute = row.pop("attribute", None)
            if attribute and differ and attribute["state"] != attribute_state.DIFFERENT:
                attribute = {**attribute, "state": attribute_state.DIFFERENT, "reason": ""}  # the difference is there to read
            model_attribute = attribute  # what the models and the photos settled, before any audit hold
            hold = evidence_quality.find_hold(row, policy, attribute, (cfg.get("compare") or {}).get("field"))
            if hold:
                attribute = {**attribute, "state": attribute_state.UNSETTLED, "reason": evidence_quality.HOLD_REASON}
            action = action_of(row, label, differ, bool(cfg.get("verified")), attribute)
            if action is None:
                left_out["repeats" if label.get("dup_of") is not None else "wrong"] += 1
                continue
            extra = attribute_cells(attribute, cfg) if attribute else {}
            if row["city_record"]:
                extra.update(evidence_quality.quality_cells(hold))
            is_open = bool(attribute) and attribute["state"] in attribute_state.OPEN
            if is_open:
                open_by[attribute["reason"]] += 1
                item_open[attribute["reason"]] += 1
                # the record check asks about the rows whose object is settled; this keeps them findable while open
                if (waiting := action_of(row, label, None, bool(cfg.get("verified")))) != VISIT and not hold:
                    extra["action_if_attribute_agrees"] = waiting
            if action == VISIT:
                extra["visit_reason"] = visit_reason(row, label, attribute, cfg)
                unresolved += is_open and row["status"] == SEEN and sure_of(row, label)
            ledger_review.suggest(row.get("ledger_source"), attribute)
            if hold:
                before = action_of(row, label, differ, bool(cfg.get("verified")), model_attribute)
                extra["quality_gate"] = {
                    "action_before": before, "causes": [evidence_quality.HOLD_REASON],
                    "model_answer": {"attribute_state": model_attribute["state"],
                                     "attribute_note": attribute_cells(model_attribute, cfg)["attribute_note"],
                                     **said(row, before, differ, cfg, sure_of(row, label))}}
            kept.append({**row, "action": action, **said(row, action, differ, cfg, sure_of(row, label)), **extra,
                         "_visit": visit_causes(row, label, attribute, hold) if action == VISIT else []})
        apply_identity_gate(kept, ledger_review.add_neighbor_warnings(kept))
        listed += kept
        needing = [r for r in kept if "attribute_state" in r]
        items.append({"item": item, "asset": name, "city_list": info["layer"], **tally(found),
                      "actions": dict(Counter(r["action"] for r in kept)),
                      **({"attribute_checks": attribute_counts(needing, item_open)} if needing else {})})
    outside = sum(not p.get("in_view") for info in check_file["items"].values() for p in info["pairs"])
    checked = next((r for r in precision.summary(auto_labels(data, labels)) if r["item"] == "all"), None) if labels else None
    checked = checked if checked and checked["n"] else None
    months = sorted({r["seen_in_photo_on"][:7] for r in rows if r["status"] == SEEN and r["seen_in_photo_on"]})
    no_visit = sum(bool(r["city_record"]) and r["action"] != VISIT for r in listed)
    totals = tally(rows)
    reach = {}
    if data["scan"].get("bbox"):
        far_counts, far, far_fields = unreached(check_file, layers, data["scan"]["bbox"])
        for entry in items:
            entry["in_area"] = entry["in_view"] + far_counts[entry["item"]]
        totals["in_area"] = totals["in_view"] + len(far)
        reach = {"unreached": far, "unreached_fields": far_fields}
    compare_unsure = sum(r["photo_shows"] == NOT_SETTLED for r in listed)
    visits_with_photo = sum(r["action"] == VISIT and bool(r.get("evidence") or r.get("nearby_photo")) for r in listed)
    needing = [r for r in listed if "attribute_state" in r]
    gaps = [years_between(r["city_date"], r["seen_in_photo_on"]) for r in listed if r["action"] == DATE]
    return {"scan_id": data["scan"]["id"], "city": town["city"], **({"credit": town["credit"]} if "credit" in town else {}),
            "items": items, "totals": totals, **reach,
            "actions": dict(Counter(r["action"] for r in listed)), "no_visit": no_visit,
            "desk_review_candidates": no_visit,  # what no_visit counts, named for what it is: not a measured avoided visit
            "quality_gate": gate_summary(listed, policy, no_visit),
            "compare_unsure": compare_unsure,
            **({"attribute_checks": attribute_counts(needing, open_by), "visits_attribute_unresolved": unresolved}
               if needing else {}),
            "visits_with_photo": visits_with_photo,
            **({"date_gap_years": round(statistics.median(gaps), 1)} if gaps else {}),
            "left_out": dict(left_out),
            "rows": listed,
            "trust": {**trust, "photo_months": months, "outside_view": outside,
                      **({"findings": {k: checked[k] for k in ("n", "yes", "no", "unclear")}} if checked else {})}}


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--scan", type=int, required=True)
    parser.add_argument("--city", default="seattle", help="a file in eval/cities, without .toml")
    parser.add_argument("--offline", action="store_true",
                        help="make no model call: use the blurred photos already made, and stop if one is missing")
    args = parser.parse_args(argv)
    run_dir = config.run_dir(args.run)
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, args.scan)
    finally:
        conn.close()
    check_file = json.loads((run_dir / f"city_check_scan{args.scan}.json").read_text(encoding="utf-8"))
    town = load_city(args.city)
    root = config.data_dir() / town["root"]
    layers = {item: json.loads((root / town["items"][item]["layer"]).read_text(encoding="utf-8"))["features"]
              for item in check_file["items"]}
    looks, trust = read_looks(run_dir, args.scan, data["frames"])
    spent = blur_looks(looks, run_dir, args.scan, offline=args.offline)
    differs, record_trust = read_differs(run_dir, args.scan)
    attributes = read_attributes(run_dir, args.scan)
    if record_trust:
        trust["record_check"] = record_trust
    trust.update(cost_trust(second_look_costs(run_dir, args.scan)))  # after the blur: it counts this run's photos
    out = build(data, check_file, town, layers, survey_items.load(data["scan"]["jurisdiction"]), looks, trust,
                _read(run_dir / f"precision_scan{args.scan}.json"), differs,
                read_nearby(run_dir, args.scan, data["frames"]), attributes)
    path = run_dir / f"asset_check_scan{args.scan}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"totals": out["totals"], "trust": out["trust"]}, ensure_ascii=False, indent=1))
    print(f"${spent:.2f} spent; wrote {path}")
    return path


if __name__ == "__main__":
    main()
