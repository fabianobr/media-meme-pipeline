#!/usr/bin/env python3
"""Driver do lab Wan 2.2 I2V (2026-08-09): parametriza o workflow API, faz upload da
imagem, enfileira no ComfyUI, faz poll do /history e baixa o MP4. Mede pico de RAM do
container e de VRAM durante o render. Uso:

  python3 wan22_render.py --image base.png --prompt "..." --out-dir /tmp/x \
      [--width 480 --height 832 --length 81 --seed 42 --prefix wan22-lab/smoke]
"""
import argparse, json, mimetypes, os, subprocess, sys, threading, time, urllib.request, uuid

COMFY = os.environ.get("COMFYUI_URL", "http://localhost:8188")
WF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wan22-i2v-gguf-lightning-api.json")


def http(path, data=None, headers=None):
    req = urllib.request.Request(COMFY + path, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def upload_image(path):
    name = f"wan22lab_{uuid.uuid4().hex[:8]}_{os.path.basename(path)}"
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
                val = float(out.rstrip("GMiB").rstrip("GiB") or 0)
                if out.endswith("GiB"):
                    self.ram_gb = max(self.ram_gb, val)
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
    ap.add_argument("--length", type=int, default=81)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--prefix", default="wan22-lab/output")
    ap.add_argument("--negative", default=None)
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()

    with open(WF) as f:
        wf = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    wf["8"]["inputs"]["text"] = a.prompt
    if a.negative:
        wf["9"]["inputs"]["text"] = a.negative
    wf["11"]["inputs"]["image"] = upload_image(a.image)
    wf["12"]["inputs"].update(width=a.width, height=a.height, length=a.length)
    wf["13"]["inputs"]["noise_seed"] = a.seed
    wf["17"]["inputs"]["filename_prefix"] = a.prefix

    mon = PeakMonitor(); mon.start()
    t0 = time.time()
    resp = json.loads(http("/prompt", json.dumps({"prompt": wf}).encode(), {"Content-Type": "application/json"}))
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
                q = urllib.parse.urlencode({"filename": item["filename"],
                                            "subfolder": item.get("subfolder", ""),
                                            "type": item.get("type", "output")})
                dest = os.path.join(a.out_dir, item["filename"])
                with open(dest, "wb") as f:
                    f.write(http(f"/view?{q}"))
                saved.append(dest)
    print(json.dumps({"prompt_id": pid, "seconds": round(dt, 1), "files": saved,
                      "peak_ram_gib": round(mon.ram_gb, 1), "peak_vram_mib": int(mon.vram_mb)}, indent=2))


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    main()
