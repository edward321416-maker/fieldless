import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


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
    assert "faint dots are the city's records in the photos' view that the survey did not find" in text
    assert "five items" not in text and "five photo checks" in text


def test_second_opinion_is_claimed_for_the_us_items_only():
    text = readme()
    assert "every possible defect on the US survey items" in text
    assert "every possible defect is asked again" not in text


def test_seoul_is_counts_and_source_only():
    text = readme()
    assert "Seoul's tree list (Open Data Plaza OA-23040) is KOGL Type 4, so only counts are shown, with the source." in text
    assert "eval.city_check.shareable" not in text  # an internal name; the README says what a visitor gets
    row = next(l for l in text.splitlines() if l.startswith("| Another country's rules"))
    assert "counts and source only" in row


def test_place_search_asks_only_on_enter():
    text = readme()
    line = next(l for l in text.splitlines() if l.startswith("- **Choosing an area.**"))
    assert "Enter" in line and "Nominatim" in line and "while you type" in line


def test_every_meta_model_says_what_it_does_and_where_it_runs():
    text = readme()
    table = text[text.index("## Meta AI used"):text.index("In the recorded Seattle survey")]
    header = next(l for l in table.splitlines() if l.startswith("| Meta technology"))
    assert "Runs on" in header
    rows = {l.split("|")[1].strip(): l for l in table.splitlines() if l.startswith("| **")}
    for name, where in (("SAM 3.1", "Meta Model API"), ("Muse Spark", "Meta Model API"),
                        ("Muse Spark Open", "Baseten"), ("Muse Glimmer", "Modal")):
        row = next(r for n, r in rows.items() if n.startswith(f"**{name}"))
        cells = row.split("|")
        assert cells[2].strip() and where in cells[3], name


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
    assert "ran locally with a $5 cap" in text and "$4.21 is not a limit breach" in text


def test_the_invite_code_is_not_in_the_repo_text():
    # the README names the variable, never a value
    for line in readme().splitlines():
        if "INVITE_CODE" in line:
            assert not re.search(r"INVITE_CODE\s*=\s*\S", line), line


def test_the_claims_the_audit_corrected():
    text = readme()
    assert "a $400,000 budget" in text and "($400,000, 2017)" not in text
    assert "Fieldless is a narrower first pass than the 2017 survey, which recorded surface condition and slope." in text
    assert "when we looked" not in text  # Claude judged these, not a person
