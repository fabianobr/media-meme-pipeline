#!/usr/bin/env python3
"""Validate the local pipeline environment without printing secret values."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MISSING = 0


def report(state: str, name: str, fix: str = "") -> None:
    global MISSING
    if state == "MISSING":
        MISSING += 1
    suffix = f" — {fix}" if fix else ""
    print(f"{state:8} {name}{suffix}")


def get_json(url: str, timeout: int = 5) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def service(name: str, url: str, fix: str) -> object | None:
    try:
        return get_json(url)
    except (OSError, ValueError, urllib.error.URLError):
        report("MISSING", name, fix)
        return None


def git_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dry-run", "render", "full"), default="dry-run")
    parser.add_argument("--homelab-root", type=Path, default=os.environ.get("HOMELAB_ROOT"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--comfyui-url", default=os.environ.get("COMFYUI_URL", "http://localhost:8188"))
    parser.add_argument("--n8n-url", default=os.environ.get("N8N_URL", "http://localhost:5678"))
    parser.add_argument("--models-optional", action="store_true", help="Report absent models as OPTIONAL for code-only installs.")
    args = parser.parse_args()
    manifest = yaml.safe_load((ROOT / "infra" / "models.lock.yaml").read_text(encoding="utf-8"))

    report("OK", f"Python {sys.version_info.major}.{sys.version_info.minor}")
    for module, package in (("requests", "requirements.lock"), ("PIL", "requirements.lock"), ("yaml", "requirements.lock")):
        report("OK" if importlib.util.find_spec(module) else "MISSING", f"Python module {module}", f"install {package}")
    try:
        request = urllib.request.Request("https://www.reddit.com/r/popular/.rss", headers={"User-Agent": "media-meme-pipeline-check/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            reddit_ok = response.status == 200
    except OSError:
        reddit_ok = False
    report("OK" if reddit_ok else "MISSING", "Reddit RSS", "check DNS/network access to reddit.com")

    ollama = service("Ollama API", f"{args.ollama_url.rstrip('/')}/api/tags", "start the media-pipeline profile")
    if ollama is not None:
        report("OK", "Ollama API")
        installed = {item.get("name") for item in ollama.get("models", [])} if isinstance(ollama, dict) else set()
        for model in manifest["ollama"]:
            name = model["name"]
            state = "OK" if name in installed else ("OPTIONAL" if args.models_optional else "MISSING")
            report(state, f"Ollama model {name}", "rerun bootstrap with --install-models")

    if args.mode in {"render", "full"}:
        if shutil.which("nvidia-smi"):
            gpu = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True)
            report("OK" if gpu.returncode == 0 else "MISSING", "NVIDIA GPU", "install the NVIDIA driver")
        else:
            report("MISSING", "NVIDIA GPU", "install the NVIDIA driver")
        comfy = service("ComfyUI API", f"{args.comfyui_url.rstrip('/')}/system_stats", "start the media-pipeline profile")
        if comfy is not None:
            report("OK", "ComfyUI API")
        if not args.homelab_root:
            report("MISSING", "HOMELAB_ROOT", "pass --homelab-root or export HOMELAB_ROOT")
        else:
            runtime = Path(os.environ.get("HOMELAB_RUNTIME_DIR", args.homelab_root / "infra" / "runtime"))
            comfy_root = Path(os.environ.get("COMFYUI_SOURCE_DIR", runtime / "comfyui"))
            for node in manifest["comfyui"]["custom_nodes"]:
                path = comfy_root / "custom_nodes" / node["name"]
                report("OK" if git_head(path) == node["commit"] else "MISSING", f"custom node {node['name']}", "rerun bootstrap")
            for model in manifest["comfyui"]["models"]:
                path = comfy_root / model["destination"]
                state = "OK" if path.is_file() else ("OPTIONAL" if args.models_optional else "MISSING")
                report(state, f"model {model['id']}", "rerun bootstrap with --install-models")

    if args.mode == "full":
        n8n = service("n8n", f"{args.n8n_url.rstrip('/')}/healthz", "enable the optional n8n profile")
        if n8n is not None:
            report("OK", "n8n")
        token = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
        chat = bool(os.environ.get("TELEGRAM_CHAT_ID"))
        report("OK" if token and chat else "OPTIONAL", "Telegram", "set credentials in a private environment file")

    return 1 if MISSING else 0


if __name__ == "__main__":
    raise SystemExit(main())
