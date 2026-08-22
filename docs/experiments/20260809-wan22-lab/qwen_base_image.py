#!/usr/bin/env python3
"""Lab driver: generate one base image via Qwen-Image + Lightning LoRA (local,
already-installed models). Uso: qwen_base_image.py --prompt "..." --out img.png
[--width 480 --height 832 --seed 1]"""
import argparse, json, os, sys, time, urllib.parse, urllib.request

COMFY = os.environ.get("COMFYUI_URL", "http://localhost:8188")
WF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qwen-image-base-api.json")


def http(path, data=None, headers=None):
    req = urllib.request.Request(COMFY + path, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--height", type=int, default=832)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()

    with open(WF) as f:
        wf = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    wf["3"]["inputs"]["text"] = a.prompt
    wf["5"]["inputs"].update(width=a.width, height=a.height)
    wf["6"]["inputs"]["seed"] = a.seed

    resp = json.loads(http("/prompt", json.dumps({"prompt": wf}).encode(), {"Content-Type": "application/json"}))
    pid = resp["prompt_id"]
    print(f"queued prompt_id={pid}", flush=True)

    t0 = time.time()
    outputs = None
    while time.time() - t0 < a.timeout:
        time.sleep(3)
        hist = json.loads(http(f"/history/{pid}")).get(pid)
        if not hist:
            continue
        if hist.get("status", {}).get("status_str") == "error":
            print("ERROR:", json.dumps(hist["status"])[:1500], file=sys.stderr)
            sys.exit(1)
        if hist.get("outputs"):
            outputs = hist["outputs"]
            break
    if outputs is None:
        print("TIMEOUT", file=sys.stderr)
        sys.exit(2)

    for node in outputs.values():
        for item in node.get("images", []):
            q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")})
            with open(a.out, "wb") as f:
                f.write(http(f"/view?{q}"))
            print(f"saved {a.out}")
            return
    print("no image output", file=sys.stderr)
    sys.exit(3)


if __name__ == "__main__":
    main()
