"""Rule documents the item-authoring agent reads (plan 5, design docs/specs/2026-10-28-spark-agent-design.md 4.1).

Each rule is a short PDF excerpt kept in assist/rules/ with its extracted text beside it. The text lets code check
that a section number or a quote Spark cites really is in the rule, so a made-up citation is dropped."""
import re
from dataclasses import dataclass
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / "rules"


@dataclass(frozen=True)
class Rule:
    name: str
    title: str
    source_url: str
    pages: tuple[tuple[int, int], ...]  # inclusive page ranges of the source PDF kept in the excerpt
    pdf: Path
    text: Path

    def read_text(self) -> str:
        return self.text.read_text(encoding="utf-8")


RULES = {
    "ada2010": Rule(
        name="ada2010",
        title="the 2010 ADA Standards for Accessible Design (excerpt: 303 Changes in Level, 405 Ramps, "
              "406 Curb Ramps, 705 Detectable Warnings)",
        source_url="https://www.ada.gov/assets/pdfs/2010-design-standards.pdf",
        pages=((109, 110), (131, 138), (200, 201)),
        pdf=FOLDER / "ada-2010-excerpt.pdf",
        text=FOLDER / "ada-2010-excerpt.txt",
    ),
}


def get(name: str) -> Rule:
    if name not in RULES:
        raise KeyError(f"unknown rule {name!r}; known: {', '.join(sorted(RULES))}")
    return RULES[name]


def _letters(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def has_quote(text: str, quote: str, min_chars: int = 15) -> bool:
    """True if quote is in text, ignoring case, spacing, punctuation and line breaks (PDF text breaks lines)."""
    q = _letters(quote)
    return len(q) >= min_chars and q in _letters(text)


def has_section(text: str, section: str) -> bool:
    """True if a section number such as 705.1.3 is in text as a whole number, not as part of 1705.1.3."""
    return bool(section) and re.search(rf"(?<![\d.]){re.escape(section)}(?!\d)", text) is not None
