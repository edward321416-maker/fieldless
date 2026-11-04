import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = "https://edward321416--fieldless-web.us-east.modal.run"
# The credit a New York record carries (NYC Open Data's terms ask for the source, version and changes): the words the app
# and the reports show, read from the city's own file so the README cannot drift from them.
NEW_YORK_CREDIT = tomllib.loads((ROOT / "eval" / "cities" / "nyc.toml").read_text(encoding="utf-8"))["credit"]


def readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


def meta_table(text: str) -> str:
    """The "Meta AI used" table, header and rows, up to the first line that is not part of it."""
    lines = text[text.index("## Meta AI used"):].splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("| Meta technology"))
    last = next((i for i in range(first, len(lines)) if not lines[i].startswith("|")), len(lines))
    return "\n".join(lines[first:last])


def test_setup_matches_the_tested_python_and_names_the_keys():
    text = readme()
    assert "Needs Python 3.14" in text and "Python 3.12+" not in text
    assert "-c constraints.txt" in text
    assert ".env.example" in text


def test_env_example_has_no_values():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    pairs = [l.split("=", 1) for l in lines if l and not l.startswith("#")]
    assert {"MODEL_API_KEY", "MAPILLARY_TOKEN", "BASETEN_API_KEY"} <= {k for k, _ in pairs}
    assert all(v == "" for _, v in pairs)


def test_env_example_can_be_committed():
    # .gitignore ignores .env*, so .env.example needs its own exception
    assert "!.env.example" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_demo_without_keys_is_the_first_thing_under_run_it_yourself():
    text = readme()
    start = text.index("## Run it yourself")
    assert "No keys?" in text[start:start + 400]
    assert "huggingface.co/spaces/BBONG1201/fieldless" in text[start:start + 400]


def test_readme_matches_what_the_screens_call_things():
    text = readme()
    assert not re.search(r"\bpatch", text, re.IGNORECASE)  # the file is "Differences (.geojson)", not a patch
    assert "Differences (.geojson)" in text and "scan_<id>_city_diff.geojson" in text
    assert "P1 · Possible defect" in text
    assert "marked for review" not in text and "rows a person rejected" not in text
    assert "a faint dot for each city record in the photos' view that the survey did not find" in text
    assert "five items" not in text and "five photo checks" in text


def test_second_opinion_is_claimed_for_the_us_items_only():
    text = readme()
    assert "every possible defect on the US survey items" in text
    assert "every possible defect is asked again" not in text


def test_seongsu_is_out_of_the_readme_and_the_korean_standard_stays_as_code():
    text = readme()
    assert "Seongsu" not in text and "OA-23040" not in text
    assert "eval.city_check.shareable" not in text  # an internal name; the README says what a visitor gets
    assert "`kr.toml` holds a Korean standard tried during the build; it is not part of the demo." in text
    assert "kr.toml" in text


def test_place_search_asks_only_on_enter():
    text = readme()
    line = next(l for l in text.splitlines() if l.startswith("- **Choosing an area.**"))
    assert "Enter" in line and "Nominatim" in line and "while you type" in line


def test_every_meta_model_says_what_it_does_and_where_it_runs():
    table = meta_table(readme())
    header = next(l for l in table.splitlines() if l.startswith("| Meta technology"))
    assert "Runs on" in header
    rows = {l.split("|")[1].strip(): l for l in table.splitlines() if l.startswith("| **")}
    for name, where in (("**SAM 3.1**", "Meta Model API"), ("**Muse Spark**", "Meta Model API"),
                        ("**Muse Spark Open** (open weights)", "Baseten"),
                        ("**Muse Glimmer** (open weights)", "Modal")):
        cells = rows[name].split("|")  # exact row name: "Muse Spark" must not stand in for "Muse Spark Open"
        assert cells[2].strip() and where in cells[3], name


def test_each_meta_model_says_which_key_it_needs():
    text = readme()
    setup = text[text.index("### Setup"):text.index("### Survey items from a rule")]
    assert "`MODEL_API_KEY` (the Meta Model API: SAM 3.1 and Muse Spark)" in setup and "`MAPILLARY_TOKEN`" in setup
    assert "`BASETEN_API_KEY` (Muse Spark Open) is optional" in setup
    rows = {l.split("|")[1].strip(): l.split("|")[3] for l in meta_table(text).splitlines() if l.startswith("| **")}
    assert "`MODEL_API_KEY`" in rows["**SAM 3.1**"] and "`MODEL_API_KEY`" in rows["**Muse Spark**"]
    assert "`BASETEN_API_KEY`" in rows["**Muse Spark Open** (open weights)"]
    glimmer = rows["**Muse Glimmer** (open weights)"]
    assert "modal deploy deploy/modal_glimmer.py" in glimmer and "`GLIMMER_URL`" in glimmer
    assert "`--glimmer`" in glimmer and "`MODEL_API_KEY`" in glimmer  # its own key is derived from MODEL_API_KEY


def test_commands_are_ones_that_run():
    text = readme()
    # eval.city_check needs --run and --scan; run bare it exits with a usage error
    for line in (l for l in text.splitlines() if "eval.city_check" in l and "python -m" in l):
        assert "--run" in line and "--scan" in line, line
    # modal is not in requirements.txt and the container serves web/dist, which a fresh clone does not have
    live = text[text.index("### Live on Modal"):text.index("### On a Hugging Face Docker Space")]
    assert "pip install modal" in live
    assert live.index("npm --prefix web run build") < live.index("modal deploy")


def test_the_cost_caps_are_stated_without_a_false_breach():
    text = readme()
    assert "Meta API spend stays inside the $3 per-survey" in text
    # the two example surveys that cost more than $3 ran locally under higher caps
    assert "ran locally under higher caps" in text
    assert "Ballard's $3.33 and Capitol Hill's $4.21 are not limit breaches" in text
    assert "A survey started on the live site stops at $3" in text


def test_readme_names_the_invite_code_variables_but_gives_no_value():
    for line in readme().splitlines():
        if "INVITE_CODE" in line:
            assert not re.search(r"INVITE_CODE\s*=\s*\S", line), line


def test_the_claims_the_audit_corrected():
    text = readme()
    assert "a $400,000 budget" in text and "($400,000, 2017)" not in text
    assert "Fieldless is a narrower first pass than the 2017 survey, which recorded surface condition and slope." in text
    assert "when we looked" not in text  # Claude judged these, not a person


def test_the_opening_leads_with_survey_productivity_and_ballard_is_the_headline():
    text = readme()
    opening = text[:text.index("## Why")]
    first = opening.split("\n\n")[1]  # the paragraph after "# Fieldless"
    assert "automates a first pass" in first and "human review" in first
    assert "Ballard, Seattle" in first and "0.92 km" in first and "10.3 minutes" in first and "$3.33" in first
    assert "Meta API subtotal of $5.63" in first and "$6.10 per km" in first
    assert "Baseten, hosting and human review are excluded" in first
    assert "actual fieldwork savings have not been measured" in first
    # docs/design-rules.md R1: no one survey item stands in the first paragraph
    for item in (r"curb ramps?", r"poles?", r"trees?", r"litter", r"pay stations?", r"steps?", r"hydrants?", r"racks?"):
        assert not re.search(rf"\b{item}\b", first, re.IGNORECASE), item
    assert "**Record Update List**" in opening  # the second paragraph says what a city gets
    # Capitol Hill is an older example. Its $4.48 per km and $4.21 belong to the results, never to the headline.
    before_results = text[:text.index("## Results (measured)")]
    assert "$4.48" not in before_results and "$4.21" not in before_results


def test_try_it_links_the_three_examples_in_order_and_the_read_only_demo():
    line = next(l for l in readme().splitlines() if l.startswith("**Try it:**"))
    places = [line.index(f"]({LIVE}/#scan={scan})") for scan in (30, 31, 17)]  # Ballard, New York, Capitol Hill
    assert places == sorted(places)
    assert "https://huggingface.co/spaces/BBONG1201/fieldless" in line
    assert f"]({LIVE}/#scan=17&item=4)" in line


def test_the_images_are_the_update_list_and_the_agent_and_both_are_there():
    text = readme()
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    assert "docs/images/update-list-revised.png" in images and "docs/images/agent-item.png" in images
    for path in images:
        assert (ROOT / path).stat().st_size > 10_000, path
    assert "records-lens" not in text  # the old screen, with Capitol Hill's $4.48
    alt = re.search(r"!\[([^\]]*)\]\(docs/images/update-list-revised\.png\)", text).group(1)
    assert "29 of 94" in alt and "Mapillary credit" in alt


def test_the_update_list_uses_the_words_on_its_screens():
    text = readme()
    screens = "".join((ROOT / "web" / "src" / name).read_text(encoding="utf-8")
                      for name in ("AssetCheckPane.tsx", "ActionHub.tsx", "reviewLabels.ts"))
    for label in ("Update list", "Correct the record", "Review newer photo evidence", "No change", "Visit on site",
                  "Compare with the list", "Open the report", "Table (.csv)", "Table (.geojson)", "Show on the map"):
        assert label in text and label in screens, label
    # the files and the page the server names the same way
    server = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
    assert "update-list-<id>.csv" in text and 'filename="update-list-{scan_id}.csv"' in server
    assert "update-list-<id>.geojson" in text and 'filename="update-list-{scan_id}.geojson"' in server
    page = (ROOT / "assist" / "asset_check.py").read_text(encoding="utf-8")
    assert '"<area> Record Update List"' in text and 'f"{place} Record Update List"' in page


def test_dispatch_and_gis_diff_are_what_a_survey_without_an_update_list_shows():
    line = next(l for l in readme().splitlines() if l.startswith("- **Dispatch and GIS Diff.**"))
    assert "The update list takes their place on a survey that has one" in line
    assert "A survey without one still shows them" in line


def test_the_update_list_is_a_suggestion_and_never_more():
    text = readme()
    assert "Every row is a suggestion" in text and "does not replace site checks" in text
    assert "changes nothing in the city's records" in text
    assert not re.search(r"\b(city|asset team) (accepted|approved|signed off)", text, re.IGNORECASE)
    # the city's date field is the city's own: nobody knows it to be the date of a site check
    assert "field-check date" not in text and "site-check date" not in text
    assert "`LAST_VERIFY_DATE` for trees, `VERIFICATION_DATE` for curb ramps" in text
    # panorama rows are 2025 photos, so only the survey photos are all 2026
    assert "all 2026 photos" not in text
    assert 'so "2026" is true of its survey photos only' in text


def test_results_take_ballard_then_new_york_then_capitol_hill():
    text = readme()
    results = section(text, "## Results (measured)", "## Data, privacy and attribution")
    names = ("Seattle, Ballard", "New York, Park Avenue South", "Seattle, Capitol Hill")
    starts = [results.index(f"- **{name}**") for name in names]
    assert starts == sorted(starts)
    ballard, new_york, capitol = results[starts[0]:starts[1]], results[starts[1]:starts[2]], results[starts[2]:]
    for fact in ("2026-05-18 to 2026-07-01", "199 photos over 0.92 km", "five photo checks", "10.3 min", "676 model calls",
                 "$3.33 ($3.60 per km)", "29 are desk-review candidates (1 correction, 25 date reviews, 3 with no change proposed)",
                 "65 need more evidence", "26 more objects are not on the city's lists, 7 of them not settled",
                 "53 of the 94 were seen in photos (27 by the survey, 9 on a second look, 17 on a second look in panoramas",
                 "41 are not determined", "27 of 94 (poles 10/22, street trees 11/45, litter cans 1/2, curb ramps 5/25, "
                 "pay stations 0/0)", "0 of 17 (survey photos) and 1 of 10 (panoramas", "agreed with the city on 7, differed "
                 "on 1 and left 14 not determined", "of 57 recorded findings, 43 right, 3 wrong and 11 could not be told"):
        assert fact in ballard, fact
    for fact in ("2026-02-02 to 2026-02-09", "181 photos over 0.70 km", "7.9 min", "641 model calls", "$2.97 ($4.24 per km)",
                 "9 of 79 (street trees 4/29, litter cans 0/4, pay stations 3/7, curb ramps 2/39; no usable pole list)",
                 "of 63 recorded findings, 52 right (83%), 1 wrong and 10 could not be told"):
        assert fact in new_york, fact
    for fact in ("from 2022", "an older example", "181 photos over 0.94 km", "7.0 min", "$4.21 ($4.48 per km)",
                 "44 are desk-review candidates and 63 need more evidence", "45 of 107", "73 of 86 recorded findings right"):
        assert fact in capitol, fact
    differs = next(l for l in results.splitlines() if l.startswith("- **What differs.**"))
    assert "newer survey photos" in differs and "lower cost per km" in differs
    assert "$3.60 and $4.24 against $4.48 for the survey, $6.10 and $5.77 against $6.93 for the whole list" in differs
    assert "match with the city's lists is lower" in differs and "share of findings judged right" in differs
    assert " better" not in differs  # only the photo date and the cost per km differ for the better, and it is not said


def test_the_assistant_that_judged_the_findings_is_named_and_is_not_a_person():
    text = readme()
    assert "by an AI assistant (Claude, the coding assistant used to build Fieldless), not by a person" in text
    assert text.count("Checked by the AI assistant") == 3  # one line for each example


def test_new_york_credit_is_in_data_privacy_and_attribution():
    text = readme()
    assert NEW_YORK_CREDIT in section(text, "## Data, privacy and attribution", "## Limits")


def test_limits_give_photo_dates_by_example_and_the_causes_nobody_has_tested():
    text = readme()
    limits = section(text, "## Limits", "## Built during the Meta Global AI Developer Hackathon")
    for fact in ("2026-05-18 to 2026-07-01", "panoramas from 2025", "2026-02-02 to 2026-02-09", "photos are from 2022",
                 "It has no pay stations, only 2 litter-can records, and street trees are 48% of its city records (45 of 94)",
                 "come from one Mapillary contributor", "**Suggestions only.**", "does not replace site checks"):
        assert fact in limits, fact
    assert "**New York matches few of the city's records** (9 of 79), and we do not know why." in limits
    # why New York's match is low has not been tested: no cause is named anywhere in the README
    assert not re.search(r"\b(snow|leafless|winter|field of view)\b", text, re.IGNORECASE)


def test_the_cost_per_km_is_the_survey_and_the_second_looks_behind_the_list_cost_more():
    text = readme()
    results = section(text, "## Results (measured)", "## Data, privacy and attribution")
    limits = section(text, "## Limits", "## Built during the Meta Global AI Developer Hackathon")
    starts = [results.index(f"- **{name}**") for name in ("Seattle, Ballard", "New York, Park Avenue South",
                                                          "Seattle, Capitol Hill", "What differs.")]
    ballard, new_york, capitol = results[starts[0]:starts[1]], results[starts[1]:starts[2]], results[starts[2]:starts[3]]
    # The exact figures from the cost files. A survey's cost per km is the survey itself; the second looks behind its
    # update list cost more, and each part is rounded to the cent. Said in Results, and again in Limits.
    cost = {
        "Ballard": ("$3.33 ($3.60 per km", "$2.30 more", "asking again about records the survey did not see $1.10",
                    "a look at 2025 panoramas $0.77", "a record value check $0.17", "blurring $0.26",
                    "$5.63, or $6.10 per km"),
        "New York": ("$2.97 ($4.24 per km)", "$1.07 more (a second look $1.05, blurring $0.02; no panoramas exist in that "
                     "area)", "$4.04, or $5.77 per km"),
        "Capitol Hill": ("$4.21 ($4.48 per km)", "$2.30 more (a second look $0.90, a test sample $0.27, panoramas $0.80, a "
                         "value check $0.20, blurring $0.12; each part rounded to the cent)", "$6.51, or $6.93 per km"),
    }
    for name, part in (("Ballard", ballard), ("New York", new_york), ("Capitol Hill", capitol)):
        for fact in cost[name]:
            assert fact in part, ("Results", name, fact)
        for fact in cost[name]:
            assert fact in limits, ("Limits", name, fact)
    assert results.count("for the survey itself") == 3  # each example's cost per km is labelled as the survey's
    assert "is the survey only, as the live app shows" in limits
    # these are Meta API charges: the open-weight calls ran on Baseten credits
    assert "These are Meta API charges: the 87 Muse Spark Open answers in the second looks ran on Baseten credits and are not priced here" in limits
    # the figures are exact, not "about"
    for rough in ("about $2 more", "about $5.5", "about $6", "a few tens of cents"):
        assert rough not in text, rough
    # the opening says it before anyone reads $3.60 as the price of "43 of 94"
    opening = text[:text.index("## Why")]
    assert "initial 0.92 km survey" in opening and "Meta API subtotal of $5.63" in opening
    assert "end-to-end list time and actual fieldwork savings have not been measured" in opening
    assert "no site visit" not in opening
    # the comparison with the 2017 survey stays, and says whose figure Fieldless's is
    assert "a $400,000 budget" in text
    assert "Fieldless's per-km figure is the survey itself, not the whole update list" in results
    assert "Fieldless's per-km figure is the survey itself, not the whole update list." in limits
