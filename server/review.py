"""A reviewer's decision -> the fields to write (spec 5 step 3). The model's own outputs are never overwritten:
each decision is appended to corrections with what changed, the old values and the seconds it took, which is the
review time in the speed comparison (spec 17). The only entrance field a reviewer edits is the step count (spec
revision 4).

"field_check" is a person's answer too: they looked at the photo and cannot tell either, so the row stays, marked for a
site visit (review_status "field_check"), and their time still counts."""

EDITABLE = ("step_count",)  # spec revision 4: the only entrance field a reviewer edits
FIELD_CHECK = "field_check"  # the action and the review_status it leaves
ENTRANCE_ACTIONS = ("confirm", "correct", FIELD_CHECK)
ISSUE_ACTIONS = ("confirm", "reject", "answer", FIELD_CHECK)
REVIEWED = ("verified", "rejected", FIELD_CHECK)  # statuses a person has given; such a row is not reviewed again


def entrance_update(row: dict, action: str, changes: dict, seconds: float, at: str) -> dict:
    if action not in ENTRANCE_ACTIONS:
        raise ValueError(f"entrance action must be one of {ENTRANCE_ACTIONS}, not {action!r}")
    changes = ({k: v for k, v in changes.items() if k in EDITABLE and v is not None}
               if action == "correct" else {})
    fields: dict = dict(changes)
    fields["review_status"] = FIELD_CHECK if action == FIELD_CHECK else "verified"
    fields["corrections"] = [*row["corrections"], {"at": at, "action": action, "changes": changes,
                                                   "before": {k: row[k] for k in changes}, "seconds": seconds}]
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
                                                      "before": row.get("object_label"), "seconds": seconds}]}
    status = {"confirm": "verified", "reject": "rejected", FIELD_CHECK: FIELD_CHECK}[action]
    return {"review_status": status,
            "corrections": [*row["corrections"], {"at": at, "action": action, "seconds": seconds}]}
