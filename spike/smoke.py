"""Smoke test: one structured Spark call, one SAM image call on a public photo, and a check of the
official meta_sam_parser on the same output. Costs well under $0.01. Needs only MODEL_API_KEY."""
import dataclasses
import json

import meta_sam_parser as msp

from spike import config
from spike import meta_client as mc
from spike.sam_parse import parse_sam_output

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}
# Freely licensed sidewalk photo with tactile paving (Wikimedia Commons). The API fetches it by URL.
IMAGE_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/Blindengeleidestrook.JPG"


def describe(obj, depth: int = 0):
    """Summarize parser objects so the real field names can be read from the output."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        if depth >= 3:
            return type(obj).__name__
        return {"type": type(obj).__name__,
                **{f.name: describe(getattr(obj, f.name), depth + 1) for f in dataclasses.fields(obj)}}
    if isinstance(obj, (list, tuple)):
        return [describe(x, depth + 1) for x in obj[:3]]
    if isinstance(obj, (str, bytes)) and len(obj) > 60:
        return f"{type(obj).__name__}[{len(obj)}]"
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    return repr(obj)[:80]


def main() -> None:
    print("spark:", mc.spark_json([mc.text_part("Reply with the word ok.")], "smoke", SCHEMA))

    text = mc.sam("tactile paving", {"type": "input_image", "image_url": IMAGE_URL}, billed_frames=1)
    (config.OUT / "smoke_sam.txt").write_text(text, encoding="utf-8")
    print("sam raw (first 300 chars):", text[:300])
    print("our parser:", parse_sam_output(text))

    parser = msp.image_segmentation_format().create_parser()
    parser.push(text)
    result = parser.finish(msp.CompletedOutcome()).result
    facts = [describe(r) for r in result.records[:4]]
    (config.OUT / "sam_parser_api.json").write_text(json.dumps(facts, indent=2, default=str), encoding="utf-8")
    print("official parser records:", json.dumps(facts, indent=2, default=str)[:1500])
    for record in result.records:
        mask = getattr(record, "mask", None)
        if mask is not None:
            raster = msp.decode_mask_to_raster(mask)
            print(f"decoded mask {mask.width}x{mask.height}: {len(raster)} cells, {sum(raster)} on")
            break
    else:
        print("no record exposes .mask; read spike/out/sam_parser_api.json for the real field name")


if __name__ == "__main__":
    main()
