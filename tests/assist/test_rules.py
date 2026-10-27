from assist.rules import get, has_quote, has_section

TEXT = ("705.1.3 Contrast. Detectable warning surfaces shall contrast visually with adjacent walking surfaces either\n"
        "light-on-dark, or dark-on-light.\n1705.2 Other.")


def test_quote_matches_across_line_breaks_case_and_punctuation():
    assert has_quote(TEXT, "detectable warning surfaces shall contrast visually with adjacent walking surfaces "
                           "either light-on-dark")
    assert not has_quote(TEXT, "detectable warning surfaces shall be yellow")
    assert not has_quote(TEXT, "Contrast")  # too short to prove anything


def test_section_must_appear_as_a_whole_number():
    assert has_section(TEXT, "705.1.3") and has_section(TEXT, "705.1")
    assert not has_section(TEXT, "705.2")  # only 1705.2 is in the text
    assert not has_section(TEXT, "")


def test_the_ada_excerpt_is_in_the_repo_with_its_text():
    rule = get("ada2010")
    text = rule.read_text()
    assert rule.pdf.is_file() and has_section(text, "705.1") and has_section(text, "406.5")
