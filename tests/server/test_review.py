import pytest

from server import review

AT = "2026-10-31T10:00:00Z"


def row(**overrides):
    base = {"step_count": 1, "review_status": "needs_review", "corrections": []}
    return {**base, **overrides}


def test_confirm_keeps_values_and_records_the_time():
    fields = review.entrance_update(row(), "confirm", {"step_count": 3}, 4.5, AT)
    assert fields == {"review_status": "verified",
                      "corrections": [{"at": AT, "action": "confirm", "changes": {}, "before": {}, "seconds": 4.5}]}


def test_correct_writes_the_step_count_and_keeps_the_old_value():
    fields = review.entrance_update(row(), "correct", {"step_count": 0}, 12.0, AT)
    assert fields["step_count"] == 0 and fields["review_status"] == "verified"
    (entry,) = fields["corrections"]
    assert entry == {"at": AT, "action": "correct", "changes": {"step_count": 0}, "before": {"step_count": 1},
                     "seconds": 12.0}


def test_correct_without_a_new_count_changes_nothing_but_the_status():
    fields = review.entrance_update(row(), "correct", {"step_count": None}, 2.0, AT)
    assert "step_count" not in fields and fields["corrections"][0]["changes"] == {}


def test_corrections_accumulate():
    first = review.entrance_update(row(), "confirm", {}, 1.0, AT)["corrections"]
    second = review.entrance_update(row(corrections=first), "confirm", {}, 2.0, AT)["corrections"]
    assert [c["seconds"] for c in second] == [1.0, 2.0]


def test_issue_confirm_and_reject():
    assert review.issue_update({"corrections": []}, "confirm", 3.0, AT)["review_status"] == "verified"
    rejected = review.issue_update({"corrections": []}, "reject", 3.0, AT)
    assert rejected["review_status"] == "rejected"
    assert rejected["corrections"] == [{"at": AT, "action": "reject", "seconds": 3.0}]


def test_only_the_step_count_can_be_changed():
    fields = review.entrance_update(row(), "correct", {"step_count": 2, "evidence": {}, "scan_id": 9}, 1.0, AT)
    assert set(fields) == {"step_count", "review_status", "corrections"}
    assert fields["corrections"][0]["changes"] == {"step_count": 2}


def test_unknown_actions_are_refused():
    with pytest.raises(ValueError):
        review.entrance_update(row(), "reject", {}, 1.0, AT)
    with pytest.raises(ValueError):
        review.issue_update({"corrections": []}, "Confirm", 1.0, AT)
