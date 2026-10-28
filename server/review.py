"""A reviewer's decision -> the fields to write (spec 5 step 3). The model's own outputs are never overwritten:
each decision is appended to corrections with what changed, the old values and the seconds it took, which is the
review time in the speed comparison (spec 17). The only entrance field a reviewer edits is the step count (spec
revision 4).

"field_check" is a person's answer too: they looked at the photo and cannot tell either, so the row stays, marked for a
site visit (review_status "field_check"), and their time still counts."""
from datetime import datetime, timezone

EDITABLE = ("step_count",)  # spec revision 4: the only entrance field a reviewer edits
FIELD_CHECK = "field_check"  # the action and the review_status it leaves
ENTRANCE_ACTIONS = ("confirm", "correct", FIELD_CHECK)
ISSUE_ACTIONS = ("confirm", "reject", "answer", FIELD_CHECK)
REVIEWED = ("verified", "rejected", FIELD_CHECK)  # statuses a person has given; such a row is not reviewed again
UNDO_WINDOW_S = 60  # a decision can be taken back this long after it was made (the card's "Saved · Undo")


def entrance_update(row: dict, action: str, changes: dict, seconds: float, at: str) -> dict:
    if action not in ENTRANCE_ACTIONS:
        raise ValueError(f"entrance action must be one of {ENTRANCE_ACTIONS}, not {action!r}")
    changes = ({k: v for k, v in changes.items() if k in EDITABLE and v is not None}
               if action == "correct" else {})
    fields: dict = dict(changes)
    fields["review_status"] = FIELD_CHECK if action == FIELD_CHECK else "verified"
    fields["corrections"] = [*row["corrections"], {"at": at, "action": action, "changes": changes,
                                                   "before": {k: row[k] for k in changes}, "seconds": seconds,
                                                   "status_before": row.get("review_status")}]
    return fields


def issue_update(row: dict, action: str, seconds: float, at: str, answer: str | None = None) -> dict:
    """confirm/reject a sidewalk row; answer sets a check row's yes/no (design 2026-10-28 §6), keeping the old one."""
    if action not in ISSUE_ACTIONS:
        raise ValueError(f"issue action must be one of {ISSUE_ACTIONS}, not {action!r}")
    if action == "answer":
        if answer not in ("yes", "no"):
            raise ValueError(f"an answer is 'yes' or 'no', not {answer!r}")
        return {"object_label": answer, "review_status": "verified",
                "corrections": [*row["corrections"], {"at": at, "action": "answer", "answer": answer,
                                                      "before": row.get("object_label"), "seconds": seconds,
                                                      "status_before": row.get("review_status")}]}
    status = {"confirm": "verified", "reject": "rejected", FIELD_CHECK: FIELD_CHECK}[action]
    return {"review_status": status,
            "corrections": [*row["corrections"], {"at": at, "action": action, "seconds": seconds,
                                                  "status_before": row.get("review_status")}]}


def undo(row: dict, kind: str, now: str, window_s: float = UNDO_WINDOW_S) -> tuple[dict, float]:
    """Take back the row's last decision, made at most window_s ago: (fields to write, its seconds to take off the
    scan's review time). The model's outputs were never changed, so only what the decision changed comes back.
    kind: "issue" or "entrance". Raises ValueError when there is nothing to take back or it is too late."""
    if not row["corrections"]:
        raise ValueError("There is no decision to undo")
    last = row["corrections"][-1]
    made, at = _seconds(last["at"]), _seconds(now)
    if at - made > window_s:
        raise ValueError(f"A decision can be undone for {window_s:.0f} seconds after it was made")
    # A decision made before status_before was recorded was made on an item waiting for review.
    fields: dict = {"review_status": last.get("status_before") or "needs_review", "corrections": row["corrections"][:-1]}
    if kind == "issue" and last["action"] == "answer":
        fields["object_label"] = last.get("before")
    if kind == "entrance":
        fields.update(last.get("before") or {})
    return fields, float(last.get("seconds") or 0)


def _seconds(stamp: str) -> float:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
