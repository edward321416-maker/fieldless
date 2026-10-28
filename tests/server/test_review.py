import pytest

from server import review

AT = "2026-10-31T10:00:00Z"


def row(**overrides):
    base = {"step_count": 1, "review_status": "needs_review", "corrections": []}
    return {**base, **overrides}


def test_confirm_keeps_values_and_records_the_time():
    fields = review.entrance_update(row(), "confirm", {"step_count": 3}, 4.5, AT)
    assert fields == {"review_status": "verified",
                      "corrections": [{"at": AT, "action": "confirm", "changes": {}, "before": {}, "seconds": 4.5,
                                       "status_before": "needs_review"}]}


def test_correct_writes_the_step_count_and_keeps_the_old_value():
    fields = review.entrance_update(row(), "correct", {"step_count": 0}, 12.0, AT)
    assert fields["step_count"] == 0 and fields["review_status"] == "verified"
    (entry,) = fields["corrections"]
    assert entry == {"at": AT, "action": "correct", "changes": {"step_count": 0}, "before": {"step_count": 1},
                     "seconds": 12.0, "status_before": "needs_review"}


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
    assert rejected["corrections"] == [{"at": AT, "action": "reject", "seconds": 3.0, "status_before": None}]


def test_only_the_step_count_can_be_changed():
    fields = review.entrance_update(row(), "correct", {"step_count": 2, "evidence": {}, "scan_id": 9}, 1.0, AT)
    assert set(fields) == {"step_count", "review_status", "corrections"}
    assert fields["corrections"][0]["changes"] == {"step_count": 2}


def test_unknown_actions_are_refused():
    with pytest.raises(ValueError):
        review.entrance_update(row(), "reject", {}, 1.0, AT)
    with pytest.raises(ValueError):
        review.issue_update({"corrections": []}, "Confirm", 1.0, AT)


def test_an_answer_sets_the_check_and_keeps_the_previous_one():
    fields = review.issue_update({"corrections": [], "object_label": "unclear"}, "answer", 4.0, AT, "no")
    assert fields == {"object_label": "no", "review_status": "verified",
                      "corrections": [{"at": AT, "action": "answer", "answer": "no", "before": "unclear",
                                       "seconds": 4.0, "status_before": None}]}
    with pytest.raises(ValueError):
        review.issue_update({"corrections": [], "object_label": "unclear"}, "answer", 4.0, AT, "maybe")


def test_a_person_who_cannot_tell_from_the_photo_leaves_the_row_for_a_site_visit():
    issue = {"corrections": [], "object_label": "unclear", "review_status": "needs_review"}
    fields = review.issue_update(issue, "field_check", 6.5, "2026-10-28T09:00:00Z")
    assert fields == {"review_status": "field_check",
                      "corrections": [{"at": "2026-10-28T09:00:00Z", "action": "field_check", "seconds": 6.5,
                                       "status_before": "needs_review"}]}
    entrance = {"corrections": [], "step_count": 1, "review_status": "needs_review"}
    fields = review.entrance_update(entrance, "field_check", {"step_count": 3}, 4.0, "2026-10-28T09:00:00Z")
    assert fields["review_status"] == "field_check" and "step_count" not in fields  # nothing is changed
    assert "field_check" in review.REVIEWED


def test_undo_takes_back_the_last_decision_within_a_minute_and_returns_its_seconds():
    issue = {"corrections": [], "object_label": "unclear", "review_status": "needs_review"}
    answered = {**issue, **review.issue_update(issue, "answer", 4.0, AT, "no")}
    fields, seconds = review.undo(answered, "issue", "2026-10-31T10:00:59Z")
    assert fields == {"review_status": "needs_review", "corrections": [], "object_label": "unclear"} and seconds == 4.0
    with pytest.raises(ValueError, match="60 seconds"):
        review.undo(answered, "issue", "2026-10-31T10:01:01Z")
    with pytest.raises(ValueError, match="no decision"):
        review.undo(issue, "issue", AT)
    entrance = row()
    corrected = {**entrance, **review.entrance_update(entrance, "correct", {"step_count": 0}, 9.0, AT)}
    fields, seconds = review.undo(corrected, "entrance", AT)
    assert fields == {"review_status": "needs_review", "corrections": [], "step_count": 1} and seconds == 9.0
    older = {**issue, "review_status": "rejected", "corrections": [{"at": AT, "action": "reject", "seconds": 2.0}]}
    assert review.undo(older, "issue", AT)[0]["review_status"] == "needs_review"  # made before status_before existed
