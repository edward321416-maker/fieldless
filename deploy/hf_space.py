"""Publish Fieldless to a free Hugging Face Space (Docker), with its data kept in a private dataset (server/store.py).

  python -m deploy.hf_space --space USER/fieldless --data USER/fieldless-data [--salt] [--seed RUN_DIR]

It uses the token saved by `hf auth login` and never prints it. It uploads the files git tracks at HEAD (git archive),
with a Space README that carries the Space settings. --salt sets a random IP_HASH_SALT secret (once; it is never shown).
--seed uploads a local run folder's threshold.db and evidence/ to the dataset, for example the demo surveys. The
owner sets MODEL_API_KEY, MAPILLARY_TOKEN, INVITE_CODE and HF_TOKEN (write access to the dataset) in the Space
settings.
"""
import argparse
import io
import secrets
import subprocess
import tarfile
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent

SPACE_HEADER = """---
title: Fieldless
emoji: 🗺️
colorFrom: green
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
license: mit
short_description: Street surveys without the fieldwork
---

"""


def export_head(dst: Path) -> None:
    """The files git tracks at HEAD, so nothing untracked (keys, photos, databases) can be uploaded."""
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "HEAD"], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dst, filter="data")
    readme = dst / "README.md"
    readme.write_text(SPACE_HEADER + readme.read_text(encoding="utf-8"), encoding="utf-8")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--space", required=True, help="USER/NAME of the Space")
    parser.add_argument("--data", required=True, help="USER/NAME of the private dataset that keeps the data")
    parser.add_argument("--salt", action="store_true", help="set a new random IP_HASH_SALT secret")
    parser.add_argument("--seed", type=Path, help="local run folder to upload to the dataset (threshold.db, evidence/)")
    args = parser.parse_args(argv)

    api = HfApi()
    api.create_repo(args.data, repo_type="dataset", private=True, exist_ok=True)
    api.create_repo(args.space, repo_type="space", space_sdk="docker", private=False, exist_ok=True)

    if args.seed:
        from server.store import DatasetStore
        DatasetStore(args.data, args.seed, api=api, download=lambda *a, **k: None).save_now()
        print(f"seeded {args.data} from {args.seed}")

    api.add_space_variable(args.space, "HF_DATA_REPO", args.data)
    if args.salt:
        api.add_space_secret(args.space, "IP_HASH_SALT", secrets.token_hex(32))
        print("set a random IP_HASH_SALT")

    with tempfile.TemporaryDirectory() as tmp:
        export_head(Path(tmp))
        api.upload_folder(repo_id=args.space, repo_type="space", folder_path=tmp,
                          commit_message=f"Deploy {head_commit()}")
    print(f"deployed to https://huggingface.co/spaces/{args.space}")


def head_commit() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


if __name__ == "__main__":
    main()
