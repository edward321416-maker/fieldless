"""Evidence quality before candidate counts: two gates a row passes before it can be counted as a desk-review candidate.

Identity. A same-item pair of records close in the city's positions and in photo time (ledger_review.neighbor_pairs) is
a pair the photos may not tell apart. Both rows are marked `unresolved`, keep their own ids, photos and answers, and
are routed to Visit on site with the reason. Nothing is merged and nothing is called a duplicate: the pair may well be
two different objects.

Audit holds. A checked-in policy (evidence_policy/ai_audit_holds.json) lists sources an AI visual audit judged too
unclear to read one field from. A hold binds the exact source: the review key (record, record's properties, position,
photo, box and scan) and every value it was made from. A source that changed, in any of them, gets no hold. A hold sets
the row's attribute check to unsettled and says why; it does not rerun a model, it does not call the city's record
wrong, and it is not ground truth (PROVENANCE).

To hold another source, take its row from a built Asset Check, call make_hold(row, field, reason, state, scan_id) and add
the result to the policy file: check_policy refuses a hold whose review key is not the key of the values it binds.

`not_flagged` and `not_reviewed` mean only that no gate raised anything for the row. They never mean verified.
"""
import json
from pathlib import Path

from eval import attribute_state, ledger_review

PROVENANCE = "AI visual audit, not independent ground truth"
POLICY_VERSION = 1
DEFAULT_POLICY = Path(__file__).resolve().parent / "evidence_policy" / "ai_audit_holds.json"
POLICY_KEYS = {"schema_version", "policy_id", "provenance", "audit", "holds"}
HOLD_KEYS = {"id", "review_key", "scan_id", "item", "layer", "record_key", "record_id", "field", "model_attribute_state",
             "properties_sha256", "position_sha256", "box", "photo", "reason", "provenance"}
HOLD_OPTIONAL = {"evidence_image", "audit_photo_sha256"}
PHOTO_KEYS = ("link", "captured_at", "creator", "license")

UNRESOLVED, NOT_FLAGGED, UNCLEAR, NOT_REVIEWED = "unresolved", "not_flagged", "unclear", "not_reviewed"
HOLD_REASON = "audit_hold"  # the attribute reason of a held row (eval.asset_check.REASONS)
NEXT_STEP = "A desk identity check or a new photo may resolve it."
NOT_REVIEWED_WHY = ("This source has not been reviewed for photo clarity. The models' answer stands as they gave it; "
                    "that is not verification.")


def hold_review_key(hold: dict) -> str:
    """The review key a hold's bound values make, to compare with the key it names."""
    return ledger_review.review_key(hold["scan_id"], hold["item"], hold["layer"], hold["record_key"], hold["record_id"],
                                    hold["properties_sha256"], hold["photo"], hold["box"], hold["position_sha256"])


def make_hold(row: dict, field: str, reason: str, model_attribute_state: str, scan_id: int, **extra) -> dict:
    """A hold that binds the exact source of a row of an Asset Check, to put in a policy."""
    src, ev = row["ledger_source"], row.get("evidence") or {}
    return {"id": f"{src['record_id']}-{field}".lower(), "review_key": src["review_key"], "scan_id": scan_id,
            "item": row["item"], "layer": src["layer"], "record_key": src["record_key"], "record_id": src["record_id"],
            "field": field, "model_attribute_state": model_attribute_state,
            "properties_sha256": src["properties_sha256"], "position_sha256": src.get("position_sha256"),
            "box": row.get("box"), "photo": {k: ev.get(k) for k in PHOTO_KEYS}, "reason": reason,
            "provenance": PROVENANCE, **extra}


def check_policy(policy) -> dict:
    """The policy, or a ValueError naming every problem: a malformed policy applies no hold at all."""
    problems: list[str] = []
    if not isinstance(policy, dict):
        raise ValueError("policy is not an object")
    problems += [f"unknown key {k!r}" for k in sorted(set(policy) - POLICY_KEYS)]
    problems += [f"missing key {k!r}" for k in sorted(POLICY_KEYS - set(policy))]
    if policy.get("schema_version") != POLICY_VERSION:
        problems.append(f"schema_version must be {POLICY_VERSION}")
    if policy.get("provenance") != PROVENANCE:
        problems.append(f"provenance must be {PROVENANCE!r}")
    if not isinstance(policy.get("audit"), dict) or not all(policy["audit"].get(k) for k in ("id", "date")):
        problems.append("audit needs an id and a date")
    holds = policy.get("holds")
    seen: set[str] = set()
    for n, hold in enumerate(holds if isinstance(holds, list) else [], 1):
        problems += _hold_problems(n, hold, seen)
    if not isinstance(holds, list):
        problems.append("holds is not a list")
    if problems:
        raise ValueError("; ".join(problems))
    return policy


def _hold_problems(n: int, hold, seen: set[str]) -> list[str]:
    label = f"hold {n}"
    if not isinstance(hold, dict):
        return [f"{label} is not an object"]
    problems = [f"{label}: unknown key {k!r}" for k in sorted(set(hold) - HOLD_KEYS - HOLD_OPTIONAL)]
    problems += [f"{label}: missing key {k!r}" for k in sorted(HOLD_KEYS - set(hold))]
    if problems:
        return problems
    for key in ("id", "review_key", "item", "layer", "record_key", "record_id", "field", "properties_sha256",
                "position_sha256", "reason"):
        if not isinstance(hold[key], str) or not hold[key].strip():
            problems.append(f"{label}: {key} must be text")
    if hold["provenance"] != PROVENANCE:
        problems.append(f"{label}: provenance must be {PROVENANCE!r}")
    if hold["model_attribute_state"] not in attribute_state.STATES:
        problems.append(f"{label}: model_attribute_state must be one of {', '.join(attribute_state.STATES)}")
    if not isinstance(hold["photo"], dict) or not all(hold["photo"].get(k) for k in PHOTO_KEYS):
        problems.append(f"{label}: photo needs {', '.join(PHOTO_KEYS)}")
    if not isinstance(hold["scan_id"], int) or isinstance(hold["scan_id"], bool):
        problems.append(f"{label}: scan_id must be a whole number")
    if problems:
        return problems
    if hold["review_key"] in seen:
        problems.append(f"{label}: duplicate review_key")
    seen.add(hold["review_key"])
    try:
        if hold_review_key(hold) != hold["review_key"]:
            problems.append(f"{label}: review_key does not match the values it binds")
    except (KeyError, TypeError, ValueError):
        problems.append(f"{label}: review_key cannot be rebuilt from its values")
    return problems


def load_policy(path: Path | str | None = None) -> dict:
    """The checked-in policy (or the one at path), validated."""
    return check_policy(json.loads(Path(path or DEFAULT_POLICY).read_text(encoding="utf-8")))


def find_hold(row: dict, policy: dict, attribute: dict | None, field: str | None) -> dict | None:
    """The hold that binds this row's exact source, or None. Every value a hold was made from must still be the row's:
    its review key, record, layer, item, the record's properties and position, the photo and its box, the field, and
    the models' answer it was made against."""
    src = row.get("ledger_source")
    if not src or not attribute or not field:
        return None
    hold = next((h for h in policy["holds"] if h["review_key"] == src.get("review_key")), None)
    if hold is None:
        return None
    ev = row.get("evidence") or {}
    bound = (hold["item"] == row.get("item") and hold["layer"] == src.get("layer")
             and hold["record_key"] == src.get("record_key") and hold["record_id"] == src.get("record_id")
             and hold["properties_sha256"] == src.get("properties_sha256")
             and hold["position_sha256"] == src.get("position_sha256")
             and hold["photo"]["link"] == ev.get("link") and hold["photo"]["captured_at"] == ev.get("captured_at")
             and hold["box"] == row.get("box") and hold["field"] == field
             and hold["model_attribute_state"] == attribute.get("state"))
    return hold if bound else None


def identity_reason(record: str, near: list[dict]) -> str:
    """Why the nearby records require a conservative identity check."""
    others = ", ".join(f"{n['record_id']} ({n['metres']:.1f} m, {n['seconds']:.0f} s)" for n in near)
    return (f"Identity not settled: {record} is within {ledger_review.NEIGHBOR_M:g} m by the city's positions and "
            f"{ledger_review.NEIGHBOR_S:g} s in photo time of {others}, so it has not been established which record this "
            "photo shows. Proximity alone does not prove a mismatch or a duplicate. They may be different objects; both records stay on the list and "
            "nothing is merged.")


def groups_of(pairs_by_record: dict[tuple[str, str], list[str]]) -> list[list[str]]:
    """The records that compete with each other, as sorted lists of ids (connected through their pairs)."""
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (item, record), others in pairs_by_record.items():
        for other in others:
            parent[find((item, record))] = find((item, other))
    members: dict[tuple[str, str], list[str]] = {}
    for item, record in list(parent):
        members.setdefault(find((item, record)), []).append(record)
    return sorted(sorted(set(m)) for m in members.values())


def quality_cells(hold: dict | None) -> dict:
    """evidence_quality and its reason for a row: unclear where a hold binds it, otherwise only not reviewed."""
    if hold:
        return {"evidence_quality": UNCLEAR, "evidence_quality_reason": f"{PROVENANCE}: {hold['reason']}"}
    return {"evidence_quality": NOT_REVIEWED, "evidence_quality_reason": NOT_REVIEWED_WHY}
