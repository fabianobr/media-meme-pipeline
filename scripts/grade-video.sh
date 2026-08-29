#!/usr/bin/env bash
# Mechanically grade a rendered video against specs/video-spec.json.
# Every rule is a computed number -- never an opinion.
#
#   scripts/grade-video.sh path/to/render.mp4 --target-seconds 25 [--prompt prompt.txt]
#
# Emits a JSON verdict on stdout:
#   { "file":..., "target_seconds":..., "rules": { name: {value, threshold, pass} }, "verdict": "PASS|FAIL" }
# Exit 0 = PASS, 1 = FAIL, 2 = usage/tooling error.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
spec="${VIDEO_SPEC:-$here/specs/video-spec.json}"
whisper_model="${WHISPER_MODEL:-base}"

file="" ; target_seconds="" ; prompt_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-seconds) target_seconds="$2"; shift 2 ;;
    --prompt) prompt_file="$2"; shift 2 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) file="$1"; shift ;;
  esac
done

[[ -n "$file" && -r "$file" ]] || { echo "usage: $0 <file> --target-seconds N [--prompt p.txt]" >&2; exit 2; }
for bin in ffprobe ffmpeg jq python3; do command -v "$bin" >/dev/null || { echo "$bin required" >&2; exit 2; }; done
[[ -r "$spec" ]] || { echo "cannot read spec: $spec" >&2; exit 2; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# --- 1. duration -----------------------------------------------------------
duration="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$file" | head -n1)"
[[ -n "$duration" ]] || { echo "ffprobe found no duration" >&2; exit 2; }

# --- 2. silence (ffmpeg silencedetect) -----------------------------------
noise_db="$(jq -r '.audio.silencedetect_noise_db' "$spec")"
min_sil="$(jq -r '.audio.silencedetect_min_duration_seconds' "$spec")"
ffmpeg -hide_banner -nostats -i "$file" \
  -af "silencedetect=noise=${noise_db}dB:d=${min_sil}" -f null - 2>"$tmp/sil.txt" || true
grep -oE 'silence_duration: [0-9.]+' "$tmp/sil.txt" | awk '{print $2}' > "$tmp/gaps.txt" || true

# --- 3. ASR: transcript + detected language -----------------------------
asr_ok=1
if command -v whisper >/dev/null; then
  whisper "$file" --model "$whisper_model" --task transcribe \
    --output_format json --output_dir "$tmp" --verbose False --fp16 False \
    >/dev/null 2>"$tmp/whisper.err" || asr_ok=0
else
  asr_ok=0
fi
asr_json="$tmp/$(basename "${file%.*}").json"
[[ $asr_ok -eq 1 && -r "$asr_json" ]] || asr_json=""

# --- 4. assemble verdict (python does the arithmetic) -------------------
SPEC="$spec" FILE="$file" DURATION="$duration" TARGET="$target_seconds" \
GAPS="$tmp/gaps.txt" ASR_JSON="$asr_json" PROMPT_FILE="$prompt_file" \
python3 - <<'PY'
import json, os, re, sys

spec = json.load(open(os.environ["SPEC"]))
audio, dur = spec["audio"], float(os.environ["DURATION"])
gaps = [float(x) for x in open(os.environ["GAPS"]).read().split() if x.strip()]
total_silence = sum(gaps)
max_gap = max(gaps) if gaps else 0.0
speech_ratio = max(0.0, (dur - total_silence) / dur) if dur > 0 else 0.0

rules = {}

def rule(name, value, threshold, ok):
    rules[name] = {"value": round(value, 4) if isinstance(value, float) else value,
                   "threshold": threshold, "pass": bool(ok)}

rule("max_silent_gap_seconds", max_gap, audio["max_silent_gap_seconds"],
     max_gap <= audio["max_silent_gap_seconds"])
rule("speech_to_total_ratio", speech_ratio, audio["min_speech_to_total_ratio"],
     speech_ratio >= audio["min_speech_to_total_ratio"])

target = os.environ.get("TARGET") or ""
if target:
    target = float(target)
    tol = spec["duration"]["tolerance_pct"] / 100.0
    lo, hi = target * (1 - tol), target * (1 + tol)
    rule("duration_seconds", dur, f"{lo:.2f}..{hi:.2f}", lo <= dur <= hi)
else:
    rule("duration_seconds", dur, "no --target-seconds given", None)

# language + forbidden tokens in the spoken transcript
asr = os.environ.get("ASR_JSON") or ""
if asr:
    data = json.load(open(asr))
    lang = data.get("language", "")
    text = (data.get("text") or "").lower()
    want_lang = spec["target_language"].split("-")[0].lower()  # 'pt-BR' -> 'pt'
    rule("spoken_language", lang, spec["target_language"], lang.lower() == want_lang)

    forbidden = []
    for group, toks in spec["forbidden_prompt_tokens"].items():
        if group.startswith("$"):
            continue
        for t in toks:
            tl = t.lower()
            if re.fullmatch(r"[a-z0-9]+", tl):
                hit = re.search(rf"\b{re.escape(tl)}\b", text)
            else:
                hit = tl in text
            if hit:
                forbidden.append(t)
    rule("no_spanish_in_speech", forbidden or "none", "none", not forbidden)
else:
    rule("spoken_language", None, spec["target_language"], False)
    rules["spoken_language"]["reason"] = "no ASR (whisper) available or failed"
    rule("no_spanish_in_speech", None, "none", False)
    rules["no_spanish_in_speech"]["reason"] = "no ASR (whisper) available or failed"

# prompt-only realism/style rule (only when --prompt is passed)
pf = os.environ.get("PROMPT_FILE") or ""
if pf and os.path.isfile(pf):
    ptext = open(pf).read().lower()
    style = spec["style"]
    bad = [k for k in style["forbidden_style_keywords"] if k.lower() in ptext]
    hits = sum(1 for k in style["required_realism_keywords"] if k.lower() in ptext)
    ok = (not bad) and hits >= style["required_realism_keyword_min_hits"]
    rule("prompt_realism_not_cartoon",
         {"forbidden_present": bad or "none", "realism_hits": hits},
         f">= {style['required_realism_keyword_min_hits']} realism, 0 forbidden", ok)

measured = [v for v in rules.values() if v["pass"] is not None]
verdict = "PASS" if all(v["pass"] for v in measured) else "FAIL"
out = {"file": os.environ["FILE"],
       "target_seconds": (float(target) if target else None),
       "rules": rules, "verdict": verdict}
json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
print()
sys.exit(0 if verdict == "PASS" else 1)
PY
