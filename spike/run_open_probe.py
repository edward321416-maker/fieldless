"""Plan 4 Task 3b probe: can Muse Spark Open (Baseten, open weights) answer a survey check on a photo, as JSON?

Two probe photos (one ramp with domes, one without). Tries response_format json_schema first, then plain JSON in the
prompt. Prints answers and token usage, never the key (BASETEN_API_KEY, loaded from .env by pipeline.config).

  python -m spike.run_open_probe
"""
import json
import os
import time

from openai import OpenAI

from pipeline import collect, config  # noqa: F401  (config loads .env)
from pipeline.meta_api import image_part
from spike.run_ramp_probe import PROMPT, SCHEMA  # the same prompt and schema as the Task 0 gate and survey_checks

BASE_URL = "https://inference.baseten.co/v1"
MODEL = "meta-models/muse-spark-open-1026"
PHOTOS = {"3490854744471068": "yes", "1859712874536378": "no"}
ASK = ("Look at the curb ramp where the sidewalk meets the street. Does it have a detectable warning surface "
       "(a panel of raised truncated domes, usually yellow) at its bottom edge?")


def main() -> None:
    client = OpenAI(base_url=BASE_URL, api_key=os.environ["BASETEN_API_KEY"])
    prompt = PROMPT.format(ask=ASK, find="curb cut")
    for image_id, truth in PHOTOS.items():
        url = image_part(collect.photo_path(image_id))["image_url"]
        messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                 {"type": "text", "text": prompt}]}]
        for mode in ("json_schema", "plain"):
            kwargs = ({"response_format": {"type": "json_schema",
                                           "json_schema": {"name": "survey_check", "schema": SCHEMA, "strict": True}}}
                      if mode == "json_schema" else {})
            started = time.time()
            try:
                resp = client.chat.completions.create(model=MODEL, messages=messages, max_tokens=2000, **kwargs)
            except Exception as exc:
                print(image_id, mode, "ERROR", type(exc).__name__, str(exc)[:300])
                continue
            msg = resp.choices[0].message
            usage = resp.usage.model_dump() if resp.usage else {}
            print(image_id, "truth", truth, mode, f"{time.time() - started:.1f}s", "usage", json.dumps(usage)[:300])
            print("  content:", (msg.content or "")[:400].replace("\n", " "))
            break


if __name__ == "__main__":
    main()
