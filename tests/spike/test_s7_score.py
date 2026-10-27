from spike.s7_score import is_flagged, is_structural, score

LABELS = {
    "structural": {"1": "missing blocks", "2": "cut by manhole"},
    "worn": {"3": "faded"},
    "intact": [4, 5],
}


def test_structural_means_abrupt_end_or_broken_or_missing_blocks():
    assert is_structural({"line_end": "abrupt", "damage": "none"})
    assert is_structural({"line_end": "none", "damage": "missing"})
    assert is_structural({"line_end": "none", "damage": "broken"})
    assert not is_structural({"line_end": "normal", "damage": "worn"})
    assert not is_structural({"error": "timeout"})


def test_flagged_also_counts_worn_and_covered():
    assert is_flagged({"line_end": "none", "damage": "worn"})
    assert is_flagged({"line_end": "none", "damage": "covered"})
    assert not is_flagged({"line_end": "normal", "damage": "none"})
    assert not is_flagged({"line_end": "unclear", "damage": "unclear"})


def test_score_counts_hits_false_flags_errors_and_missing_rows():
    rows = {
        1: {"line_end": "none", "damage": "missing"},
        2: {"line_end": "none", "damage": "none"},
        3: {"line_end": "none", "damage": "worn"},
        4: {"line_end": "abrupt", "damage": "none"},
        5: {"error": "RateLimitError"},
    }
    s = score(rows, LABELS)
    assert (s["structural_hit"], s["structural_total"]) == (1, 2)
    assert (s["worn_hit"], s["worn_total"]) == (1, 1)
    assert (s["intact_flagged"], s["intact_total"]) == (1, 2)
    assert s["errors"] == [5]
    assert s["missing"] == []


def test_score_reports_labelled_photos_without_a_row():
    s = score({1: {"line_end": "abrupt", "damage": "none"}}, LABELS)
    assert s["missing"] == [2, 3, 4, 5]
