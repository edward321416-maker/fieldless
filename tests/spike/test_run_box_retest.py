from spike import run_box_retest as rb


def test_to_pixels_scales_the_0_to_1000_box_to_the_image():
    assert rb.to_pixels({"x1": 20, "y1": 780, "x2": 220, "y2": 998}, 2048, 1536) == (40, 1198, 450, 1532)


def test_to_pixels_clamps_to_the_last_pixel():
    assert rb.to_pixels({"x1": 0, "y1": 0, "x2": 1000, "y2": 1200}, 2048, 1536) == (0, 0, 2047, 1535)


def test_to_pixels_rejects_an_empty_or_inverted_box():
    assert rb.to_pixels({"x1": 0, "y1": 0, "x2": 0, "y2": 0}, 2048, 1536) is None
    assert rb.to_pixels({"x1": 500, "y1": 10, "x2": 400, "y2": 90}, 2048, 1536) is None


def test_box_prompt_numbers_objects_from_one_on_frame_zero():
    text = rb.box_prompt([(1, 2, 3, 4), (5, 6, 7, 8)], 2048, 1536)
    assert text == ("<0f>1<|box;x1=1;y1=2;x2=3;y2=4;w=2048;h=1536|>,"
                    "2<|box;x1=5;y1=6;x2=7;y2=8;w=2048;h=1536|>")


def test_box_prompt_keeps_at_most_16_objects():
    text = rb.box_prompt([(0, 0, 9, 9)] * 20, 100, 100)
    assert text.count("<|box") == rb.MAX_BOXES == 16


def test_negative_photos_takes_every_nth_photo_outside_the_ranges():
    assert rb.negative_photos(12, [(2.0, 4.0), (8.0, 8.0)], step=2) == [0, 5, 7, 10]


def test_summarize_rates_positives_and_negatives_separately_and_skips_errors():
    rows = [
        {"positive": True, "concept": True, "box": True},
        {"positive": True, "concept": False, "box": True},
        {"positive": True, "concept": None, "box": False},  # concept request failed: not scored
        {"positive": False, "concept": False, "box": True},
        {"positive": False, "concept": False, "box": False},
    ]
    s = rb.summarize_rows(rows)
    assert s["concept"] == {"hit_rate": 0.5, "positives": 2, "false_positive_rate": 0.0, "negatives": 2}
    assert s["box"] == {"hit_rate": 2 / 3, "positives": 3, "false_positive_rate": 0.5, "negatives": 2}
    # either = concept or box; a row is unscored when one method failed and the other did not hit
    assert s["either"] == {"hit_rate": 1.0, "positives": 2, "false_positive_rate": 0.5, "negatives": 2}


def test_summarize_reports_none_when_nothing_was_scored():
    s = rb.summarize_rows([{"positive": True, "concept": None, "box": None}])
    assert s["concept"]["hit_rate"] is None and s["concept"]["positives"] == 0
