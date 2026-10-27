import tomllib

from assist.items import DRAFT_PROMPT, DRAFT_SCHEMA, to_toml, validate

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
