#!/usr/bin/env python3
"""Install locked ComfyUI models with checksum verification and atomic moves."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfyui-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "infra" / "models.lock.yaml")
    parser.add_argument("--accept-licenses", action="store_true")
    args = parser.parse_args()
    if not args.accept_licenses:
        print("MISSING: pass --accept-licenses after reviewing every license_url in the manifest", file=sys.stderr)
        return 2

    manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    token = os.environ.get("HF_TOKEN", "")
    for model in manifest["comfyui"]["models"]:
        target = args.comfyui_root / model["destination"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and digest(target) == model["sha256"]:
            print(f"OK: {model['id']}")
            continue
        if model.get("requires_hf_token") and not token:
            print(f"MISSING: HF_TOKEN is required for {model['id']}; export it in this shell", file=sys.stderr)
            return 2
        headers = {"Authorization": f"Bearer {token}"} if model.get("requires_hf_token") else {}
        print(f"Downloading {model['id']} from {model['repository']} ({model['size_bytes']} bytes expected)")
        with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", dir=target.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            try:
                with requests.get(model["url"], headers=headers, stream=True, timeout=(30, 300)) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(8 * 1024 * 1024):
                        if chunk:
                            temp.write(chunk)
                temp.flush()
                os.fsync(temp.fileno())
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
        actual = digest(temp_path)
        if actual != model["sha256"]:
            temp_path.unlink(missing_ok=True)
            print(f"MISSING: checksum mismatch for {model['id']}", file=sys.stderr)
            return 2
        os.replace(temp_path, target)
        print(f"OK: {model['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
