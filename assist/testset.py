"""Labelled test photos for the authoring agent (plan 5): which photos show the target, and the true answer to each
labelled question. Scores are computed here, by code, so the agent never grades its own work.

Each labelled question lists the rule sections it covers and the answer that is a defect. An item can be scored only
against the question for its own section, so the agent cannot rewrite an item into a different requirement to fit the
labels (it did on the first real run, 2026-10-28).

An inventory test set (things a survey records the location of, such as streetlight poles) has no questions: each
photo only says whether it shows the target, which is all that confirming a find needs."""
import json
from dataclasses import dataclass
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / "testsets"
VALUES = ("yes", "no", "unclear")


@dataclass(frozen=True)
class Question:
    means: str  # what a "yes" means
    sections: tuple  # rule sections this question checks, matched exactly
    defect_when: str  # the answer that is a defect: yes | no


@dataclass(frozen=True)
class Photo:
    id: str
    target_visible: str  # yes | no | unclear
    answers: dict  # question key -> yes | no | unclear
    note: str = ""


@dataclass(frozen=True)
class TestSet:
    name: str
    target: str
    questions: dict  # key -> Question
    photos: tuple

    def visible(self) -> list[Photo]:
        return [p for p in self.photos if p.target_visible == "yes"]

    def negatives(self) -> list[Photo]:
        return [p for p in self.photos if p.target_visible == "no"]

    def key_for(self, section: str) -> str | None:
        """The labelled question that checks this rule section, or None if no label covers it."""
        return next((k for k, q in self.questions.items() if section in q.sections), None)


def _question(name: str, key: str, q: dict) -> Question:
    if q.get("defect_when") not in ("yes", "no") or not q.get("sections"):
        raise ValueError(f"{name}: question {key}: needs sections and a defect_when of yes or no")
    return Question(str(q["means"]), tuple(str(s) for s in q["sections"]), q["defect_when"])


def load(name: str, folder: Path = FOLDER) -> TestSet:
    data = json.loads((Path(folder) / f"{name}.json").read_text(encoding="utf-8"))
    questions = {k: _question(name, k, q) for k, q in data["questions"].items()}
    photos = []
    for p in data["photos"]:
        answers = dict(p.get("answers", {}))
        if p["target_visible"] not in VALUES or any(v not in VALUES for v in answers.values()):
            raise ValueError(f"{name}: photo {p['id']}: values must be yes, no or unclear")
        unknown = set(answers) - set(questions)
        if unknown:
            raise ValueError(f"{name}: photo {p['id']}: unknown question keys {sorted(unknown)}")
        photos.append(Photo(str(p["id"]), p["target_visible"], answers, p.get("note", "")))
    return TestSet(data["name"], data["target"], questions, tuple(photos))


def score_find(ts: TestSet, found: dict) -> dict:
    """found: photo id -> True if SAM found the target. Visible photos it found or missed, negatives it fired on."""
    visible, negatives = ts.visible(), ts.negatives()
    return {"found_visible": sum(bool(found.get(p.id)) for p in visible), "total_visible": len(visible),
            "false_on_negatives": sum(bool(found.get(p.id)) for p in negatives), "total_negatives": len(negatives),
            "missed": [p.id for p in visible if not found.get(p.id)]}


def score_confirm(ts: TestSet, answers: dict) -> dict:
    """An inventory item's confirmation. answers: photo id -> yes | no | unclear for the photos where SAM found
    something. On a photo that shows the target the right answer is yes (the asset is recorded); on a photo without
    it, SAM's find is false and the right answer is no (the pipeline drops it). Unclear goes to a person either way."""
    visible = [p.id for p in ts.visible() if p.id in answers]
    negatives = [p.id for p in ts.negatives() if p.id in answers]
    on_visible = [answers[i] for i in visible]
    on_negatives = [answers[i] for i in negatives]
    return {"total_visible": len(ts.visible()), "found_visible": len(visible),
            "confirmed": on_visible.count("yes"), "rejected_wrongly": on_visible.count("no"),
            "unclear": on_visible.count("unclear"),
            "total_negatives": len(ts.negatives()), "false_finds": len(negatives),
            "false_finds_rejected": on_negatives.count("no"), "false_finds_unclear": on_negatives.count("unclear"),
            "kept_true": on_visible.count("yes"), "kept_false": on_negatives.count("yes"),
            "wrong_visible": [{"photo_id": i, "answer": answers[i]} for i in visible if answers[i] != "yes"],
            "wrong_negatives": [{"photo_id": i, "answer": answers[i]} for i in negatives if answers[i] != "no"]}


def flip(answer: str) -> str:
    return {"yes": "no", "no": "yes"}.get(answer, answer)


def score_answers(ts: TestSet, key: str, answers: dict, polarity: str = "same") -> dict:
    """answers: photo id -> yes | no | unclear for the photos the model was asked about. polarity "opposite" means a
    yes to the asked question is a no to the labelled one. Only visible photos with a yes/no truth count."""
    if key not in ts.questions:
        raise ValueError(f"unknown question key {key!r}; known: {', '.join(ts.questions)}")
    if polarity not in ("same", "opposite"):
        raise ValueError("polarity must be same or opposite")
    decidable = [p for p in ts.visible() if p.answers.get(key) in ("yes", "no")]
    right, unclear, unchecked, wrong = 0, 0, 0, []
    for p in decidable:
        if p.id not in answers:
            unchecked += 1
            continue
        answer = answers[p.id] if polarity == "same" else flip(answers[p.id])
        if answer == "unclear":
            unclear += 1
        elif answer == p.answers[key]:
            right += 1
        else:
            wrong.append({"photo_id": p.id, "truth": p.answers[key], "answer": answer})
    return {"right": right, "decidable": len(decidable), "unclear": unclear, "unchecked": unchecked, "wrong": wrong}
