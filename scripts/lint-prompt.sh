#!/usr/bin/env bash
# Scan prompt text for anything specs/video-spec.json forbids, BEFORE rendering.
# Pass every string that reaches the model -- the visual prompt AND the spoken
# line -- so a leftover Spanish cue or a cartoon keyword never costs a render.
#
#   scripts/lint-prompt.sh visual-prompt.txt [spoken.txt ...]
#
# Exit 0 = clean. Exit 1 = violations (printed). Exit 2 = usage/tooling error.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
spec="${VIDEO_SPEC:-$here/specs/video-spec.json}"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <prompt-file> [more-files ...]" >&2
  exit 2
fi
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }
[[ -r "$spec" ]] || { echo "cannot read spec: $spec" >&2; exit 2; }

# Lowercased concatenation of every input file, for case-insensitive matching.
text=""
for f in "$@"; do
  [[ -r "$f" ]] || { echo "cannot read prompt file: $f" >&2; exit 2; }
  text+="$(tr '[:upper:]' '[:lower:]' < "$f")"$'\n'
done
prompt_file="$*"
violations=0

# word-boundary grep for a single needle; returns 0 if found
found() {
  local needle
  needle="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  # Word-boundary match only for plain ASCII alphanumeric tokens; everything
  # else (accents, punctuation, multi-word) is a literal substring match.
  if [[ "$needle" =~ ^[a-z0-9]+$ ]]; then
    grep -qiE -- "\\b${needle}\\b" <<<"$text"
  else
    grep -qF -- "$needle" <<<"$text"
  fi
}

echo "== lint-prompt: $prompt_file"

# 1. Forbidden Spanish-language cues (any group).
while IFS= read -r tok; do
  [[ -n "$tok" ]] || continue
  if found "$tok"; then
    echo "  FORBIDDEN spanish cue: '$tok'"
    violations=$((violations + 1))
  fi
done < <(jq -r '.forbidden_prompt_tokens | to_entries[] | select(.key|startswith("$")|not) | .value[]' "$spec")

# 2. Forbidden style keywords (cartoon/anime/...).
while IFS= read -r tok; do
  [[ -n "$tok" ]] || continue
  if found "$tok"; then
    echo "  FORBIDDEN style keyword: '$tok'"
    violations=$((violations + 1))
  fi
done < <(jq -r '.style.forbidden_style_keywords[]' "$spec")

# 3. Required realism keywords (need at least N hits).
need="$(jq -r '.style.required_realism_keyword_min_hits' "$spec")"
hits=0
while IFS= read -r tok; do
  [[ -n "$tok" ]] || continue
  if found "$tok"; then hits=$((hits + 1)); fi
done < <(jq -r '.style.required_realism_keywords[]' "$spec")
if (( hits < need )); then
  echo "  MISSING realism keywords: found $hits, need $need"
  violations=$((violations + 1))
fi

if (( violations > 0 )); then
  echo "== FAIL ($violations violation(s)) -- do not render this prompt"
  exit 1
fi
echo "== OK"
