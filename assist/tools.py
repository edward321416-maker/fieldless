"""The tools the authoring agents call: SAM 3.1 finds a phrase in the labelled photos, and Muse Spark answers a
question on the photos where SAM found it, with the same prompt and schema as the pipeline's check stage
(pipeline/survey_checks.py; since plan 4's final review Spark is asked about the item's `target` noun, such as "curb
ramp", not the SAM phrase: "whether the curb cut is visible" let driveway cuts pass as visible). Every result is scored by assist.testset and kept in history, so the report
shows measured scores, never the agent's own claims.

A check item's question is scored against its labelled question (`ask`). An inventory item's question only confirms
that what SAM found is the target (`confirm`), so it is also asked on photos without the target where SAM fired:
there a "no" is right, because the pipeline drops the find. Answers are counted the way the pipeline counts them
(survey_checks.status), so the score says what a scan would record: an answer that does not see the target drops the
pick, which for a check item on a photo that shows it is a miss ("dropped", scored wrong)."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import cv2

from assist import testset as scoring
from pipeline import collect
from pipeline.meta_api import image_part, text_part
from pipeline.sam_output import confident, parse
from pipeline.survey_checks import PROMPT, SCHEMA  # the scan's own, so a test asks exactly what a scan asks


def counted_check(out: dict) -> str:
    """A check item's answer as the pipeline counts it (survey_checks.status): the target not visible drops the pick
    ("dropped"); otherwise yes, no or unclear."""
    if not out.get("visible", True):
        return "dropped"
    answer = out.get("answer")
    return answer if answer in ("yes", "no") else "unclear"


def counted_inventory(out: dict) -> str:
    """An inventory confirmation as the pipeline counts it (pipeline.survey_checks.status): the target not visible, or
    a "no", drops the pick (a look-alike such as a plain utility pole); "unclear" goes to review; "yes" is an asset."""
    if not out.get("visible", True) or out.get("answer") == "no":
        return "no"
    return "yes" if out.get("answer") == "yes" else "unclear"


class Bench:
    def __init__(self, api, testset, items: list[dict] = (), photo=collect.photo_path, workers: int = 4, judge=None):
        self.api, self.ts, self.photo, self.workers = api, testset, photo, workers
        # judge.answer(check, photo): the model that will run an inventory item in scans (Muse Glimmer on Modal when
        # the item's judge is glimmer), so the agent tests the wording on that model. None: Muse Spark.
        self.judge = judge
        self.items = {i["id"]: i for i in items}  # the drafted items, so an item is scored by its own section
        self.areas: dict[str, dict[str, float]] = {}  # phrase -> photo id -> largest mask share of the photo
        self.answers: dict[tuple[str, str, str], tuple[str, str]] = {}  # (find, ask, photo id) -> (answer, note)
        self.confirmations: dict[tuple[str, str], tuple[str, str]] = {}  # (prompt, photo id) -> (answer, note)
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
            got = (counted_check(out), out.get("note", ""))
            self.answers[(find, ask, photo_id)] = got
            return got

        rows = dict(zip(hits, self._map(one, hits)))
        score = scoring.score_answers(self.ts, key, {i: a for i, (a, _) in rows.items()}, polarity)
        for w in score["wrong"]:
            w["note"] = rows[w["photo_id"]][1]
        result = {"item_id": item_id, "find": find, "ask": ask, "key": key, "polarity": polarity, **score}
        self.history.append({"tool": "score_ask", **result})
        return result

    def _confirmation(self, prompt: str, photo_id: str, check) -> tuple[str, str]:
        """(counted answer, note) for one photo. Cached by prompt, not by phrase: the photo and the question decide
        Spark's answer, so trying another phrase pays only for photos no earlier phrase found."""
        cached = self.confirmations.get((prompt, photo_id))
        if cached:
            return cached
        path = Path(self.photo(photo_id))
        if self.judge is not None:
            out = self.judge.answer(check, path)  # the same PROMPT, built from check.ask and check.target
        else:
            out = self.api.spark_json([image_part(path), text_part(prompt)], "survey_check", SCHEMA)
        got = (counted_inventory(out), out.get("note", ""))
        self.confirmations[(prompt, photo_id)] = got
        return got

    def confirm(self, item_id: str, find: str, ask: str) -> dict:
        """Score an inventory item: Spark answers `ask` about the item's target on every photo where SAM found `find`,
        with and without the target (assist.testset.score_confirm). Wrong answers carry Spark's note."""
        if item_id not in self.items:
            raise ValueError(f"unknown item {item_id!r}")
        find, ask = find.strip(), ask.strip()
        self.find(find)
        target = self.items[item_id].get("target") or find
        prompt = PROMPT.format(ask=ask, target=target)
        check = SimpleNamespace(id=item_id, ask=ask, target=target)  # what survey_checks.prompt() reads
        if prompt not in self.prompts:
            self.prompts.append(prompt)
        hits = [p.id for p in (*self.ts.visible(), *self.ts.negatives()) if self.areas[find].get(p.id, 0) > 0]
        rows = dict(zip(hits, self._map(lambda i: self._confirmation(prompt, i, check), hits)))
        score = scoring.score_confirm(self.ts, {i: a for i, (a, _) in rows.items()})
        for w in score["wrong_visible"] + score["wrong_negatives"]:
            w["note"] = rows[w["photo_id"]][1]
        result = {"item_id": item_id, "find": find, "ask": ask, **score}
        self.history.append({"tool": "score_confirm", **result})
        return result

    def last(self, tool: str, **match) -> dict | None:
        for entry in reversed(self.history):
            if entry["tool"] == tool and all(entry.get(k) == v for k, v in match.items()):
                return entry
        return None
