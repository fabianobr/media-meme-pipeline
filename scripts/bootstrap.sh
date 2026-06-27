#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
HOMELAB_ROOT="${HOMELAB_ROOT:-}"
HOMELAB_TAG="v1.0.0"
INSTALL_MODELS=false
ACCEPT_LICENSES=false

usage() {
  echo "Usage: $0 --homelab-root PATH [--homelab-tag TAG] [--install-models --accept-model-licenses]"
}

while (($#)); do
  case "$1" in
    --homelab-root) HOMELAB_ROOT="${2:?missing value}"; shift 2 ;;
    --homelab-tag) HOMELAB_TAG="${2:?missing value}"; shift 2 ;;
    --install-models) INSTALL_MODELS=true; shift ;;
    --accept-model-licenses) ACCEPT_LICENSES=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "MISSING: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

fail() { echo "MISSING: $*" >&2; exit 2; }
need() { command -v "$1" >/dev/null 2>&1 || fail "$1 is required"; }

[[ -n "${HOMELAB_ROOT}" ]] || fail "pass --homelab-root or export HOMELAB_ROOT"
HOMELAB_ROOT="$(realpath "${HOMELAB_ROOT}")"
[[ -f "${HOMELAB_ROOT}/infra/media-pipeline/contract.yaml" ]] || fail "homelab-ai contract not found at ${HOMELAB_ROOT}"
[[ -r /etc/os-release ]] || fail "Ubuntu is the supported platform"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail "Ubuntu is the supported platform (detected ${ID:-unknown})"

need git
need python3
need docker
need nvidia-smi
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable; configure user access without implicit sudo"
nvidia-smi >/dev/null 2>&1 || fail "NVIDIA driver is unavailable"
need nvidia-ctk
nvidia-ctk cdi list 2>/dev/null | grep -q 'nvidia.com/gpu' || fail "NVIDIA CDI is missing; run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"

actual_tag="$(git -C "${HOMELAB_ROOT}" describe --tags --exact-match 2>/dev/null || true)"
[[ "${actual_tag}" == "${HOMELAB_TAG}" ]] || fail "homelab-ai must be checked out at exact tag ${HOMELAB_TAG} (current: ${actual_tag:-untagged}); run: git -C ${HOMELAB_ROOT} fetch --tags && git -C ${HOMELAB_ROOT} switch --detach ${HOMELAB_TAG}"
grep -q "^homelab_release: ${HOMELAB_TAG}$" "${HOMELAB_ROOT}/infra/media-pipeline/contract.yaml" || fail "homelab contract does not declare ${HOMELAB_TAG}"

vram_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | sort -nr | head -1)"
((vram_mib >= 16384)) || fail "at least one NVIDIA GPU with 16 GiB VRAM is required"
memory_kib="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
((memory_kib >= 16 * 1024 * 1024)) || fail "at least 16 GiB system RAM is required"
free_kib="$(df -Pk "${HOMELAB_ROOT}" | awk 'NR==2 {print $4}')"
((free_kib >= 100 * 1024 * 1024)) || fail "at least 100 GiB free disk is required"

python3 -m venv "${ROOT}/.venv"
"${ROOT}/.venv/bin/python" -m pip install --disable-pip-version-check -r "${ROOT}/requirements.lock"

export HOMELAB_ROOT
export HOMELAB_RUNTIME_DIR="${HOMELAB_RUNTIME_DIR:-${HOMELAB_ROOT}/infra/runtime}"
export COMFYUI_SOURCE_DIR="${COMFYUI_SOURCE_DIR:-${HOMELAB_RUNTIME_DIR}/comfyui}"
export OLLAMA_DATA_DIR="${OLLAMA_DATA_DIR:-${HOMELAB_RUNTIME_DIR}/ollama}"
"${HOMELAB_ROOT}/infra/scripts/prepare-media-pipeline.sh"
docker compose -f "${HOMELAB_ROOT}/infra/docker/docker-compose.yml" --profile media-pipeline up -d ollama comfyui

if [[ "${INSTALL_MODELS}" == true ]]; then
  [[ "${ACCEPT_LICENSES}" == true ]] || fail "--install-models requires --accept-model-licenses after reviewing infra/models.lock.yaml"
  while IFS= read -r model; do
    docker compose -f "${HOMELAB_ROOT}/infra/docker/docker-compose.yml" exec -T ollama ollama pull "${model}"
  done < <("${ROOT}/.venv/bin/python" -c 'import yaml; print("\n".join(x["name"] for x in yaml.safe_load(open("infra/models.lock.yaml"))["ollama"]))')
  "${ROOT}/.venv/bin/python" "${ROOT}/scripts/install_models.py" --comfyui-root "${COMFYUI_SOURCE_DIR}" --accept-licenses
else
  echo "OPTIONAL: model downloads skipped; rerun with --install-models --accept-model-licenses"
fi

check_args=(--mode "$([[ "${INSTALL_MODELS}" == true ]] && echo render || echo dry-run)" --homelab-root "${HOMELAB_ROOT}")
[[ "${INSTALL_MODELS}" == true ]] || check_args+=(--models-optional)
"${ROOT}/.venv/bin/python" "${ROOT}/scripts/check_environment.py" "${check_args[@]}"
echo "OK: bootstrap complete"
