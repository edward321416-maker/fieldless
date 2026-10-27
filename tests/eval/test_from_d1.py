from eval.from_d1 import convert


def test_convert_maps_photo_numbers_to_image_ids_and_drops_photos_not_in_the_scan():
    frames_json = {"frames": [{"id": f"m{t}"} for t in range(100)]}
    d1 = {"clips": {"street_b.mp4": {
        "entrances": [{"t": 23, "step_count": 3}, {"t": 99, "step_count": 0}],
        "obstacles": [{"t_start": 4, "t_end": 5, "label": "bicycle", "on_tactile": True},
                      {"t_start": 7, "t_end": 7, "label": "cone", "on_tactile": False}]}}}
    s7 = {"structural": {"45": "fragment", "63": "missing", "64": "missing", "81": "cover", "82": "cover"}}
    scan_ids = {f"m{t}" for t in range(100)} - {"m64", "m99"}

    t = convert(d1, frames_json, s7, "plan1-live", 1, scan_ids)

    assert t["format"] == "threshold-survey-truth/1" and t["labeling_seconds"] == 0
    assert t["photos"]["m63"]["defects"] == [{"type": "missing_blocks"}] and "m64" not in t["photos"]
    assert t["photos"]["m82"]["defects"] == [{"type": "cover_cut"}] and "m45" not in t["photos"]
    assert t["photos"]["m4"]["obstructions"] == [{"label": "bicycle"}] and "m7" not in t["photos"]
    assert t["photos"]["m23"]["entrances"] == [{"steps": 3, "sill_unsure": False}] and "m99" not in t["photos"]
