import tomllib

from assist.items import (DRAFT_PROMPT, DRAFT_SCHEMA, INVENTORY_SCHEMA, UNCLEAR, inventory_prompt, to_toml, validate,
                          validate_inventory, with_unclear)

RULE = ("406.5 Location. Curb ramps shall be located so that they do not project into vehicular traffic lanes.\n"
        "705.1.3 Contrast. Detectable warning surfaces shall contrast visually with adjacent walking surfaces.")


def item(**kw):
    base = {"id": "warning_contrast", "target": "curb ramp", "find": "curb cut",
            "ask": "Does the detectable warning contrast visually with the sidewalk around it?", "report_when": "no",
            "label_yes": "Warning contrasts", "label_no": "Warning does not contrast", "section": "705.1.3",
            "page": 196, "quote": "Detectable warning surfaces shall contrast visually with adjacent walking surfaces"}
    return {**base, **kw}


def test_valid_items_pass_and_keep_a_clean_section_number():
    items, problems = validate({"checkable": [item(section="705.1.3 Contrast")], "not_checkable": []}, RULE)
    assert problems == [] and items[0]["section"] == "705.1.3"


def test_bad_ids_answers_and_made_up_citations_are_dropped_with_reasons():
    draft = {"checkable": [item(id="Warning Contrast"), item(id="obstruction"), item(report_when="maybe"),
                           item(id="ramp_slope", section="406.1"),
                           item(id="made_up", quote="Ramps shall be painted blue on every Sunday morning"),
                           item(), item()], "not_checkable": []}
    items, problems = validate(draft, RULE)
    assert [i["id"] for i in items] == ["warning_contrast"]
    text = "\n".join(problems)
    for reason in ("lower case", "reserved", "report_when", "section '406.1'", "quote is not", "id repeated"):
        assert reason in text


def test_toml_round_trips_and_carries_the_source():
    out = to_toml([{**item(), "test_ask": "right on 9 of 9"}], ["drafted by Muse Spark"])
    check = tomllib.loads(out)["check"][0]
    assert check["id"] == "warning_contrast" and check["report_when"] == "no" and check["find"] == "curb cut"
    assert check["target"] == "curb ramp"  # the noun Spark is asked about (pipeline.survey_items key `target`)
    assert check["source_section"] == "705.1.3" and check["source_page"] == 196
    assert check["test_ask"] == "right on 9 of 9" and out.startswith("# drafted by Muse Spark")


def test_the_draft_schema_requires_the_fields_the_pipeline_and_the_checks_need():
    required = set(DRAFT_SCHEMA["properties"]["checkable"]["items"]["required"])
    assert {"id", "target", "find", "ask", "report_when", "label_yes", "label_no", "section", "page",
            "quote"} <= required


def test_the_draft_prompt_asks_for_a_definition_and_the_unclear_rule():
    # The Seattle scan showed driveways answered as curb ramps and far corners answered anyway (2026-10-28).
    text = DRAFT_PROMPT.format(title="t", max_items=3)
    assert "Start the ask with a one-sentence definition" in text and "set visible to false" in text
    assert "too far away or too small to see, answer unclear" in text


POLE_ASK = ("A streetlight pole is a pole that carries a lamp head over the street; plain utility poles, sign posts "
            "and trees are not. Is this a streetlight pole?")


def pole(**kw):
    base = {"id": "streetlight_pole", "target": "streetlight pole", "find": "utility pole",
            "alternatives": ["pole", "lamp post", "street light"], "ask": f"{POLE_ASK} {UNCLEAR}",
            "label_yes": "Streetlight pole", "label_no": "Not a streetlight pole"}
    return {**base, **kw}


def test_inventory_toml_round_trips_with_inventory_true_and_no_report_when():
    out = to_toml([{**pole(inventory=True), "test_confirm": "kept 9 of 10"}], ["drafted by Muse Spark"])
    check = tomllib.loads(out)["check"][0]
    assert check["id"] == "streetlight_pole" and check["inventory"] is True
    assert "report_when" not in check and not any(k.startswith("source_") for k in check)
    assert (check["target"], check["find"], check["label_no"]) == ("streetlight pole", "utility pole",
                                                                   "Not a streetlight pole")
    assert check["ask"] == f"{POLE_ASK} {UNCLEAR}" and check["test_confirm"] == "kept 9 of 10"
    assert "alternatives" not in check  # the agent's material, not the pipeline's
    mixed = tomllib.loads(to_toml([item(), pole(inventory=True)], []))["check"]
    assert [c.get("inventory", False) for c in mixed] == [False, True] and mixed[0]["report_when"] == "no"


def test_valid_inventory_drafts_pass_marked_and_with_the_unclear_sentence():
    items, problems = validate_inventory(pole(ask=POLE_ASK, report_when="no",
                                              alternatives=["Utility Pole", " pole ", "", "lamp post", "pole",
                                                            "street light", "light pole"]))
    assert problems == [] and len(items) == 1
    got = items[0]
    assert got["inventory"] is True and "report_when" not in got  # a stray defect answer would make it a check
    assert got["ask"] == f"{POLE_ASK} {UNCLEAR}"
    assert got["alternatives"] == ["pole", "lamp post", "street light"]  # not find, no repeats, at most three
    assert validate_inventory(pole())[0][0]["ask"].count("answer unclear") == 1


def test_bad_inventory_drafts_are_dropped_with_reasons():
    draft = {"items": [pole(id="Streetlight Pole"), pole(id="obstruction"), pole(id="tree", find=" "),
                       pole(id="can", ask="", label_yes=""), pole(), pole()]}
    items, problems = validate_inventory(draft)
    assert [i["id"] for i in items] == ["streetlight_pole"]
    text = "\n".join(problems)
    for reason in ("lower case", "reserved", "find is empty", "ask is empty", "label_yes is empty", "id repeated"):
        assert reason in text


def test_with_unclear_adds_the_sentence_only_when_missing():
    assert with_unclear(" Is it a pole? ") == f"Is it a pole? {UNCLEAR}"
    assert with_unclear("Is it a pole? If you cannot tell, answer unclear.") == \
        "Is it a pole? If you cannot tell, answer unclear."


def test_the_inventory_draft_prompt_and_schema():
    text = inventory_prompt(" streetlight pole ", "Seattle City Light poles that carry a street lamp")
    assert '"streetlight pole"' in text and "Seattle City Light poles that carry a street lamp" in text
    assert "one-sentence definition of the target that names the look-alikes" in text and UNCLEAR in text
    assert "Streetlight pole" in text and "3 other short phrases" in text
    assert "What the survey means" not in inventory_prompt("street tree")
    assert {"id", "target", "find", "alternatives", "ask", "label_yes", "label_no"} == \
        set(INVENTORY_SCHEMA["required"])
    assert "report_when" not in INVENTORY_SCHEMA["properties"]
