import os

import pytest

from pipeline import survey_items

US = '''
standard = "US"

[path]
surface = "sidewalk"
name = "the sidewalk walking path"
describe = "sidewalks"
objects = ["traffic cone"]
fixed = []

[[check]]
id = "curb_ramp_warning"
find = "curb ramp"
target = "curb ramp"
ask = "Does it have domes?"
report_when = "no"
label_yes = "Curb ramp with detectable warning"
label_no = "Curb ramp without detectable warning"
'''

INVENTORY = '''
standard = "US"
[path]
surface = "sidewalk"
name = "the sidewalk walking path"
describe = "sidewalks"
[[check]]
id = "streetlight"
inventory = true
find = "utility pole"
target = "streetlight pole"
ask = "Is this a streetlight pole (a pole with a lamp head)?"
label_yes = "Streetlight pole"
'''


def test_parse_reads_the_path_and_the_checks():
    std = survey_items.parse(US, "us.toml")
    assert std.code == "US" and std.tactile_defects is False
    assert std.path == survey_items.PathSpec("sidewalk", "the sidewalk walking path", "sidewalks",
                                             ("traffic cone",), ())
    (check,) = std.checks
    assert check.label("no") == ("✖", "Curb ramp without detectable warning")
    assert check.label("yes") == ("✓", "Curb ramp with detectable warning")
    assert check.label("unclear") == ("?", "Curb ramp: unclear from the photo")
    assert check.label(None) == ("?", "Curb ramp: unclear from the photo")
    assert std.check("curb_ramp_warning") is check and std.check("missing_blocks") is None
    assert check.target == "curb ramp"


def test_group_m_is_the_metres_of_street_one_row_may_cover():
    assert survey_items.parse(US, "us.toml").checks[0].group_m is None
    assert survey_items.parse(US + "group_m = 15", "us.toml").checks[0].group_m == 15.0
    assert isinstance(survey_items.parse(US + "group_m = 15", "us.toml").checks[0].group_m, float)
    assert survey_items.parse(US + "group_m = 7.5", "us.toml").checks[0].group_m == 7.5
    for bad in ("0", "-3", '"x"', "true"):
        with pytest.raises(ValueError, match="group_m must be a positive number of metres"):
            survey_items.parse(US + f"group_m = {bad}", "us.toml")


def test_a_check_without_target_uses_find_and_icons_follow_report_when():
    plain = US.replace('find = "curb ramp"', 'find = "curb cut"').replace('target = "curb ramp"', "")
    check = survey_items.parse(plain, "us.toml").checks[0]
    assert check.target == "curb cut" and check.label("unclear") == ("?", "Curb cut: unclear from the photo")
    defect_when_yes = survey_items.parse(US.replace('report_when = "no"', 'report_when = "yes"'), "us.toml").checks[0]
    assert defect_when_yes.label("yes")[0] == "✖" and defect_when_yes.label("no")[0] == "✓"
    own_icon = survey_items.parse(US + 'icon_yes = "A"', "us.toml")  # the check is the last table
    assert own_icon.checks[0].label("yes")[0] == "A" and own_icon.checks[0].label("no")[0] == "✖"


def test_an_inventory_check_needs_no_defect_answer():
    (check,) = survey_items.parse(INVENTORY, "us.toml").checks
    assert check.inventory and check.report_when == ""
    assert check.label("yes") == ("●", "Streetlight pole")
    assert check.label_no == "Not a streetlight pole"
    std = survey_items.parse(INVENTORY, "us.toml")
    assert not std.is_defect({"issue_type": "streetlight", "object_label": "yes"})
    assert not std.is_defect({"issue_type": "streetlight", "object_label": ""})
    with pytest.raises(ValueError, match="report_when"):
        survey_items.parse(INVENTORY.replace('label_yes', 'report_when = "no"\nlabel_yes'), "us.toml")


@pytest.mark.parametrize("change, message", [
    (('report_when = "no"', 'report_when = "maybe"'), "report_when"),
    (('find = "curb ramp"\n', ''), "missing 'find'"),
    (('id = "curb_ramp_warning"', 'id = "obstruction"'), "reserved"),
    (('id = "curb_ramp_warning"', 'id = "Curb ramp"'), "lower case"),
    (('name = "the sidewalk walking path"\n', ''), "missing 'name'"),
])
def test_parse_refuses_a_broken_standard(change, message):
    with pytest.raises(ValueError, match=message):
        survey_items.parse(US.replace(*change), "us.toml")


def test_check_ids_must_not_repeat():
    twice = US + US[US.index("[[check]]"):]
    with pytest.raises(ValueError, match="repeat"):
        survey_items.parse(twice, "us.toml")


def test_the_shipped_standards_load():
    kr, us = survey_items.load("KR"), survey_items.load("us")
    assert kr.tactile_defects and kr.checks == ()
    assert kr.path.surface == "yellow tactile paving" and kr.path.fixed == ("bollard", "pole")
    assert kr.path.objects == ("electric scooter", "bicycle")
    assert not us.tactile_defects and us.path.surface == "sidewalk" and us.path.fixed == ()
    assert [c.id for c in us.checks] == ["curb_ramp_warning", "utility_pole", "street_tree", "litter_can", "parking_pay_station"]
    assert us.checks[0].report_when == "no" and not us.checks[0].inventory
    assert all(c.inventory and c.report_when == "" for c in us.checks[1:])


def test_load_reads_an_edited_standard_again(tmp_path, monkeypatch):
    monkeypatch.setattr(survey_items, "FOLDER", tmp_path)
    file = tmp_path / "us.toml"
    file.write_text(US, encoding="utf-8")
    assert survey_items.load("us").checks[0].ask == "Does it have domes?"
    file.write_text(US.replace("Does it have domes?", "Is it yellow?"), encoding="utf-8")
    stat = file.stat()
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))  # a coarse clock may keep the same mtime
    assert survey_items.load("US").checks[0].ask == "Is it yellow?"


def test_an_unknown_standard_is_an_error():
    with pytest.raises(ValueError, match="no survey standard"):
        survey_items.load("FR")


@pytest.mark.parametrize("bbox, code", [
    ([127.055161, 37.546707, 127.058991, 37.550062], "KR"),  # Seongsu
    ([126.9815, 37.5605, 126.9875, 37.5650], "KR"),  # Myeongdong
    ([-122.3230, 47.6130, -122.3170, 47.6175], "US"),  # Seattle Capitol Hill
    ([-73.9790, 40.7550, -73.9730, 40.7595], "US"),  # New York Midtown
])
def test_standard_for_picks_by_the_area_centre(bbox, code):
    assert survey_items.standard_for(bbox) == code


def test_is_defect_follows_the_standard():
    kr, us = survey_items.load("KR"), survey_items.load("US")
    assert kr.is_defect({"issue_type": "cover_cut", "object_label": None})
    assert kr.is_defect({"issue_type": "fixed_obstruction", "object_label": "pole"})
    assert not kr.is_defect({"issue_type": "obstruction", "object_label": "scooter"})
    assert us.is_defect({"issue_type": "curb_ramp_warning", "object_label": "no"})
    assert not us.is_defect({"issue_type": "curb_ramp_warning", "object_label": "yes"})
    assert not us.is_defect({"issue_type": "curb_ramp_warning", "object_label": "unclear"})
    assert not us.is_defect({"issue_type": "obstruction", "object_label": "traffic cone"})


def test_judge_defaults_to_spark_and_glimmer_is_only_for_inventory_checks():
    assert survey_items.parse(INVENTORY, "us.toml").checks[0].judge == "spark"
    glimmer = survey_items.parse(INVENTORY.replace('inventory = true', 'inventory = true\njudge = "glimmer"'), "us.toml")
    assert glimmer.checks[0].judge == "glimmer"
    with pytest.raises(ValueError, match="judge"):
        survey_items.parse(INVENTORY.replace('inventory = true', 'inventory = true\njudge = "gpt"'), "us.toml")
    defect = survey_items.load("US").check("curb_ramp_warning")
    assert defect.judge == "spark"
    us = (survey_items.FOLDER / "us.toml").read_text(encoding="utf-8")
    ramp = us.replace('id = "curb_ramp_warning"', 'id = "curb_ramp_warning"\njudge = "glimmer"', 1)
    with pytest.raises(ValueError, match="inventory"):
        survey_items.parse(ramp, "us.toml")


def test_a_check_may_name_its_map_icon_and_a_short_question():
    plain = survey_items.parse(INVENTORY, "us.toml").checks[0]
    assert plain.map_icon == "" and plain.question == ""
    named = survey_items.parse(INVENTORY.replace("inventory = true", 'inventory = true\nmap_icon = "street-light"\n'
                                                 'question = "Is this a streetlight pole?"'), "us.toml").checks[0]
    assert named.map_icon == "street-light" and named.question == "Is this a streetlight pole?"
    with pytest.raises(ValueError, match="map_icon"):
        survey_items.parse(INVENTORY.replace("inventory = true", 'inventory = true\nmap_icon = "Tree Icon"'), "us.toml")


def test_a_check_is_found_by_photos_unless_it_says_video_and_video_has_no_group_m():
    assert survey_items.parse(US, "us.toml").checks[0].find_by == "photos"
    assert survey_items.parse(US + 'find_by = "video"', "us.toml").checks[0].find_by == "video"
    with pytest.raises(ValueError, match="find_by must be one of"):
        survey_items.parse(US + 'find_by = "film"', "us.toml")
    with pytest.raises(ValueError, match="one row per object"):
        survey_items.parse(US + 'find_by = "video"\ngroup_m = 8', "us.toml")
