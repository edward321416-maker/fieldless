"""Smoke test: one structured Spark call, one SAM image call on a public photo, and a check of the
official meta_sam_parser on the same output. Costs well under $0.01. Needs only MODEL_API_KEY."""
import argparse
import dataclasses
import json
from pathlib import Path

import httpx
import meta_sam_parser as msp

from spike import config
from spike import meta_client as mc
from spike.console import utf8_stdout
from spike.sam_parse import parse_sam_output

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}
# Freely licensed sidewalk photo with tactile paving (Wikimedia Commons). Downloaded locally and sent as
# data, because the API's remote URL fetch is refused by Commons robots.txt.
COMMONS_API = (
    "https://commons.wikimedia.org/w/api.php?action=query&titles=File:Blindengeleidestrook.JPG"
    "&prop=imageinfo&iiprop=url&iiurlwidth=1280&format=json"
)
USER_AGENT = "threshold-spike/0.1 (hackathon research)"
LOCAL_IMAGE = config.OUT / "smoke_tactile.jpg"


def fetch_image() -> Path:
    if LOCAL_IMAGE.exists():
        return LOCAL_IMAGE
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as http:
        data = http.get(COMMONS_API).raise_for_status().json()
        info = next(iter(data["query"]["pages"].values()))["imageinfo"][0]
        LOCAL_IMAGE.write_bytes(http.get(info.get("thumburl") or info["url"]).raise_for_status().content)
    return LOCAL_IMAGE


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
    utf8_stdout()
    parser_args = argparse.ArgumentParser(description="Day-1 Meta API smoke test.")
    parser_args.add_argument("--image", type=Path, default=None,
                             help="local photo to send to SAM; skips the Wikimedia download")
    parser_args.add_argument("--concept", default="tactile paving", help="SAM concept prompt")
    args = parser_args.parse_args()

    print("spark:", mc.spark_json([mc.text_part("Reply with the word ok.")], "smoke", SCHEMA))

    image = args.image if args.image is not None else fetch_image()
    text = mc.sam(args.concept, mc.image_part(image), billed_frames=1)
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
