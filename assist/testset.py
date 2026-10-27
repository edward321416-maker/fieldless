"""Labelled test photos for the authoring agent (plan 5): which photos show the target, and the true answer to each
labelled question. Scores are computed here, by code, so the agent never grades its own work."""
import json
from dataclasses import dataclass
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / "testsets"
VALUES = ("yes", "no", "unclear")


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
    questions: dict  # key -> what a "yes" means
    photos: tuple

    def visible(self) -> list[Photo]:
        return [p for p in self.photos if p.target_visible == "yes"]

    def negatives(self) -> list[Photo]:
        return [p for p in self.photos if p.target_visible == "no"]


def load(name: str, folder: Path = FOLDER) -> TestSet:
    data = json.loads((Path(folder) / f"{name}.json").read_text(encoding="utf-8"))
    photos = []
    for p in data["photos"]:
        answers = dict(p.get("answers", {}))
        if p["target_visible"] not in VALUES or any(v not in VALUES for v in answers.values()):
            raise ValueError(f"{name}: photo {p['id']}: values must be yes, no or unclear")
        unknown = set(answers) - set(data["questions"])
        if unknown:
            raise ValueError(f"{name}: photo {p['id']}: unknown question keys {sorted(unknown)}")
        photos.append(Photo(str(p["id"]), p["target_visible"], answers, p.get("note", "")))
    return TestSet(data["name"], data["target"], dict(data["questions"]), tuple(photos))


def score_find(ts: TestSet, found: dict) -> dict:
    """found: photo id -> True if SAM found the target. Visible photos it found or missed, negatives it fired on."""
    visible, negatives = ts.visible(), ts.negatives()
    return {"found_visible": sum(bool(found.get(p.id)) for p in visible), "total_visible": len(visible),
            "false_on_negatives": sum(bool(found.get(p.id)) for p in negatives), "total_negatives": len(negatives),
            "missed": [p.id for p in visible if not found.get(p.id)]}


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
