"""A second opinion (plan 4 Task 3b): Muse Spark Open, the open-weight Muse Spark served on Baseten, answers the
same check on the same photo whenever Muse Spark reported a defect. Agreement is recorded; a disagreement puts the
row first in line for a person ("models_disagree"). Probe (spike/run_open_probe.py, 2026-10-28): JSON-schema answers
on two Seattle ramps, both right, 11-15 s each.

Off unless BASETEN_API_KEY is set (pipeline.config loads it from .env; it is never logged). Baseten is billed to its
own credit, so these calls are not counted against the Meta cost caps; their token use is logged in calls_open.jsonl.
"""
import json
import os
import time
from pathlib import Path

from pipeline.meta_api import error_text, image_part
from pipeline.parallel import run_parallel
from pipeline.survey_checks import SCHEMA, prompt
from pipeline.survey_items import Check

BASE_URL = "https://inference.baseten.co/v1"
MODEL = "meta-models/muse-spark-open-1026"
MAX_TOKENS = 4000  # Spark Open reasons before it answers (probe: 700-2000 completion tokens)


class OpenModel:
    def __init__(self, log_path: Path, client=None):
        self.log_path = Path(log_path)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=BASE_URL, api_key=os.environ["BASETEN_API_KEY"], timeout=120,
                                  max_retries=2)
        return self._client

    def answer(self, check: Check, photo: Path) -> dict:
        started = time.time()
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_part(photo)["image_url"]}},
                {"type": "text", "text": prompt(check)}]}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "survey_check", "schema": SCHEMA, "strict": True}})
        self._log({"kind": "spark_open", "model": MODEL, "check": check.id, "photo": Path(photo).name,
                   "seconds": round(time.time() - started, 1),
                   "usage": resp.usage.model_dump() if resp.usage else {}})
        return json.loads(resp.choices[0].message.content)

    def _log(self, record: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def from_env(run_dir: Path) -> OpenModel | None:
    """The second opinion when BASETEN_API_KEY is set, else None (the scan runs without it)."""
    return OpenModel(Path(run_dir) / "calls_open.jsonl") if os.environ.get("BASETEN_API_KEY") else None


def apply(model, check: Check, rows: list[dict], photos: dict[int, Path], workers: int) -> list[dict]:
    """Ask the open model on every row whose answer is the check's report_when; record its answer and whether the two
    models agree. Anything but the same answer is a disagreement, so "unclear" (or a photo where the open model sees
    nothing) also puts the row first for a person. A failed request, or a reply of the wrong shape, is recorded on
    that row and changes nothing else."""
    todo = [r for r in rows if r["object_label"] == check.report_when]

    def one(row):
        try:
            answer = model.answer(check, photos[row["frame_idx"]])
            if not isinstance(answer, dict) or answer.get("answer") not in ("yes", "no", "unclear"):
                raise ValueError(f"unexpected answer from {MODEL}: {str(answer)[:200]}")
            return answer
        except Exception as exc:  # this row keeps Spark's answer alone
            return {"error": error_text(exc)}

    for row, answer in zip(todo, run_parallel(one, todo, workers)):
        if "error" in answer:
            row["model_outputs"]["second_opinion"] = {"model": MODEL, "error": answer["error"]}
            continue
        current = answer["answer"] if answer.get("visible", True) else "unclear"
        agree = current == row["object_label"]
        row["model_outputs"]["second_opinion"] = {"model": MODEL, "answer": current, "agree": agree,
                                                  "confidence": answer.get("confidence"),
                                                  "note": answer.get("note"), "box": answer.get("box")}
        if not agree:
            row["review_reasons"] = [*row["review_reasons"], "models_disagree"]
    return rows
