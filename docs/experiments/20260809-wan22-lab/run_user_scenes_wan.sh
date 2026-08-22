#!/bin/bash
# Lab: render the user's own 3 scene prompts via Wan 2.2 (as requested), sequential
# to avoid GPU/RAM contention.
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd ../../.. && pwd)
BASE="$ROOT/data/media-pipeline/20260809-1958-wan22-lab/scenes"
LOG="$BASE/logs/user-scenes-wan.log"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"

NEGATIVE="morphing, warping, distortion, face deformation, flickering, jittering, sudden changes, blurry, low quality, bad anatomy, text, watermark"

declare -a NAMES=(user-scene1 user-scene2 user-scene3)
declare -a PROMPTS=(
"Vertical 9:16 video, close-up tracking shot. High-quality 3D anatomical visualization of glowing red human biceps muscle fibers straining and developing subtle microscopic micro-tears during a barbell workout. Modern fitness app graphical style, warm lighting, smooth motion, high detail."
"Vertical 9:16 video, macro camera zoom. Glowing cyan and white protein nutrients and amino acids dynamically weaving and rebuilding damaged muscle tissue. Soft ambient bedroom lighting blending with futuristic scientific hologram overlay, smooth organic motion, 24fps."
"Vertical 9:16 video, medium dolly-in shot. A cheerful young beginner gym-goer smiling in a modern bright gym, holding a water bottle. Overlaid with a glowing 3D graphic showing stronger, reinforced muscle fibers expanding smoothly. Vibrant lighting, energetic mood, cinematic depth of field."
)

for i in 0 1 2; do
  name="${NAMES[$i]}"; prompt="${PROMPTS[$i]}"
  img="$BASE/base/${name}.png"
  echo "[$(date +%H:%M:%S)] === $name : WAN 2.2 ===" >> "$LOG"
  python3 wan22_render.py --image "$img" --prompt "$prompt" --negative "$NEGATIVE" \
    --out-dir "$BASE/wan-user/$name" --width 480 --height 832 --length 81 --seed $((202807500+i)) \
    --prefix "wan22-lab/$name-wan" >> "$LOG" 2>&1
done
echo "[$(date +%H:%M:%S)] ALL USER SCENE RENDERS DONE" >> "$LOG"
