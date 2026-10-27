"""The two tools the authoring agent calls: SAM 3.1 finds a phrase in the labelled photos, and Muse Spark answers a
question on the photos where SAM found it, with the same prompt and schema as the pipeline's check stage
(pipeline/survey_checks.py; since plan 4's final review Spark is asked about the item's `target` noun, such as "curb
ramp", not the SAM phrase: "whether the curb cut is visible" let driveway cuts pass as visible). Every result is scored by assist.testset and kept in history, so the report
shows measured scores, never the agent's own claims."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from assist import testset as scoring
from pipeline import collect
from pipeline.meta_api import image_part, text_part
from pipeline.sam_output import confident, parse

PROMPT = """A pedestrian took this photo on a sidewalk.
{ask}
Answer as JSON:
- visible: whether the {target} is visible in the photo.
- answer: "yes", "no", or "unclear" if the photo does not show it well enough to tell.
- box: the {target} as [x1, y1, x2, y2] on a 0-1000 scale of the image, or [] if it is not visible.
- confidence: 0 to 1 for your answer.
- note: one sentence on what you see."""
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["visible", "answer", "box", "confidence", "note"],
    "properties": {
        "visible": {"type": "boolean"},
        "answer": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "box": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
}


class Bench:
    def __init__(self, api, testset, items: list[dict] = (), photo=collect.photo_path, workers: int = 4):
        self.api, self.ts, self.photo, self.workers = api, testset, photo, workers
        self.items = {i["id"]: i for i in items}  # the drafted items, so an item is scored by its own section
        self.areas: dict[str, dict[str, float]] = {}  # phrase -> photo id -> largest mask share of the photo
        self.answers: dict[tuple[str, str, str], tuple[str, str]] = {}  # (find, ask, photo id) -> (answer, note)
        self.history: list[dict] = []
        self.prompts: list[str] = []  # each distinct question text sent, for tests and the report

    def _map(self, fn, items: list) -> list:
        if self.workers <= 1:
            return [fn(i) for i in items]
        with ThreadPoolExecutor(self.workers) as pool:
            return list(pool.map(fn, items))

    def _area(self, phrase: str, photo_id: str) -> float:
        path = Path(self.photo(photo_id))
        segs = confident(parse(self.api.sam_image(phrase, path), video=False))
        if not segs:
            return 0.0
        h, w = cv2.imread(str(path)).shape[:2]
        return max(s.area for s in segs) / (w * h)

    def find(self, phrase: str) -> dict:
        phrase = phrase.strip()
        if phrase not in self.areas:
            ids = [p.id for p in self.ts.photos]
            self.areas[phrase] = dict(zip(ids, self._map(lambda i: self._area(phrase, i), ids)))
        found = {i: share > 0 for i, share in self.areas[phrase].items()}
        result = {"phrase": phrase, **scoring.score_find(self.ts, found)}
        self.history.append({"tool": "score_find", **result})
        return result

    def ask(self, item_id: str, find: str, ask: str, polarity: str = "same") -> dict:
        """Score item_id's question against the labelled question for the item's own rule section. Raises
        ValueError if no label covers that section: the agent may reword an item, not change what it checks."""
        if item_id not in self.items:
            raise ValueError(f"unknown item {item_id!r}")
        section = self.items[item_id]["section"]
        key = self.ts.key_for(section)
        if key is None:
            raise ValueError(f"no labelled question covers section {section}: return {item_id} untested")
        find = find.strip()
        self.find(find)
        hits = [p.id for p in self.ts.visible() if self.areas[find].get(p.id, 0) > 0]
        prompt = PROMPT.format(ask=ask, target=self.items[item_id].get("target") or find)
        if prompt not in self.prompts:
            self.prompts.append(prompt)

        def one(photo_id: str) -> tuple[str, str]:
            cached = self.answers.get((find, ask, photo_id))
            if cached:
                return cached
            out = self.api.spark_json([image_part(Path(self.photo(photo_id))), text_part(prompt)], "survey_check",
                                      SCHEMA)
            got = (out.get("answer", "unclear"), out.get("note", ""))
            self.answers[(find, ask, photo_id)] = got
            return got

        rows = dict(zip(hits, self._map(one, hits)))
        score = scoring.score_answers(self.ts, key, {i: a for i, (a, _) in rows.items()}, polarity)
        for w in score["wrong"]:
            w["note"] = rows[w["photo_id"]][1]
        result = {"item_id": item_id, "find": find, "ask": ask, "key": key, "polarity": polarity, **score}
        self.history.append({"tool": "score_ask", **result})
        return result

    def last(self, tool: str, **match) -> dict | None:
        for entry in reversed(self.history):
            if entry["tool"] == tool and all(entry.get(k) == v for k, v in match.items()):
                return entry
        return None
