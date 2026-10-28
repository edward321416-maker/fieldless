import ast
import hashlib
import hmac
from pathlib import Path

from pipeline import second_opinion

SOURCE = Path(__file__).resolve().parents[2] / "deploy" / "modal_glimmer.py"


def constant(name):
    for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {SOURCE.name}")


def test_the_deployed_model_is_the_one_the_pipeline_asks_for():
    assert constant("MODEL") == second_opinion.GLIMMER_MODEL


def test_the_server_key_is_derived_like_the_clients(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "meta-secret")
    expected = hmac.new(b"meta-secret", constant("KEY_SALT"), hashlib.sha256).hexdigest()
    assert second_opinion.glimmer_key() == expected


def test_the_gpu_scales_to_zero_with_one_container_at_most():
    text = SOURCE.read_text(encoding="utf-8")
    assert 'gpu="H100"' in text and "min_containers=0" in text and "max_containers=1" in text
    assert "scaledown_window=120" in text and 'VLLM = "vllm==0.31.0"' in text
