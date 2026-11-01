import json
from dataclasses import dataclass
from types import SimpleNamespace

from eval import asset_check, record_check
from pipeline.cost import CostCapReached

YES = {"answer": "yes", "visible": True, "box": [], "confidence": 0.9, "note": ""}
NO = {"answer": "no", "visible": True, "box": [], "confidence": 0.9, "note": ""}
UNCLEAR = {"answer": "unclear", "visible": True, "box": [], "confidence": 0.5, "note": ""}
COMPARE = {"field": "LIT", "yes": "Y", "no": "N", "ask": "Is it lit?", "says_yes": "Lit", "says_no": "Not lit"}
TOWN = {"city": "Testville", "items": {"pole": {"record_field": "ID", "compare": COMPARE}, "can": {"record_field": "ID"}}}
LAYERS = {"pole": {"P1": {"ID": "P1", "LIT": "N"}, "P2": {"ID": "P2", "LIT": "Y"}, "P3": {"ID": "P3", "LIT": None}},
          "can": {"C1": {"ID": "C1"}}}


@dataclass(frozen=True)
class Check:
    target: str = "pole"
    ask: str = ""


STANDARD = SimpleNamespace(check=lambda item: Check())


def row(record, action=asset_check.SAME, item="pole", box=(1, 2, 3, 4), image="p.jpg"):
    return {"item": item, "city_record": record, "action": action, "box": list(box) if box else None,
            "evidence": {"image": image} if image else None}


class Spark:
    def __init__(self, answers):
        self.answers = answers

    def spark_json(self, parts, name, schema):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Open:
    def __init__(self, answer):
        self.answer_given, self.calls = answer, 0

    def answer(self, check, photo, box=None):
        self.calls += 1
        return self.answer_given


def run_dir(tmp_path):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "p.jpg").write_bytes(b"jpeg")
    return tmp_path


def jobs(*records):
    return [{"item": "pole", "record": r, "image": "p.jpg", "box": [1, 2, 3, 4], "city_value": v} for r, v in records]


def test_a_record_differs_only_when_both_models_answer_against_it():
    job = {"city_value": "no"}
    assert record_check.differs(job, YES, YES) is True
    assert record_check.differs(job, YES, NO) is False
    assert record_check.differs(job, YES, None) is False
    assert record_check.differs(job, NO, None) is False
    assert record_check.differs(job, UNCLEAR, YES) is False
    assert record_check.differs(job, {**YES, "visible": False}, YES) is False


def test_only_settled_rows_with_a_field_a_photo_can_answer_are_asked():
    check = {"rows": [row("P1"), row("P2", asset_check.DATE), row("P9", asset_check.VISIT), row("P3"),
                      row("P1", box=None), row("P1", image=None), row("C1", item="can"), row("", asset_check.COMPARE)]}
    picked = record_check.jobs_for(check, TOWN, LAYERS)
    assert [(j["record"], j["city_value"]) for j in picked] == [("P1", "no"), ("P2", "yes")]
    assert picked[0]["box"] == [1, 2, 3, 4] and picked[0]["image"] == "p.jpg"


def test_the_second_model_is_asked_only_when_the_first_disagrees_with_the_city(tmp_path):
    other = Open(NO)
    out = record_check.run(jobs(("P1", "no"), ("P2", "yes")), Spark([NO, NO]), other, STANDARD, TOWN, run_dir(tmp_path),
                           workers=1)
    assert other.calls == 1
    assert [r["differs"] for r in out] == [False, True]
    assert record_check.summary(out) == {"asked": 2, "agree": 1, "differ": 1, "unsettled": 0}


def test_a_record_that_differs_says_both_sides_in_the_citys_words(tmp_path):
    out = record_check.run(jobs(("P1", "no")), Spark([YES]), Open(YES), STANDARD, TOWN, run_dir(tmp_path))
    assert (out[0]["city_says"], out[0]["photo_shows"]) == ("Not lit", "Lit")


def test_models_that_do_not_agree_leave_a_record_unsettled(tmp_path):
    out = record_check.run(jobs(("P1", "no")), Spark([YES]), Open(NO), STANDARD, TOWN, run_dir(tmp_path))
    assert out[0]["differs"] is False and "city_says" not in out[0]
    assert record_check.summary(out) == {"asked": 1, "agree": 0, "differ": 0, "unsettled": 1}


def test_the_cost_cap_and_an_error_leave_a_record_unsettled_and_keep_the_rest(tmp_path):
    answers = [CostCapReached("cap"), ValueError("bad"), NO]
    out = record_check.run(jobs(("P1", "no"), ("P2", "no"), ("P3", "no")), Spark(answers), Open(YES), STANDARD, TOWN,
                           run_dir(tmp_path), workers=1)
    assert [r["spark"]["answer"] for r in out] == ["not_asked", "error", "no"]
    assert record_check.summary(out) == {"asked": 1, "agree": 1, "differ": 0, "unsettled": 2}


def test_a_failure_of_the_second_model_leaves_the_record_unsettled(tmp_path):
    class Broken(Open):
        def answer(self, check, photo, box=None):
            raise RuntimeError("down")

    out = record_check.run(jobs(("P1", "no")), Spark([YES]), Broken(None), STANDARD, TOWN, run_dir(tmp_path))
    assert out[0]["differs"] is False and out[0]["open"]["answer"] == "error"


def test_the_asset_check_reads_what_differs_without_what_the_assistant_sent_back(tmp_path):
    rows = [{"item": "pole", "record": "P1", "differs": True, "city_says": "Not lit", "photo_shows": "Lit"},
            {"item": "pole", "record": "P2", "differs": True, "city_says": "Lit", "photo_shows": "Not lit"},
            {"item": "pole", "record": "P3", "differs": False}]
    (tmp_path / "record_check_scan1.json").write_text(json.dumps(
        {"summary": {"asked": 3, "agree": 1, "differ": 2, "unsettled": 0}, "rows": rows}), encoding="utf-8")
    (tmp_path / "look_review_scan1.json").write_text(json.dumps(
        {"not_different": [{"item": "pole", "record": "P2", "why": "The box is on a tree."}]}), encoding="utf-8")
    differs, trust = asset_check.read_differs(tmp_path, 1)
    assert differs == {("pole", "P1"): {"city_says": "Not lit", "photo_shows": "Lit"}}
    assert trust == {"asked": 3, "agree": 1, "differ": 1, "unsettled": 0, "sent_back": 1}


def test_no_record_check_is_nothing_to_read(tmp_path):
    assert asset_check.read_differs(tmp_path, 1) == ({}, None)
