#!/usr/bin/env bash
# Spec-driven, self-grading render loop:
#   lint prompt -> render -> grade -> mutate ONLY the failing dimension -> re-render
# Hard cap of 5 iterations, then stop and report the grade trend + best hypothesis.
#
# The render approach is fixed by whoever launched this. This script never
# switches approach (e.g. mute + external dubbing). If it cannot pass within the
# approach, it stops and reports -- a human decides what to change.
#
#   scripts/video-accept-loop.sh \
#     --concept concept.json --target-seconds 25 --out-dir data/media-pipeline/accept-loop/<run>
#
# concept.json:
#   { "visual_prompt": "...", "spoken_text": "...", "negative_prompt": "..." }
#
# --render-cmd is a template run for each iteration with these tokens substituted:
#   {prompt_file} {spoken_file} {negative_file} {target_seconds} {out_mp4}
# It must produce {out_mp4}. Default template drives the checked-in pipeline.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
spec="${VIDEO_SPEC:-$here/specs/video-spec.json}"
max_iter=5
concept="" ; target_seconds="" ; out_dir="" ; render_cmd=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --concept) concept="$2"; shift 2 ;;
    --target-seconds) target_seconds="$2"; shift 2 ;;
    --out-dir) out_dir="$2"; shift 2 ;;
    --max-iter) max_iter="$2"; shift 2 ;;
    --render-cmd) render_cmd="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ -r "$concept" && -n "$target_seconds" && -n "$out_dir" ]] || {
  echo "usage: $0 --concept c.json --target-seconds N --out-dir DIR [--max-iter 5] [--render-cmd TPL]" >&2
  exit 2
}
command -v jq >/dev/null || { echo "jq required" >&2; exit 2; }
mkdir -p "$out_dir"

visual="$(jq -r '.visual_prompt' "$concept")"
spoken="$(jq -r '.spoken_text' "$concept")"
negative="$(jq -r '.negative_prompt // ""' "$concept")"

trend="$out_dir/grade-trend.tsv"
echo -e "iter\tverdict\tfailing_rules" > "$trend"

assemble_prompt() { printf '%s\n' "$1"; }

# Mutate exactly one dimension, chosen by the first failing rule.
mutate() {
  local failing="$1"
  case "$failing" in
    max_silent_gap_seconds|speech_to_total_ratio)
      # Not enough speech for the runtime: lengthen the spoken line, same visual.
      spoken="$spoken E o gato encara a câmera por mais um instante, sem pressa, e conclui com calma."
      echo "  mutation: extended spoken_text (more pt-BR narration)"
      ;;
    duration_seconds)
      echo "  mutation: duration off-target is a render-parameter fix, not a prompt fix -- flagging for human"
      return 1
      ;;
    prompt_realism_not_cartoon|spoken_language|no_spanish_in_speech)
      visual="photorealistic, live-action, natural light, $visual"
      negative="cartoon, anime, illustration, cgi, 3d render, stylized, spanish accent, ${negative}"
      echo "  mutation: strengthened realism keywords + pushed style/Spanish cues to negative"
      ;;
    *)
      echo "  mutation: no rule for '$failing' -- flagging for human"
      return 1
      ;;
  esac
}

default_render_cmd='python3 '"$here"'/scripts/daily_reddit_meme_pipeline.py --help >/dev/null; echo "NO --render-cmd given: wire this to your render entrypoint" >&2; exit 3'
[[ -n "$render_cmd" ]] || render_cmd="$default_render_cmd"

best_iter="" ; best_fail_count=999
for ((i = 1; i <= max_iter; i++)); do
  d="$out_dir/iter-$i"; mkdir -p "$d"
  pf="$d/prompt.txt" ; sf="$d/spoken.txt" ; nf="$d/negative.txt" ; mp4="$d/render.mp4"
  assemble_prompt "$visual" > "$pf"
  printf '%s\n' "$spoken" > "$sf"
  printf '%s\n' "$negative" > "$nf"

  echo "== iteration $i"
  if ! "$here/scripts/lint-prompt.sh" "$pf"; then
    echo "  prompt failed lint; mutating without spending a render"
    mutate "prompt_realism_not_cartoon" || { echo "iter $i: lint dead-end" ; break ; }
    echo -e "$i\tLINT_FAIL\tprompt" >> "$trend"
    continue
  fi

  cmd="${render_cmd//\{prompt_file\}/$pf}"
  cmd="${cmd//\{spoken_file\}/$sf}"
  cmd="${cmd//\{negative_file\}/$nf}"
  cmd="${cmd//\{target_seconds\}/$target_seconds}"
  cmd="${cmd//\{out_mp4\}/$mp4}"
  echo "  render: $cmd"
  if ! bash -c "$cmd"; then
    echo -e "$i\tRENDER_FAIL\t-" >> "$trend"
    echo "  render failed; see output above"
    break
  fi

  set +e
  "$here/scripts/grade-video.sh" "$mp4" --target-seconds "$target_seconds" --prompt "$pf" > "$d/grade.json"
  graded=$?
  set -e
  cat "$d/grade.json"
  failing="$(jq -r '.rules | to_entries[] | select(.value.pass == false) | .key' "$d/grade.json" | paste -sd, -)"
  echo -e "$i\t$(jq -r .verdict "$d/grade.json")\t${failing:-none}" >> "$trend"

  fc="$(jq -r '[.rules[] | select(.pass == false)] | length' "$d/grade.json")"
  if (( fc < best_fail_count )); then best_fail_count=$fc; best_iter=$i; fi

  if (( graded == 0 )); then
    echo "== PASS at iteration $i"
    echo "PASS_ITER=$i"
    echo "$mp4"
    exit 0
  fi

  first_fail="$(jq -r '.rules | to_entries[] | select(.value.pass == false) | .key' "$d/grade.json" | head -n1)"
  echo "  first failing rule: $first_fail"
  if ! mutate "$first_fail"; then
    echo "== dead-end at iteration $i: no in-approach mutation for '$first_fail'"
    break
  fi
done

echo "== no passing candidate in $max_iter iteration(s)"
echo "-- grade trend --"
column -t -s $'\t' "$trend"
echo "-- best iteration: ${best_iter:-none} ($best_fail_count failing rule(s)) --"
exit 1
