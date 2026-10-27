"""A reviewer's decision -> the fields to write (spec 5 step 3). The model's own outputs are never overwritten:
each decision is appended to corrections with what changed, the old values and the seconds it took, which is the
review time in the speed comparison (spec 17). The only entrance field a reviewer edits is the step count (spec
revision 4)."""

EDITABLE = ("step_count",)  # spec revision 4: the only entrance field a reviewer edits
ENTRANCE_ACTIONS = ("confirm", "correct")
ISSUE_ACTIONS = ("confirm", "reject")


def entrance_update(row: dict, action: str, changes: dict, seconds: float, at: str) -> dict:
    if action not in ENTRANCE_ACTIONS:
        raise ValueError(f"entrance action must be one of {ENTRANCE_ACTIONS}, not {action!r}")
    changes = ({k: v for k, v in changes.items() if k in EDITABLE and v is not None}
               if action == "correct" else {})
    fields: dict = dict(changes)
    fields["review_status"] = "verified"
    fields["corrections"] = [*row["corrections"], {"at": at, "action": action, "changes": changes,
                                                   "before": {k: row[k] for k in changes}, "seconds": seconds}]
    return fields


def issue_update(row: dict, action: str, seconds: float, at: str) -> dict:
    if action not in ISSUE_ACTIONS:
        raise ValueError(f"issue action must be one of {ISSUE_ACTIONS}, not {action!r}")
    return {"review_status": "verified" if action == "confirm" else "rejected",
            "corrections": [*row["corrections"], {"at": at, "action": action, "seconds": seconds}]}
