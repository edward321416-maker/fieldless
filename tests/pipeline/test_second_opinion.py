import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest

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

    def answer(self, check, photo, box=None):
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
    assert (disagree["object_label"], disagree["review_reasons"]) == ("unclear", ["unclear", "models_disagree"])
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


def test_a_wrong_shape_answer_is_recorded_on_its_row_only(tmp_path):
    photos = {4: synthetic_photo(tmp_path / "4.jpg")}
    for wrong in ({}, {"answer": "maybe"}, None, ["yes"]):
        (out,) = second_opinion.apply(FakeOpen({"4.jpg": wrong}), CHECK, [row("no")], photos, 1)
        assert out["review_reasons"] == ["possible_defect"]
        assert "unexpected answer" in out["model_outputs"]["second_opinion"]["error"]
        assert "agree" not in out["model_outputs"]["second_opinion"]


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


def fake_client(reply: dict, sent: dict, models=None):
    def create(**kwargs):
        sent.update(kwargs)
        message = SimpleNamespace(content=json.dumps(reply))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                           models=SimpleNamespace(list=models or (lambda: [])))


def test_an_open_model_takes_its_server_model_and_system_prompt_and_names_itself_in_the_answer(tmp_path):
    sent = {}
    reply = {"visible": True, "answer": "yes", "box": [], "confidence": 0.8, "note": "a pole"}
    model = second_opinion.OpenModel(tmp_path / "calls_glimmer.jsonl", base_url="https://x.modal.run/v1",
                                     model="meta-models/Muse-Glimmer-30B", api_key="k", max_tokens=1234,
                                     system="Reasoning strength: low", kind="glimmer",
                                     client=fake_client(reply, sent))
    answer = model.answer(CHECK, synthetic_photo(tmp_path / "p.jpg"))
    assert answer == {**reply, "model": "meta-models/Muse-Glimmer-30B"}
    assert sent["model"] == "meta-models/Muse-Glimmer-30B" and sent["max_tokens"] == 1234
    assert sent["messages"][0] == {"role": "system", "content": "Reasoning strength: low"}
    assert sent["messages"][1]["role"] == "user"
    (logged,) = [json.loads(l) for l in (tmp_path / "calls_glimmer.jsonl").read_text(encoding="utf-8").splitlines()]
    assert logged["kind"] == "glimmer" and logged["model"] == "meta-models/Muse-Glimmer-30B" and "ts" in logged


def test_the_default_open_model_is_spark_open_on_baseten_without_a_system_prompt(tmp_path):
    sent = {}
    model = second_opinion.OpenModel(tmp_path / "calls_open.jsonl", client=fake_client(
        {"visible": True, "answer": "no", "box": [], "confidence": 0.9, "note": "plain"}, sent))
    assert model.answer(CHECK, synthetic_photo(tmp_path / "p.jpg"))["model"] == second_opinion.MODEL
    assert [m["role"] for m in sent["messages"]] == ["user"]
    assert model.base_url == second_opinion.BASE_URL and model.kind == "spark_open"


def test_wake_pings_the_server_in_the_background_and_never_raises(tmp_path):
    pinged = []

    def slow_list():
        time.sleep(0.3)
        pinged.append(True)
        raise RuntimeError("cold start 503")

    model = second_opinion.OpenModel(tmp_path / "c.jsonl", client=fake_client({}, {}, models=slow_list), kind="glimmer",
                                     wake_wait_s=0.2, wake_retry_s=0.1)  # gives up after the first try
    started = time.time()
    model.wake()
    assert time.time() - started < 0.2  # the scan does not wait for the container
    deadline = time.time() + 5
    while not pinged and time.time() < deadline:
        time.sleep(0.05)
    assert pinged
    log = tmp_path / "c.jsonl"
    while not (log.exists() and log.read_text(encoding="utf-8").strip()) and time.time() < deadline:
        time.sleep(0.05)  # the wake thread logs right after the ping; under load that takes a moment
    (logged,) = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert logged["kind"] == "glimmer_wake" and "cold start 503" in logged["error"]


def test_after_wake_an_answer_waits_until_the_cold_server_is_up_before_it_is_sent(tmp_path):
    events = []
    tries = []

    def cold_list():
        tries.append(True)
        if len(tries) < 3:
            raise RuntimeError("503 starting")
        events.append("up")

    sent = {}
    client = fake_client({"visible": True, "answer": "yes", "box": [], "confidence": 0.9, "note": "a can"}, sent,
                         models=cold_list)
    create = client.chat.completions.create
    client.chat.completions.create = lambda **kw: (events.append("asked"), create(**kw))[1]
    model = second_opinion.OpenModel(tmp_path / "c.jsonl", client=client, kind="glimmer", wake_wait_s=5,
                                     wake_retry_s=0.05)
    model.wake()
    assert model.answer(CHECK, synthetic_photo(tmp_path / "p.jpg"))["answer"] == "yes"
    assert events == ["up", "asked"] and len(tries) == 3
    deadline = time.time() + 5  # the wake thread logs after it lets the answer go
    while time.time() < deadline and len((tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()) < 2:
        time.sleep(0.05)
    logged = [json.loads(l) for l in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()]
    (wake,) = [r for r in logged if r["kind"] == "glimmer_wake"]
    assert "error" not in wake


def test_an_answer_without_a_wake_is_sent_at_once(tmp_path):
    sent = {}
    model = second_opinion.OpenModel(tmp_path / "c.jsonl", client=fake_client(
        {"visible": False, "answer": "no", "box": [], "confidence": 0.9, "note": ""}, sent))
    started = time.time()
    model.answer(CHECK, synthetic_photo(tmp_path / "p.jpg"))
    assert time.time() - started < 1 and sent


def test_glimmer_from_env_needs_its_url_and_derives_its_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GLIMMER_URL", raising=False)
    assert second_opinion.glimmer_from_env(tmp_path) is None
    monkeypatch.setenv("GLIMMER_URL", "https://me--fieldless-glimmer-serve.modal.run/")
    model = second_opinion.glimmer_from_env(tmp_path)
    assert model.base_url == "https://me--fieldless-glimmer-serve.modal.run/v1"
    assert model.model == "meta-models/Muse-Glimmer-30B" and model.system == "Reasoning strength: medium"
    assert model.max_tokens == 4000
    assert model.kind == "glimmer" and model.log_path == tmp_path / "calls_glimmer.jsonl"
    from pipeline import config
    expected = hmac.new(config.api_key().encode(), b"fieldless-glimmer-key", hashlib.sha256).hexdigest()
    assert model.api_key == expected
    assert "fieldless" not in repr(model) and expected not in repr(model)  # the key is never shown


def test_apply_records_the_open_models_own_name(tmp_path):
    photos = {4: synthetic_photo(tmp_path / "4.jpg")}
    model = FakeOpen({"4.jpg": {"visible": True, "answer": "no", "box": [], "confidence": 0.9, "note": "plain"}})
    model.model = "some-open-model"
    (out,) = second_opinion.apply(model, CHECK, [row("no")], photos, 1)
    assert out["model_outputs"]["second_opinion"]["model"] == "some-open-model"



def test_gpu_spend_today_counts_each_session_from_first_wake_to_last_answer_plus_the_idle_window(tmp_path):
    from datetime import datetime, timezone
    day = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc).timestamp()
    log = tmp_path / "calls_glimmer.jsonl"
    records = [
        {"kind": "glimmer_wake", "ts": day, "seconds": 150},
        {"kind": "glimmer", "ts": day + 160, "seconds": 20},
        {"kind": "glimmer", "ts": day + 300, "seconds": 20},  # within 120 s of the last: same session, ends +320
        {"kind": "glimmer", "ts": day + 3600, "seconds": 30},  # a new session, 30 s long
        {"kind": "glimmer", "ts": day - 86_400, "seconds": 999},  # yesterday
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\nnot json\n", encoding="utf-8")
    rate = second_opinion.GPU_USD_PER_HOUR / 3600
    expected = ((320 + 120) + (30 + 120)) * rate
    assert second_opinion.gpu_usd_today(log, now=day + 4000) == pytest.approx(expected)
    assert second_opinion.gpu_usd_today(tmp_path / "missing.jsonl") == 0.0
    assert second_opinion.gpu_usd_total(log) == pytest.approx(expected + (999 + 120) * rate)  # yesterday too
    assert second_opinion.gpu_usd_total(tmp_path / "missing.jsonl") == 0.0
