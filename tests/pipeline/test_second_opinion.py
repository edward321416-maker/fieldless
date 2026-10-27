import json
from types import SimpleNamespace

from pipeline import second_opinion, survey_items
from tests.pipeline.fakes import synthetic_photo

CHECK = survey_items.load("US").check("curb_ramp_warning")


def row(answer, idx=4):
    return {"issue_type": CHECK.id, "object_label": answer, "frame_idx": idx,
            "review_status": "needs_review" if answer != "yes" else "auto",
            "review_reasons": ["possible_defect"] if answer == "no" else [],
            "model_outputs": {"check": {"answer": answer}}}


class FakeOpen:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def answer(self, check, photo):
        self.asked.append(photo.name)
        result = self.answers[photo.name]
        if isinstance(result, Exception):
            raise result
        return result


def test_apply_asks_only_on_reported_defects_and_marks_a_disagreement(tmp_path):
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in (2, 4, 6)}
    model = FakeOpen({"4.jpg": {"visible": True, "answer": "yes", "box": [1, 2, 3, 4], "confidence": 0.7, "note": "domes"},
                      "6.jpg": {"visible": True, "answer": "no", "box": [], "confidence": 0.9, "note": "plain"}})
    rows = second_opinion.apply(model, CHECK, [row("no", 4), row("yes", 2), row("no", 6)], photos, workers=1)
    assert model.asked == ["4.jpg", "6.jpg"]  # the "yes" row is not a reported defect
    disagree, ok, agree = rows
    assert disagree["review_reasons"] == ["possible_defect", "models_disagree"]
    assert disagree["model_outputs"]["second_opinion"] == {"model": second_opinion.MODEL, "answer": "yes",
                                                            "agree": False, "confidence": 0.7, "note": "domes",
                                                            "box": [1, 2, 3, 4]}
    assert agree["review_reasons"] == ["possible_defect"] and agree["model_outputs"]["second_opinion"]["agree"] is True
    assert "second_opinion" not in ok["model_outputs"]


def test_a_failed_second_opinion_is_recorded_and_changes_nothing_else(tmp_path):
    photos = {4: synthetic_photo(tmp_path / "4.jpg")}
    (out,) = second_opinion.apply(FakeOpen({"4.jpg": RuntimeError("baseten 503")}), CHECK, [row("no")], photos, 1)
    assert out["review_reasons"] == ["possible_defect"]
    assert out["model_outputs"]["second_opinion"]["model"] == second_opinion.MODEL
    assert "baseten 503" in out["model_outputs"]["second_opinion"]["error"]


def test_open_model_asks_with_the_photo_and_the_schema_and_logs_usage(tmp_path):
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        message = SimpleNamespace(content=json.dumps({"visible": True, "answer": "no", "box": [], "confidence": 0.9,
                                                      "note": "plain"}))
        usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 3290, "completion_tokens": 700})
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    model = second_opinion.OpenModel(tmp_path / "calls_open.jsonl", client=client)
    answer = model.answer(CHECK, synthetic_photo(tmp_path / "p.jpg"))
    assert answer["answer"] == "no"
    assert sent["model"] == second_opinion.MODEL
    assert sent["response_format"]["json_schema"]["name"] == "survey_check"
    image, text = sent["messages"][0]["content"]
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,") and CHECK.ask in text["text"]
    (logged,) = [json.loads(l) for l in (tmp_path / "calls_open.jsonl").read_text(encoding="utf-8").splitlines()]
    assert logged["kind"] == "spark_open" and logged["usage"]["prompt_tokens"] == 3290 and "key" not in json.dumps(logged)


def test_from_env_needs_the_baseten_key(tmp_path, monkeypatch):
    assert second_opinion.from_env(tmp_path) is None
    monkeypatch.setenv("BASETEN_API_KEY", "test-not-a-real-key")
    assert isinstance(second_opinion.from_env(tmp_path), second_opinion.OpenModel)
