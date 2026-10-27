from eval.spans import match_points, match_spans, runs


def test_runs_merge_consecutive_photos():
    assert runs([5, 3, 4, 9, 9]) == [(3, 5), (9, 9)]
    assert runs([]) == []


def test_spans_count_hits_found_false_and_missed_with_tolerance():
    out = match_spans(pred=[(4, 4), (20, 21)], truth=[(5, 6), (12, 12)], tol=1)
    assert (out["predicted"], out["truth"], out["hits"], out["found"]) == (2, 2, 1, 1)
    assert out["false"] == [(20, 21)] and out["missed"] == [(12, 12)]


def test_two_predictions_on_one_spot_are_both_hits_and_find_it_once():
    out = match_spans(pred=[(5, 5), (6, 6)], truth=[(5, 6)], tol=0)
    assert (out["hits"], out["found"]) == (2, 1)


def test_points_match_one_to_one_and_maximally():
    assert match_points([2, 5], [4, 6], tol=1) == [(1, 0)]
    assert sorted(match_points([2, 5], [4, 6], tol=2)) == [(0, 0), (1, 1)]
    assert match_points([10, 10], [10], tol=0) == [(0, 0)]


def test_points_prefer_the_nearer_truth_when_the_count_is_the_same():
    # Myeongdong idx 172-173: a currency exchange (1 step) next to a bank (3 steps). The prediction at 173 is the bank.
    assert match_points([173], [172, 173], tol=1) == [(0, 1)]
    assert sorted(match_points([172, 173], [172, 173], tol=1)) == [(0, 0), (1, 1)]
