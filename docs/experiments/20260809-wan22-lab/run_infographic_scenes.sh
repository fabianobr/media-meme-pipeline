#!/bin/bash
# Lab: render the 3 infographic-style scenes (flat vector, consistent palette) via LTX 2.3
# I2V, sequential to avoid GPU contention.
set -u
cd "$(dirname "$0")"
BASE=/home/fabiano/code/media-meme-pipeline/data/media-pipeline/20260809-1958-wan22-lab/scenes
LOG=/home/fabiano/code/media-meme-pipeline/docs/experiments/20260809-wan22-lab/infographic.log
: > "$LOG"

declare -a NAMES=(infog-scene1 infog-scene2d infog-scene3b)
declare -a PROMPTS=(
"Flat 2D motion graphics animation, minimal explainer video style. The glowing orange crack lines on the muscle fiber pulse and flicker gently with a soft heartbeat rhythm, the fiber ribbon shape breathes subtly, clean flat shading, no camera movement, stable frame"
"Flat 2D motion graphics animation, minimal explainer video style. The small orange circles flow smoothly and bounce gently along the dotted arrow path from top to bottom, subtle elastic motion, trailing glow, clean flat shading, no camera movement, stable frame"
"Flat 2D motion graphics animation, minimal explainer video style. The flat-design character confidently flexes both arms in a bouncy elastic motion, the bicep glow highlight pulses brighter, a subtle happy bob of the head, clean flat shading, no camera movement, stable frame"
)

for i in 0 1 2; do
  name="${NAMES[$i]}"; prompt="${PROMPTS[$i]}"
  img="$BASE/base/${name}.png"
  echo "[$(date +%H:%M:%S)] === $name : LTX 2.3 (infographic) ===" >> "$LOG"
  python3 ltx23_render.py --image "$img" --prompt "$prompt" \
    --out-dir "$BASE/ltx-infog/$name" --width 512 --height 896 --frames 126 --seed $((202807400+i)) \
    --prefix "wan22-lab/$name-infog-ltx" >> "$LOG" 2>&1
done
echo "[$(date +%H:%M:%S)] ALL INFOGRAPHIC RENDERS DONE" >> "$LOG"
