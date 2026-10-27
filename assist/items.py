"""Survey item drafts from Muse Spark: the JSON schema Spark answers in, checks on what it wrote, and TOML in the
format pipeline/standards/<code>.toml reads (pipeline.survey_items: required check keys id, find, ask, report_when,
label_yes, label_no; unknown keys are ignored, so each item's source and test result can sit beside it).

Two kinds of item. A check item comes from a rule: it has a defect answer (report_when) and cites the rule, and code
drops it if the citation is not in the rule. An inventory item (inventory = true) records where a thing is, such as a
streetlight pole: a "yes" is an asset point, so it has no report_when, and it comes from a target name, not a rule, so
there is nothing to cite."""
import json
import re

from assist.rules import has_quote, has_section

CHECK_ID = re.compile(r"[a-z][a-z0-9_]*")
RESERVED = ("missing_blocks", "cover_cut", "fixed_obstruction", "obstruction")  # pipeline.survey_items.RESERVED
ANSWERS = ("yes", "no")
TEXT_KEYS = ("target", "find", "ask", "label_yes", "label_no")
UNCLEAR = "If it is too far away or too small to see, answer unclear."
ALTERNATIVES = 3  # other SAM phrases an inventory draft offers the agent

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
   - ask: one yes/no question about one photo of that thing. Start the ask with a one-sentence definition of the target
     that names the look-alikes it is not, and say that if the photo shows only those, set visible to false. End it
     with: "If it is too far away or too small to see, answer unclear."
   - report_when: the answer, "yes" or "no", that means the requirement is NOT met.
   - label_yes, label_no: short map labels for the two answers.
   - section: the section number; page: the page number printed on the page; quote: a short exact quote of the requirement.
2) List the requirements a photo cannot settle (for example anything that needs a slope, length or width measured), with the reason.
Return JSON only."""

INVENTORY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "target", "find", "alternatives", "ask", "label_yes", "label_no"],
    "properties": {
        "id": {"type": "string"}, "target": {"type": "string"}, "find": {"type": "string"},
        "alternatives": {"type": "array", "items": {"type": "string"}},
        "ask": {"type": "string"}, "label_yes": {"type": "string"}, "label_no": {"type": "string"},
    },
}

INVENTORY_PROMPT = """A surveyor records where every {target} is along city streets, only from ordinary photos taken by a pedestrian on the sidewalk.{describe}
An image segmentation model (SAM 3.1) finds candidates in each photo, and you will then be asked about each photo where it found one. Write one survey item for this:
- id: a short lower_snake_case name.
- target: the thing recorded, as a short noun phrase ("{target}" unless a more exact name is needed).
- find: a short noun phrase SAM 3.1 can find in the photo. An everyday name for what the thing looks like often finds more than its official name.
- alternatives: {alternatives} other short phrases for find, different from find and from each other.
- ask: a yes/no question about one photo. Start it with a one-sentence definition of the target that names the look-alikes it is not, then ask whether the photo shows one; if it shows only look-alikes, the answer is no. End it with: "{unclear}"
- label_yes, label_no: short map labels for the two answers, for example "{label}" and "Not a {target}".
Return JSON only."""


def inventory_prompt(target: str, describe: str = "") -> str:
    """The drafting prompt for one inventory item; describe, if given, says what the survey means by the target."""
    target = target.strip()
    return INVENTORY_PROMPT.format(target=target, describe=f"\nWhat the survey means by it: {describe.strip()}"
                                   if describe.strip() else "", alternatives=ALTERNATIVES, unclear=UNCLEAR,
                                   label=target[:1].upper() + target[1:])


def with_unclear(ask: str) -> str:
    """ask, ending with the unclear sentence if it does not already tell Spark when to answer unclear: without it
    Spark answered far, tiny objects anyway (the Seattle scan, 2026-10-28)."""
    ask = ask.strip()
    return ask if "answer unclear" in ask.lower() else f"{ask} {UNCLEAR}"


def _id_reasons(name: str, seen: set) -> list[str]:
    if not CHECK_ID.fullmatch(name):
        return ["id must be lower case letters, digits and _"]
    if name in RESERVED:
        return ["id is a reserved issue type"]
    if name in seen:
        return ["id repeated"]
    return []


def validate(draft: dict, rule_text: str) -> tuple[list[dict], list[str]]:
    """(items that pass, problems). An item is dropped if a required text is empty, its id is malformed, reserved or
    repeated, its report_when is not yes or no, or its section or quote is not in the rule text (made up)."""
    items, problems, seen = [], [], set()
    for item in draft.get("checkable", []):
        name = str(item.get("id", ""))
        reasons = _id_reasons(name, seen)
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


def _alternatives(phrases, find: str) -> list[str]:
    """Up to ALTERNATIVES distinct non-empty phrases other than find."""
    out: list[str] = []
    for p in phrases if isinstance(phrases, list) else []:
        p = str(p).strip()
        if p and p.lower() != find.lower() and p.lower() not in (o.lower() for o in out):
            out.append(p)
    return out[:ALTERNATIVES]


def validate_inventory(draft: dict) -> tuple[list[dict], list[str]]:
    """(items that pass, problems) for inventory drafts: one item as Spark wrote it (INVENTORY_SCHEMA), or
    {"items": [...]}. They cite no rule, so only the id rules and the required texts are checked. A passing item is
    marked inventory, keeps only the keys an inventory item has (a stray report_when would make it a check), ends its
    ask with the unclear sentence and keeps at most ALTERNATIVES other phrases."""
    items, problems, seen = [], [], set()
    for item in draft.get("items", [draft]):
        name = str(item.get("id", ""))
        reasons = _id_reasons(name, seen)
        reasons += [f"{key} is empty" for key in TEXT_KEYS if not str(item.get(key, "")).strip()]
        if reasons:
            problems.append(f"{name or '?'}: " + "; ".join(reasons))
            continue
        seen.add(name)
        clean = {key: str(item[key]).strip() for key in ("id", *TEXT_KEYS)}
        items.append({**clean, "inventory": True, "ask": with_unclear(clean["ask"]),
                      "alternatives": _alternatives(item.get("alternatives"), clean["find"])})
    return items, problems


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def to_toml(items: list[dict], header: list[str]) -> str:
    """[[check]] tables for pipeline/standards/<code>.toml, with the source and the measured test result kept as
    extra keys (source_*, test_*), which the pipeline ignores. An inventory item gets `inventory = true` and no
    report_when (the pipeline refuses one) or source keys."""
    out = [f"# {line}" for line in header] + [""]
    for item in items:
        inventory = bool(item.get("inventory"))
        out += ["[[check]]", f"id = {_toml_str(item['id'])}"] + (["inventory = true"] if inventory else [])
        keys = ("target", "find", "ask") + (() if inventory else ("report_when",)) + ("label_yes", "label_no")
        out += [f"{key} = {_toml_str(item[key])}" for key in keys]
        if not inventory:
            out.append(f"source_section = {_toml_str(item['section'])}")
            out.append(f"source_page = {int(item['page'])}")
            out.append(f"source_quote = {_toml_str(item['quote'])}")
        for key in ("test_find", "test_ask", "test_confirm"):
            if item.get(key):
                out.append(f"{key} = {_toml_str(item[key])}")
        out.append("")
    return "\n".join(out)
