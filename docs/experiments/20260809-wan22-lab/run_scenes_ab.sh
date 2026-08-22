#!/bin/bash
# Lab: render the 3 "recuperação muscular" scenes with both Wan 2.2 and LTX 2.3,
# same base images/prompts, sequential to avoid GPU contention (16GB VRAM shared).
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd ../../.. && pwd)
BASE="$ROOT/data/media-pipeline/20260809-1958-wan22-lab/scenes"
LOG="$BASE/logs/scenes.log"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"

declare -a NAMES=(scene1-microtears scene2-repair scene3-result)
declare -a PROMPTS=(
"Close-up shot, microscopic level inside human muscle tissue. Individual muscle fibers showing tiny, clean microscopic micro-tears glowing softly in warm orange and red light, smooth camera zoom-in through the tissue, hyper-realistic 3D animation, educational, accessible style."
"Macro 3D animation. Glowing blue and green protein molecules and amino acids flowing through blood vessels, traveling directly to the damaged muscle fibers. The glowing particles attach to the micro-tears and construct new tissue, building a thicker layer over the muscle. Smooth cinematic camera motion, bright and engaging colors."
"Cross-section animated view. The microscopic muscle fibers finish healing and visibly expand, becoming thicker, stronger, and more dense. Transitions smoothly to a stylized 3D avatar flexing arm biceps with a subtle green glow showing muscle growth, casual modern gym background, clean aesthetic."
)

for i in 0 1 2; do
  name="${NAMES[$i]}"; prompt="${PROMPTS[$i]}"
  img="$BASE/base/${name}.png"
  echo "[$(date +%H:%M:%S)] === $name : WAN 2.2 ===" >> "$LOG"
  python3 wan22_render.py --image "$img" --prompt "$prompt" \
    --out-dir "$BASE/wan/$name" --width 480 --height 832 --length 81 --seed $((100+i)) \
    --prefix "wan22-lab/$name-wan" >> "$LOG" 2>&1
  echo "[$(date +%H:%M:%S)] === $name : LTX 2.3 ===" >> "$LOG"
  python3 ltx23_render.py --image "$img" --prompt "$prompt" \
    --out-dir "$BASE/ltx/$name" --width 512 --height 896 --frames 126 --seed $((202807100+i)) \
    --prefix "wan22-lab/$name-ltx" >> "$LOG" 2>&1
done
echo "[$(date +%H:%M:%S)] ALL SCENE RENDERS DONE" >> "$LOG"
