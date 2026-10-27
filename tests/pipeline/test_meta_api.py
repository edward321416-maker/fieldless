import json
from types import SimpleNamespace

import pytest

from pipeline import config
from pipeline import meta_api as ma
from pipeline.cost import CostCapReached, CostMeter

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["a"],
          "properties": {"a": {"type": "integer"}}}
SPARK_COST = 1000 * 1.25e-6 + 1000 * 4.25e-6  # the fake reports 1000 input and 1000 output tokens


class FakeStream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, *exc):
        return False


def delta(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def completed():
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=None))


def failed():
    return SimpleNamespace(type="response.failed", response=SimpleNamespace(
        status="failed", error=SimpleNamespace(message="boom"), incomplete_details=None))


class FakeClient:
    """spark: answers for responses.create in order (a str, or an exception to raise);
    streams: event lists for responses.stream in order."""

    def __init__(self, spark=(), streams=()):
        self.spark = list(spark)
        self.streams = list(streams)
        self.created, self.streamed, self.deleted = [], [], []
        self.responses = SimpleNamespace(create=self._create, stream=self._stream)
        self.files = SimpleNamespace(create=self._upload, delete=self.deleted.append)

    def _create(self, **kwargs):
        self.created.append(kwargs)
        item = self.spark.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(output_text=item, usage={"input_tokens": 1000, "output_tokens": 1000})

    def _stream(self, **kwargs):
        self.streamed.append(kwargs)
        return FakeStream(self.streams.pop(0))

    def _upload(self, purpose, file):
        return SimpleNamespace(id="file-1")


def make_api(tmp_path, client, cap=1.0):
    return ma.MetaApi(tmp_path / "calls.jsonl", CostMeter(cap), client=client, sleep=lambda s: None)


def read_log(tmp_path):
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


def photo(tmp_path):
    path = tmp_path / "p.jpg"
    path.write_bytes(b"\xff\xd8\xff")
    return path


def test_parts_have_the_api_shapes(tmp_path):
    assert ma.text_part("hi") == {"type": "input_text", "text": "hi"}
    assert ma.file_part("f") == {"type": "input_file", "file_id": "f"}
    assert ma.video_part("f") == {"type": "input_video", "file_id": "f"}
    assert ma.image_part(photo(tmp_path))["image_url"].startswith("data:image/jpeg;base64,")


def test_spark_json_returns_the_answer_and_logs_its_cost(tmp_path):
    client = FakeClient(spark=['{"a": 1}'])
    api = make_api(tmp_path, client)
    assert api.spark_json([ma.text_part("q")], "probe", SCHEMA) == {"a": 1}
    call = client.created[0]
    assert call["model"] == "muse-spark-1.3"
    assert call["text"]["format"] == {"type": "json_schema", "name": "probe", "schema": SCHEMA, "strict": True}
    (entry,) = read_log(tmp_path)
    assert entry["kind"] == "spark" and entry["schema"] == "probe"
    assert entry["cost_usd"] == pytest.approx(SPARK_COST)
    assert api.meter.spent_usd == pytest.approx(SPARK_COST)
    assert api.meter.reserved_usd == 0


def test_a_spark_answer_that_is_not_json_is_retried_once_and_both_are_charged(tmp_path):
    api = make_api(tmp_path, FakeClient(spark=["not json", "still not"]))
    with pytest.raises(ma.SparkFormatError):
        api.spark_json([ma.text_part("q")], "probe", SCHEMA)
    assert [e["kind"] for e in read_log(tmp_path)] == ["spark_failed", "spark_failed"]
    assert api.meter.spent_usd == pytest.approx(2 * SPARK_COST)


def test_a_request_past_the_cap_is_refused_before_calling(tmp_path):
    client = FakeClient(spark=['{"a": 1}'])
    api = make_api(tmp_path, client, cap=0.01)
    with pytest.raises(CostCapReached):
        api.spark_json([ma.text_part("q")], "probe", SCHEMA, estimate_usd=0.02)
    assert client.created == []


def test_sam_image_joins_deltas_and_is_billed_per_image(tmp_path):
    client = FakeClient(streams=[[delta("<0f>"), delta("x"), completed()]])
    api = make_api(tmp_path, client)
    assert api.sam_image("stairs", photo(tmp_path)) == "<0f>x"
    sent = client.streamed[0]
    assert sent["model"] == "sam-3.1"
    assert sent["metadata"] == {"include_confidence": "true"}
    assert sent["input"][0]["content"][0] == {"type": "input_text", "text": "stairs"}
    assert sent["input"][0]["content"][1]["type"] == "input_image"
    (entry,) = read_log(tmp_path)
    assert entry["kind"] == "sam" and entry["images"] == 1
    assert entry["cost_usd"] == pytest.approx(0.0025)


def test_sam_video_is_billed_for_at_least_50_frames(tmp_path):
    client = FakeClient(streams=[[completed()]])
    api = make_api(tmp_path, client)
    assert api.sam_video("stairs", "file-9", frames=5) == ""
    assert client.streamed[0]["input"][0]["content"][1] == {"type": "input_video", "file_id": "file-9"}
    assert read_log(tmp_path)[0]["cost_usd"] == pytest.approx(0.01)


def test_a_failed_sam_stream_is_retried_once_and_both_attempts_are_charged(tmp_path):
    api = make_api(tmp_path, FakeClient(streams=[[failed()], [failed()]]))
    with pytest.raises(ma.SamStreamError):
        api.sam_image("stairs", photo(tmp_path))
    assert [e["kind"] for e in read_log(tmp_path)] == ["sam_failed", "sam_failed"]
    assert api.meter.spent_usd == pytest.approx(0.005)


def test_a_stream_without_completed_is_an_error(tmp_path):
    api = make_api(tmp_path, FakeClient(streams=[[delta("x")], [delta("x")]]))
    with pytest.raises(ma.SamStreamError, match="without response.completed"):
        api.sam_image("stairs", photo(tmp_path))


def test_five_failed_requests_in_a_row_abort_the_run(tmp_path):
    api = make_api(tmp_path, FakeClient(spark=[ValueError("bad request")] * 5))
    for _ in range(4):
        with pytest.raises(ValueError):
            api.spark_json([ma.text_part("q")], "probe", SCHEMA)
    with pytest.raises(ma.RunAborted):
        api.spark_json([ma.text_part("q")], "probe", SCHEMA)


def test_a_success_resets_the_failure_count(tmp_path):
    answers = [ValueError("x")] * 4 + ['{"a": 1}'] + [ValueError("x")] * 4
    api = make_api(tmp_path, FakeClient(spark=answers))
    for _ in range(4):
        with pytest.raises(ValueError):
            api.spark_json([ma.text_part("q")], "probe", SCHEMA)
    api.spark_json([ma.text_part("q")], "probe", SCHEMA)
    for _ in range(4):
        with pytest.raises(ValueError):  # not RunAborted
            api.spark_json([ma.text_part("q")], "probe", SCHEMA)


def test_an_uploaded_file_is_deleted_even_when_the_body_fails(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    client = FakeClient()
    api = make_api(tmp_path, client)
    with pytest.raises(RuntimeError):
        with api.uploaded(video) as file_id:
            assert file_id == "file-1"
            raise RuntimeError("stage failed")
    assert client.deleted == ["file-1"]


def test_contributor_models_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SPARK_MODEL", "muse-spark-1.3-contributor")
    with pytest.raises(ValueError, match="contributor"):
        make_api(tmp_path, FakeClient())
