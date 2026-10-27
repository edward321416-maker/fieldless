"""Build the read-only public demo: the web app plus the API answers for the demo surveys, as static files.

  python -m deploy.static_site --run RUN --scan 3:Seongsu [--scan 7:Myeongdong] --out DIR [--space USER/fieldless]

Every answer comes from the real server app (server.app, in-process), so the demo shows exactly what the live server
shows: GET /api/x is written to DIR/api/x.json, and each evidence photo is copied as it is. The web app is built with
VITE_STATIC_DEMO=1, which reads those files and hides everything that would change data. --space uploads DIR to a
free static Hugging Face Space with the token saved by `hf auth login`.
"""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from pipeline import config
from server.app import create_app
from server.settings import Settings

ROOT = Path(__file__).resolve().parent.parent

SPACE_README = """---
title: Fieldless
emoji: 🗺️
colorFrom: green
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: mit
short_description: Street surveys without the fieldwork
---

Public demo of [Fieldless](https://github.com/edward321416-maker/threshold): street surveys Fieldless has already run,
read-only. Street photos from Mapillary (CC BY-SA 4.0), credited on every photo.
"""


class NoWorker:
    """The export never runs scans."""

    def start(self):
        pass

    def stop(self):
        pass

    def submit(self, scan_id):
        raise RuntimeError("the static export never starts a scan")


def build_web(out: Path) -> None:
    env = {**os.environ, "VITE_STATIC_DEMO": "1"}
    subprocess.run(f'npx vite build --outDir "{out}" --emptyOutDir', cwd=ROOT / "web", env=env, shell=True,
                   check=True)


def export_api(run: str, scans: list[tuple[int, str]], out: Path) -> list[str]:
    """Write the GET answers for the demo surveys under out/api and copy their evidence photos. Returns the paths."""
    settings = Settings(run_name=run, demo_scans=tuple(scans), static_dir=out / "no-web")
    client = TestClient(create_app(settings, worker=NoWorker(), preview=lambda *a: None))
    paths = ["/api/config", "/api/demo"]
    for scan_id, _ in scans:
        paths += [f"/api/scans/{scan_id}", f"/api/scans/{scan_id}/entrances", f"/api/scans/{scan_id}/issues"]
    evidence = config.run_dir(run) / "evidence"
    written = []
    for path in paths:
        response = client.get(path)
        response.raise_for_status()
        body = response.json()
        target = out / f"{path.lstrip('/')}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        written.append(path)
        for row in body if isinstance(body, list) else []:
            if row.get("evidence"):
                name = row["evidence"]["url"].rsplit("/", 1)[1]
                (out / "api" / "evidence").mkdir(parents=True, exist_ok=True)
                shutil.copy2(evidence / name, out / "api" / "evidence" / name)
    return written


def publish(out: Path, space: str) -> None:
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(space, repo_type="space", space_sdk="static", private=False, exist_ok=True)
    (out / "README.md").write_text(SPACE_README, encoding="utf-8")
    # Old hashed assets and answers go; files uploaded in the same commit stay.
    api.upload_folder(repo_id=space, repo_type="space", folder_path=out, delete_patterns=["assets/*", "api/**"],
                      commit_message="Publish the demo surveys")


def parse_scan(text: str) -> tuple[int, str]:
    scan_id, _, name = text.partition(":")
    return int(scan_id), name.strip() or f"Survey {scan_id}"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="run folder under <data>/runs that holds the demo surveys")
    parser.add_argument("--scan", required=True, action="append", type=parse_scan, help="ID:Name, repeatable")
    parser.add_argument("--out", required=True, type=Path, help="folder to write the site to (emptied first)")
    parser.add_argument("--space", help="USER/NAME of a static Hugging Face Space to upload to")
    args = parser.parse_args(argv)

    out = args.out.resolve()
    build_web(out)
    for path in export_api(args.run, args.scan, out):
        print("wrote", path)
    if args.space:
        publish(out, args.space)
        print(f"published to https://huggingface.co/spaces/{args.space}")


if __name__ == "__main__":
    main()
