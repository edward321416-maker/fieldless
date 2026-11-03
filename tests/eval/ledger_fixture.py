"""A small made-up city for the ledger review tests: records, an Asset Check built by eval.asset_check.build from
them, and journals. Nothing here is a municipal approval: the names, dates and reasons are test inputs."""
import copy
from types import SimpleNamespace

from eval import asset_check

SCAN = 7
TOWN = {"city": "Testville", "items": {
    "pole": {"layer": "assets/poles.geojson", "record_field": "UNITID", "id_fields": ["UNITID"],
             "checked_field": "CHECKED", "verified": True,
             "compare": {"field": "IS_LIGHT", "yes": "YES", "no": "NO", "ask": "Does the pole hold a lamp?",
                         "says_yes": "Holds a street light", "says_no": "Holds no street light"}},
    "tree": {"layer": "assets/trees.geojson", "record_field": "UNITID", "id_fields": ["UNITID"],
             "checked_field": "LAST_VERIFY_DATE", "verified": True},
    "can": {"layer": "litter.geojson", "record_field": "UNITID", "id_fields": ["UNITID"]}}}
FAR = {"lat": 1.0, "lng": 2.0}


def feature(props, lat=47.6, lng=-122.3):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [lng, lat]}}


def layers():
    """Raw features as the city's files hold them (a fresh copy each call)."""
    return copy.deepcopy({
        "pole": [feature({"UNITID": "P1", "IS_LIGHT": "YES", "CHECKED": "2020-05-01", "NOTE": "café"}, 47.6, -122.3),
                 feature({"UNITID": "P2", "IS_LIGHT": "Maybe", "CHECKED": "2020-05-01"}, 47.61, -122.3),
                 feature({"UNITID": "P3", "IS_LIGHT": "NO", "CHECKED": "2020-05-01"}, 47.62, -122.3),
                 feature({"UNITID": "P3", "IS_LIGHT": "NO"}, 47.63, -122.3)],  # P3 is a duplicated id
        "tree": [feature({"UNITID": "TRE-53943", "LAST_VERIFY_DATE": 1600000000000, "CONDITION": "Good"}, 47.7, -122.3),
                 feature({"UNITID": "TRE-53948", "LAST_VERIFY_DATE": 1600000000000, "CONDITION": "Good"},
                         47.7 + 0.00007, -122.3),  # about 8 m north of TRE-53943
                 feature({"UNITID": "TRE-90000", "LAST_VERIFY_DATE": 1600000000000}, 47.7 + 0.01, -122.3)],
        "can": [feature({"UNITID": "C1"}, 47.8, -122.3)]})


def issue(id_, item, captured="2022-08-30T10:00:00Z", box=(1, 2, 3, 4), label="yes"):
    return {"id": id_, "issue_type": item, "object_label": label, "review_status": "auto", "lat": 5.0, "lng": 6.0,
            "model_outputs": {"check": {"box": list(box)}},
            "evidence": {"image": f"scan{SCAN}_issue{id_}_9.jpg", "image_id": f"9{id_}", "captured_at": captured,
                         "creator": "photographer", "link": f"https://www.mapillary.com/app/?pKey=9{id_}",
                         "license": "CC BY-SA 4.0"}}


def data():
    return {"scan": {"id": SCAN}, "issues": [
        issue(1, "pole"), issue(2, "pole"), issue(3, "pole"),
        issue(4, "tree", "2022-08-30T10:00:00Z"), issue(5, "tree", "2022-08-30T10:00:07Z"),
        issue(6, "tree", "2022-08-30T10:00:07Z")]}


def check_file():
    def pair(issue_id, uid):
        return {"issue_id": issue_id, "in_view": True, "city": {"UNITID": uid}}
    return {"items": {
        "pole": {"layer": "City poles", "id_fields": ["UNITID"],
                 "pairs": [pair(1, "P1"), pair(2, "P2"), pair(3, "P3")], "missed": []},
        "tree": {"layer": "City trees", "id_fields": ["UNITID"],
                 "pairs": [pair(4, "TRE-53943"), pair(5, "TRE-53948"), pair(6, "TRE-90000")], "missed": []},
        "can": {"layer": "City cans", "id_fields": ["UNITID"], "pairs": [],
                "missed": [{"city": {"UNITID": "C1"}, **FAR}]}}}


STANDARD = SimpleNamespace(check=lambda item: SimpleNamespace(
    target={"pole": "city pole", "tree": "street tree", "can": "public can"}[item]))
TRUST = {"controls": [], "looked_at": 0, "sent_back": 0}


def make_check(town=None, layer_map=None, differs=None, attributes=None, issues=None):
    d = data()
    if issues is not None:
        d["issues"] = issues
    return asset_check.build(d, check_file(), town or TOWN, layer_map or layers(), STANDARD, {}, TRUST, None,
                             differs, None, attributes)


def row_of(check, record):
    return next(r for r in check["rows"] if r["city_record"] == record)


def decision(check, record, **over):
    """A complete, valid `change` of the pole P1 light field unless overridden."""
    row = row_of(check, record)
    src = row["ledger_source"]
    base = {"review_key": src["review_key"], "record_id": src["record_id"], "item": row["item"],
            "identity": "confirmed", "evidence": "clear", "decision": "change", "field": "IS_LIGHT",
            "proposed_value": "NO", "neighbors_checked": False, "reviewer": "A. Tester",
            "note": "lamp not on the pole in the photo", "reviewed_at": "2026-11-04T09:30:00.000Z"}
    return {**base, **over}


def journal(*decisions, scan_id=SCAN):
    return {"schema_version": 1, "scan_id": scan_id, "decisions": list(decisions)}
