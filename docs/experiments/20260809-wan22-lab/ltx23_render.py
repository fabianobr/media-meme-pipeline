#!/usr/bin/env python3
"""Lab driver for the LTX 2.3 A/B leg: reuses the repo's checked-in, validated
official I2V workflow (workflows/05-ltx23-official-i2v-audio-api.json) as-is,
parameterizing only its declared inputs -- same node-index mapping as
queue_comfy_ltx23_native_video() in scripts/daily_reddit_meme_pipeline.py, no
second hand-built graph. Uso:

  ltx23_render.py --image base.png --prompt "..." --out-dir /tmp/x \
      [--width 480 --height 832 --frames 126 --seed 202807000]
"""
import argparse, json, mimetypes, os, subprocess, sys, threading, time, urllib.parse, urllib.request, uuid

COMFY = os.environ.get("COMFYUI_URL", "http://localhost:8188")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
WF = os.path.join(PROJECT_ROOT, "workflows", "05-ltx23-official-i2v-audio-api.json")

DEFAULT_LTX23_CKPT_NAME = "ltx-2.3-22b-dev-fp8.safetensors"
DEFAULT_LTX23_TEXT_ENCODER = "gemma_3_12B_it_fp4_mixed.safetensors"
DEFAULT_LTX23_LORA = "ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors"
DEFAULT_LTX23_UPSCALER = "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"
LTX23_NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly"


def http(path, data=None, headers=None):
    req = urllib.request.Request(COMFY + path, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def upload_image(path):
    name = f"wan22lab_ltx_{uuid.uuid4().hex[:8]}_{os.path.basename(path)}"
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        img = f.read()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n").encode() + img + f"\r\n--{boundary}--\r\n".encode()
    resp = http("/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(resp)["name"]


class PeakMonitor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.ram_gb = self.vram_mb = 0.0
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "comfyui"],
                                     capture_output=True, text=True, timeout=15).stdout.split("/")[0].strip()
                if out.endswith("GiB"):
                    self.ram_gb = max(self.ram_gb, float(out.rstrip("GiB")))
                sm = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=15).stdout.strip()
                self.vram_mb = max(self.vram_mb, float(sm))
            except Exception:
                pass
            self.stop.wait(10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--height", type=int, default=832)
    ap.add_argument("--frames", type=int, default=126)
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("--seed", type=int, default=202807000)
    ap.add_argument("--prefix", default="wan22-lab/ltx-scene")
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()

    with open(WF) as f:
        document = json.load(f)
    prompt = document["prompt"]
    expected_nodes = {str(i) for i in range(1, 37)}
    if set(prompt) != expected_nodes:
        raise SystemExit("validated LTX 2.3 workflow node set changed unexpectedly")

    uploaded = upload_image(a.image)
    prompt["1"]["inputs"]["ckpt_name"] = DEFAULT_LTX23_CKPT_NAME
    prompt["2"]["inputs"].update({"lora_name": DEFAULT_LTX23_LORA, "strength_model": 0.5})
    prompt["3"]["inputs"].update({"text_encoder": DEFAULT_LTX23_TEXT_ENCODER, "ckpt_name": DEFAULT_LTX23_CKPT_NAME, "device": "cpu"})
    prompt["4"]["inputs"]["ckpt_name"] = DEFAULT_LTX23_CKPT_NAME
    prompt["5"]["inputs"]["text"] = a.prompt
    prompt["6"]["inputs"]["text"] = LTX23_NEGATIVE
    prompt["7"]["inputs"]["frame_rate"] = a.fps
    prompt["8"]["inputs"]["image"] = uploaded
    prompt["9"]["inputs"].update({"width": a.width, "height": a.height})
    prompt["11"]["inputs"]["img_compression"] = 18
    prompt["12"]["inputs"].update({"width": a.width // 2, "height": a.height // 2, "length": a.frames, "batch_size": 1})
    prompt["14"]["inputs"].update({"frames_number": a.frames, "frame_rate": int(a.fps), "batch_size": 1})
    prompt["16"]["inputs"]["noise_seed"] = a.seed
    prompt["22"]["inputs"]["model_name"] = DEFAULT_LTX23_UPSCALER
    prompt["35"]["inputs"]["fps"] = a.fps
    prompt["36"]["inputs"].update({"filename_prefix": a.prefix, "format": "mp4", "codec": "h264"})

    mon = PeakMonitor(); mon.start()
    t0 = time.time()
    resp = json.loads(http("/prompt", json.dumps({"prompt": prompt}).encode(), {"Content-Type": "application/json"}))
    pid = resp["prompt_id"]
    print(f"queued prompt_id={pid}", flush=True)

    outputs = None
    while time.time() - t0 < a.timeout:
        time.sleep(10)
        hist = json.loads(http(f"/history/{pid}")).get(pid)
        if not hist:
            continue
        status = hist.get("status", {})
        if status.get("status_str") == "error":
            mon.stop.set()
            print("RENDER ERROR:", json.dumps(status, indent=2)[:2000], file=sys.stderr)
            sys.exit(1)
        if hist.get("outputs"):
            outputs = hist["outputs"]
            break
    mon.stop.set()
    if outputs is None:
        print("TIMEOUT waiting for render", file=sys.stderr)
        sys.exit(2)

    dt = time.time() - t0
    os.makedirs(a.out_dir, exist_ok=True)
    saved = []
    for node in outputs.values():
        for key in ("images", "video", "videos", "gifs"):
            for item in node.get(key, []):
                q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")})
                dest = os.path.join(a.out_dir, item["filename"])
                with open(dest, "wb") as f:
                    f.write(http(f"/view?{q}"))
                saved.append(dest)
    print(json.dumps({"prompt_id": pid, "seconds": round(dt, 1), "files": saved,
                      "peak_ram_gib": round(mon.ram_gb, 1), "peak_vram_mib": int(mon.vram_mb)}, indent=2))


if __name__ == "__main__":
    main()
