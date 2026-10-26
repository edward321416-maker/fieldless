import pytest

from spike.sam_parse import Detection, boxes_intersect, confident, parse_sam_output

IMAGE = (
    "<0f>0<|box;x1=181;y1=365;x2=556;y2=486;w=1516;h=1600|><|mask;x=0;y=0;data=4,4,~0123|>,"
    "1<|box;x1=0;y1=0;x2=758;y2=800;w=1516;h=1600|><|mask;x=0;y=0;data=4,4,!abcd|>"
)
VIDEO = "\n".join([
    "<0f>0<|box;x1=10;y1=20;x2=110;y2=220;w=320;h=334;c=0.91|><|mask;x=0;y=0;c=0.88;data=8,8,~zz|>",
    "<2f>0<|box;x1=12;y1=22;x2=112;y2=222;w=320;h=334;c=0.42|><|mask;x=0;y=0;c=0.40;data=8,8,~zz|>, "
    "2<|box;x1=0;y1=0;x2=32;y2=33;w=320;h=334;c=0.75|><|mask;x=0;y=0;c=0.7;data=8,8,!yy|>",
    "garbage line",
])


def test_parses_image_records():
    dets = parse_sam_output(IMAGE)
    assert [(d.frame, d.obj) for d in dets] == [(0, 0), (0, 1)]
    assert dets[0].x1 == 181 and dets[0].w == 1516 and dets[0].c is None
    assert abs(dets[1].area_ratio - 0.25) < 1e-9


def test_parses_video_frames_ids_and_confidence():
    dets = parse_sam_output(VIDEO)
    assert [(d.frame, d.obj, d.c) for d in dets] == [(0, 0, 0.91), (2, 0, 0.42), (2, 2, 0.75)]


def test_confident_keeps_missing_confidence_and_filters_low():
    dets = parse_sam_output(VIDEO) + parse_sam_output(IMAGE)
    kept = confident(dets, 0.5)
    assert [d.c for d in kept] == [0.91, 0.75, None, None]


def test_boxes_intersect():
    a = Detection(0, 0, 0, 0, 10, 10, 100, 100, None)
    b = Detection(0, 1, 5, 5, 20, 20, 100, 100, None)
    c = Detection(0, 2, 11, 11, 20, 20, 100, 100, None)
    assert boxes_intersect(a, b) and not boxes_intersect(a, c)


def test_empty_and_whitespace_text_returns_empty():
    assert parse_sam_output("") == []
    assert parse_sam_output("  \n\t\n ") == []


def test_missing_height_raises_with_frame_and_record():
    text = "<3f>0<|box;x1=0;y1=0;x2=10;y2=10;w=100|><|mask;x=0;y=0;data=4,4,~0|>"
    with pytest.raises(ValueError) as err:
        parse_sam_output(text)
    msg = str(err.value)
    assert "frame 3" in msg and "'h'" in msg and "<|box;x1=0" in msg


def test_non_numeric_field_raises_with_frame():
    text = "<1f>0<|box;x1=abc;y1=0;x2=10;y2=10;w=100;h=100|>"
    with pytest.raises(ValueError, match="frame 1"):
        parse_sam_output(text)


def test_zero_width_raises_instead_of_dividing_later():
    text = "<1f>0<|box;x1=0;y1=0;x2=10;y2=10;w=0;h=100|>"
    with pytest.raises(ValueError, match="frame 1"):
        parse_sam_output(text)


def test_box_text_without_frame_marker_is_unrecognized():
    text = "0<|box;x1=0;y1=0;x2=1;y2=1;w=10;h=10|>" + "x" * 500
    with pytest.raises(ValueError, match="unrecognized SAM output") as err:
        parse_sam_output(text)
    assert len(str(err.value)) < 200
