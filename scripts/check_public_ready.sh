#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

private_files="$(git ls-files | grep -E '(^|/)(\.env($|\.)|data/|outputs?/|runs?/|payloads?/)|\.(mp4|mov|mkv|webm|mp3|wav)$' | grep -v '^\.env\.example$' || true)"
if [[ -n "${private_files}" ]]; then
  echo "MISSING: generated/private files are tracked" >&2
  echo "${private_files}" >&2
  exit 1
fi

secret_pattern='(-----BEGIN [A-Z ]*PRIVATE KEY-----|hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|TELEGRAM_BOT_TOKEN=[^[:space:]]+|TELEGRAM_CHAT_ID=[0-9-]+)'
if git grep -n -E "${secret_pattern}" -- ':!.env.example' ':!scripts/check_public_ready.sh'; then
  echo "MISSING: possible secret in tracked content; review and rotate it" >&2
  exit 1
fi

python3 -m py_compile scripts/*.py
bash -n scripts/*.sh
git diff --check

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks git --redact --no-banner
else
  echo "OPTIONAL: install gitleaks for a full history scan"
fi

if [[ -x .venv/bin/pip-audit ]]; then
  .venv/bin/pip-audit -r requirements.lock
else
  echo "OPTIONAL: install pip-audit in .venv to scan Python dependencies"
fi

echo "OK: public-readiness checks passed"
