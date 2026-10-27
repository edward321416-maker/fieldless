"""Survey item drafts from Muse Spark: the JSON schema Spark answers in, checks on what it wrote, and TOML in the
format pipeline/standards/<code>.toml reads (pipeline.survey_items: required check keys id, find, ask, report_when,
label_yes, label_no; unknown keys are ignored, so each item's source and test result can sit beside it)."""
import json
import re

from assist.rules import has_quote, has_section

CHECK_ID = re.compile(r"[a-z][a-z0-9_]*")
RESERVED = ("missing_blocks", "cover_cut", "fixed_obstruction", "obstruction")  # pipeline.survey_items.RESERVED
ANSWERS = ("yes", "no")
TEXT_KEYS = ("target", "find", "ask", "label_yes", "label_no")

_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "target", "find", "ask", "report_when", "label_yes", "label_no", "section", "page", "quote"],
    "properties": {
        "id": {"type": "string"}, "target": {"type": "string"}, "find": {"type": "string"},
        "ask": {"type": "string"}, "report_when": {"type": "string", "enum": list(ANSWERS)},
        "label_yes": {"type": "string"}, "label_no": {"type": "string"},
        "section": {"type": "string"}, "page": {"type": "integer"}, "quote": {"type": "string"},
    },
}
DRAFT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["checkable", "not_checkable"],
    "properties": {
        "checkable": {"type": "array", "items": _ITEM},
        "not_checkable": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["requirement", "section", "reason"],
            "properties": {"requirement": {"type": "string"}, "section": {"type": "string"},
                           "reason": {"type": "string"}}}},
    },
}

DRAFT_PROMPT = """This PDF is an excerpt of {title}.
A surveyor will check streets only from ordinary photos taken by a pedestrian on the sidewalk. Nobody measures anything.
1) List the requirements in this excerpt that such a photo can settle, at most {max_items}. For each one give:
   - id: a short lower_snake_case name.
   - target: the thing the requirement is about, as a short noun phrase (for example "curb ramp").
   - find: a short noun phrase an image segmentation model (SAM 3.1) can find in the photo. For curb ramps use "curb cut".
   - ask: one yes/no question about one photo of that thing.
   - report_when: the answer, "yes" or "no", that means the requirement is NOT met.
   - label_yes, label_no: short map labels for the two answers.
   - section: the section number; page: the page number printed on the page; quote: a short exact quote of the requirement.
2) List the requirements a photo cannot settle (for example anything that needs a slope, length or width measured), with the reason.
Return JSON only."""


def validate(draft: dict, rule_text: str) -> tuple[list[dict], list[str]]:
    """(items that pass, problems). An item is dropped if a required text is empty, its id is malformed, reserved or
    repeated, its report_when is not yes or no, or its section or quote is not in the rule text (made up)."""
    items, problems, seen = [], [], set()
    for item in draft.get("checkable", []):
        name = str(item.get("id", ""))
        reasons = []
        if not CHECK_ID.fullmatch(name):
            reasons.append("id must be lower case letters, digits and _")
        elif name in RESERVED:
            reasons.append("id is a reserved issue type")
        elif name in seen:
            reasons.append("id repeated")
        if item.get("report_when") not in ANSWERS:
            reasons.append("report_when must be yes or no")
        reasons += [f"{key} is empty" for key in TEXT_KEYS if not str(item.get(key, "")).strip()]
        found = re.match(r"[\d.]+", str(item.get("section", "")).strip())
        section = found.group(0).rstrip(".") if found else ""
        if not has_section(rule_text, section):
            reasons.append(f"section {item.get('section')!r} is not in the rule")
        if not has_quote(rule_text, str(item.get("quote", ""))):
            reasons.append("quote is not in the rule")
        if reasons:
            problems.append(f"{name or '?'}: " + "; ".join(reasons))
            continue
        seen.add(name)
        items.append({**item, "section": section})
    return items, problems


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def to_toml(items: list[dict], header: list[str]) -> str:
    """[[check]] tables for pipeline/standards/<code>.toml, with the source and the measured test result kept as
    extra keys (source_*, test_*), which the pipeline ignores."""
    out = [f"# {line}" for line in header] + [""]
    for item in items:
        out.append("[[check]]")
        for key in ("id", "find", "ask", "report_when", "label_yes", "label_no"):
            out.append(f"{key} = {_toml_str(item[key])}")
        out.append(f"source_section = {_toml_str(item['section'])}")
        out.append(f"source_page = {int(item['page'])}")
        out.append(f"source_quote = {_toml_str(item['quote'])}")
        for key in ("test_find", "test_ask"):
            if item.get(key):
                out.append(f"{key} = {_toml_str(item[key])}")
        out.append("")
    return "\n".join(out)
