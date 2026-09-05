#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${OPENLOCALAI_MODEL:-qwen3.5:4b-q4_K_M}"
OUTPUT_DIR=''
APP_IMAGE="${OPENLOCALAI_IMAGE:-openlocalai-workbench:local}"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-ollama/ollama:0.33.3}"
ALPINE_IMAGE="alpine:3.21.3"
TEMP_DIR=''
BUNDLE_PROJECT="openlocalai-bundle-$(date '+%s')-$$"
BUNDLE_MODEL_VOLUME="${BUNDLE_PROJECT}-ollama-models"
BUNDLE_APP_VOLUME="${BUNDLE_PROJECT}-app-data"
BUNDLE_NETWORK="${BUNDLE_PROJECT}-backend"
BUNDLE_OLLAMA_CONTAINER="${BUNDLE_PROJECT}-ollama"
BUNDLE_INIT_CONTAINER="${BUNDLE_PROJECT}-model-init"
BUNDLE_APP_CONTAINER="${BUNDLE_PROJECT}-app"

usage() {
  printf '%s\n' \
    '用法: ./scripts/build-offline-bundle.sh [选项]' \
    '' \
    '  --model NAME    要随离线包携带的 Ollama 模型' \
    '  --output PATH   输出目录；默认 offline-bundles/openlocalai-offline-时间戳' \
    '  -h, --help      显示帮助'
}

die() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ "${BUNDLE_PROJECT}" =~ ^openlocalai-bundle-[0-9]+-[0-9]+$ && -n "${TEMP_DIR}" && -f "${TEMP_DIR}/bundle.env" && -f "${TEMP_DIR}/bundle.override.yaml" ]]; then
    docker compose \
      --project-name "${BUNDLE_PROJECT}" \
      --env-file "${TEMP_DIR}/bundle.env" \
      -f "${ROOT_DIR}/compose.yaml" \
      -f "${TEMP_DIR}/bundle.override.yaml" \
      down --volumes --remove-orphans >/dev/null 2>&1 || true
    docker volume rm "${BUNDLE_MODEL_VOLUME}" >/dev/null 2>&1 || true
    docker volume rm "${BUNDLE_APP_VOLUME}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
    rm -rf -- "${TEMP_DIR}"
  fi
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --model)
      (($# >= 2)) || die '--model 缺少值'
      MODEL="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || die '--output 缺少值'
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数：$1"
      ;;
  esac
done

case "${MODEL}" in
  qwen3.5:4b-q4_K_M|qwen3.5:9b-q4_K_M|qwen3.5:27b-q4_K_M|qwen3:4b) ;;
  *) die '模型不在已审核的生成模型及许可证清单中' ;;
esac
[[ "${OLLAMA_IMAGE##*/}" == *':0.33.3' ]] || die 'OLLAMA_IMAGE 必须固定为 0.33.3 标签'
if [[ -z "${OUTPUT_DIR}" ]]; then
  OUTPUT_DIR="${ROOT_DIR}/offline-bundles/openlocalai-offline-$(date '+%Y%m%d-%H%M%S')"
elif [[ "${OUTPUT_DIR}" != /* ]]; then
  OUTPUT_DIR="$(pwd)/${OUTPUT_DIR}"
fi
ARCHIVE_PATH="${OUTPUT_DIR}.tar.gz"
[[ ! -e "${OUTPUT_DIR}" && ! -e "${ARCHIVE_PATH}" ]] \
  || die "输出目标已存在：${OUTPUT_DIR} 或 ${ARCHIVE_PATH}"

command -v docker >/dev/null 2>&1 || die '未找到 Docker。'
docker info >/dev/null 2>&1 || die 'Docker 服务未运行。'
docker compose version >/dev/null 2>&1 || die '缺少 Docker Compose v2。'
command -v gzip >/dev/null 2>&1 || die '缺少 gzip。'
command -v tar >/dev/null 2>&1 || die '缺少 tar。'
command -v python3 >/dev/null 2>&1 || die '缺少 Python 3，无法生成 SPDX SBOM。'

TEMP_DIR="$(mktemp -d)"
umask 077
{
  printf 'OPENLOCALAI_MODEL=%s\n' "${MODEL}"
  printf 'OPENLOCALAI_PORT=8765\n'
  printf 'OPENLOCALAI_BIND=127.0.0.1\n'
  printf 'OPENLOCALAI_IMAGE=%s\n' "${APP_IMAGE}"
  printf 'OLLAMA_IMAGE=%s\n' "${OLLAMA_IMAGE}"
  printf 'OLLAMA_CONTEXT_LENGTH=4096\n'
  printf 'OLLAMA_NUM_PARALLEL=1\n'
  printf 'OLLAMA_KV_CACHE_TYPE=q8_0\n'
  printf 'BUNDLE_MODEL_VOLUME=%s\n' "${BUNDLE_MODEL_VOLUME}"
  printf 'BUNDLE_APP_VOLUME=%s\n' "${BUNDLE_APP_VOLUME}"
  printf 'BUNDLE_NETWORK=%s\n' "${BUNDLE_NETWORK}"
  printf 'BUNDLE_OLLAMA_CONTAINER=%s\n' "${BUNDLE_OLLAMA_CONTAINER}"
  printf 'BUNDLE_INIT_CONTAINER=%s\n' "${BUNDLE_INIT_CONTAINER}"
  printf 'BUNDLE_APP_CONTAINER=%s\n' "${BUNDLE_APP_CONTAINER}"
} >"${TEMP_DIR}/bundle.env"

{
  printf 'services:\n'
  printf '  ollama:\n'
  printf '    container_name: ${BUNDLE_OLLAMA_CONTAINER}\n'
  printf '  model-init:\n'
  printf '    container_name: ${BUNDLE_INIT_CONTAINER}\n'
  printf '  app:\n'
  printf '    container_name: ${BUNDLE_APP_CONTAINER}\n'
  printf 'networks:\n'
  printf '  backend:\n'
  printf '    name: ${BUNDLE_NETWORK}\n'
  printf 'volumes:\n'
  printf '  app_data:\n'
  printf '    name: ${BUNDLE_APP_VOLUME}\n'
  printf '  ollama_models:\n'
  printf '    name: ${BUNDLE_MODEL_VOLUME}\n'
} >"${TEMP_DIR}/bundle.override.yaml"

compose=(
  docker compose
  --project-name "${BUNDLE_PROJECT}"
  --env-file "${TEMP_DIR}/bundle.env"
  -f "${ROOT_DIR}/compose.yaml"
  -f "${TEMP_DIR}/bundle.override.yaml"
)

printf '1/7 拉取运行时镜像并构建应用镜像。\n'
"${compose[@]}" pull ollama model-init
"${compose[@]}" build app
docker pull "${ALPINE_IMAGE}"

docker volume inspect "${BUNDLE_MODEL_VOLUME}" >/dev/null 2>&1 \
  && die "临时模型卷意外存在：${BUNDLE_MODEL_VOLUME}"
"${compose[@]}" up -d ollama

printf '2/7 等待 Ollama 就绪。\n'
deadline=$((SECONDS + 180))
while ((SECONDS < deadline)); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${BUNDLE_OLLAMA_CONTAINER}" 2>/dev/null || true)"
  [[ "${status}" == 'healthy' ]] && break
  sleep 2
done
status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${BUNDLE_OLLAMA_CONTAINER}" 2>/dev/null || true)"
[[ "${status}" == 'healthy' ]] || die "Ollama 未就绪：${status:-不存在}"

printf '3/7 下载并校验模型 %s。\n' "${MODEL}"
"${compose[@]}" run --rm --no-deps model-init
docker exec "${BUNDLE_OLLAMA_CONTAINER}" ollama show "${MODEL}" >/dev/null

mkdir -p -- "${OUTPUT_DIR}"
cp "${ROOT_DIR}/compose.yaml" "${OUTPUT_DIR}/compose.yaml"
cp "${ROOT_DIR}/compose.nvidia.yaml" "${OUTPUT_DIR}/compose.nvidia.yaml"
cp "${ROOT_DIR}/scripts/install-offline.sh" "${OUTPUT_DIR}/install.sh"
cp "${ROOT_DIR}/scripts/install-offline.ps1" "${OUTPUT_DIR}/install.ps1"
cp "${ROOT_DIR}/LICENSE" "${OUTPUT_DIR}/LICENSE"
cp "${ROOT_DIR}/NOTICE" "${OUTPUT_DIR}/NOTICE"
cp "${ROOT_DIR}/THIRD_PARTY_NOTICES.md" "${OUTPUT_DIR}/THIRD_PARTY_NOTICES.md"
cp "${ROOT_DIR}/docs/OFFLINE_INSTALL.md" "${OUTPUT_DIR}/OFFLINE_INSTALL.md"
mkdir -p -- "${OUTPUT_DIR}/licenses"
cp "${ROOT_DIR}/LICENSE" "${OUTPUT_DIR}/licenses/OpenLocalAI-Apache-2.0.txt"
cp "${ROOT_DIR}/LICENSE" "${OUTPUT_DIR}/licenses/Qwen-Apache-2.0.txt"
cp "${ROOT_DIR}/licenses/Ollama-MIT.txt" "${OUTPUT_DIR}/licenses/Ollama-MIT.txt"
cp "${ROOT_DIR}/licenses/Python-2.0.txt" "${OUTPUT_DIR}/licenses/Python-2.0.txt"
cp "${ROOT_DIR}/licenses/PyInstaller-GPL-2.0-or-later-with-Bootloader-exception.txt" \
  "${OUTPUT_DIR}/licenses/PyInstaller-GPL-2.0-or-later-with-Bootloader-exception.txt"
{
  printf 'OPENLOCALAI_MODEL=%s\n' "${MODEL}"
  printf 'OPENLOCALAI_PORT=8765\n'
  printf 'OPENLOCALAI_BIND=127.0.0.1\n'
  printf 'OPENLOCALAI_IMAGE=%s\n' "${APP_IMAGE}"
  printf 'OLLAMA_IMAGE=%s\n' "${OLLAMA_IMAGE}"
  printf 'OLLAMA_CONTEXT_LENGTH=4096\n'
  printf 'OLLAMA_NUM_PARALLEL=1\n'
  printf 'OLLAMA_KV_CACHE_TYPE=q8_0\n'
} >"${OUTPUT_DIR}/.openlocalai.env"
chmod 755 "${OUTPUT_DIR}/install.sh"

printf '4/7 导出容器镜像。\n'
docker save "${APP_IMAGE}" "${OLLAMA_IMAGE}" "${ALPINE_IMAGE}" | gzip -1 >"${OUTPUT_DIR}/images.tar.gz"

printf '5/7 导出完整 Ollama 模型目录。\n'
docker run --rm \
  --mount "type=volume,source=${BUNDLE_MODEL_VOLUME},target=/source,readonly" \
  --mount "type=bind,source=${OUTPUT_DIR},target=/backup" \
  "${ALPINE_IMAGE}" sh -eu -c 'test -d /source/models && cd /source && tar czf /backup/ollama-models.tar.gz models'

platform_id="$(docker version --format '{{.Server.Os}}-{{.Server.Arch}}' 2>/dev/null || printf 'unknown')"
{
  printf 'OpenLocalAI offline bundle\n'
  printf 'platform=%s\n' "${platform_id}"
  printf 'model=%s\n' "${MODEL}"
  printf 'ollama_image=%s\n' "${OLLAMA_IMAGE}"
  printf 'created_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'The target Docker host must use the same CPU architecture.\n'
} >"${OUTPUT_DIR}/BUNDLE_INFO.txt"

printf '6/7 生成 SPDX 2.3 SBOM。\n'
python3 "${ROOT_DIR}/scripts/generate_sbom.py" \
  --project-root "${ROOT_DIR}" \
  --app-image "${APP_IMAGE}" \
  --ollama-image "${OLLAMA_IMAGE}" \
  --alpine-image "${ALPINE_IMAGE}" \
  --model "${MODEL}" \
  --images-archive "${OUTPUT_DIR}/images.tar.gz" \
  --models-archive "${OUTPUT_DIR}/ollama-models.tar.gz" \
  --output "${OUTPUT_DIR}/sbom.spdx.json"

printf '7/7 生成 SHA-256 校验并压缩安装包。\n'
if command -v sha256sum >/dev/null 2>&1; then
  (cd "${OUTPUT_DIR}" && sha256sum BUNDLE_INFO.txt LICENSE NOTICE THIRD_PARTY_NOTICES.md OFFLINE_INSTALL.md compose.yaml compose.nvidia.yaml install.sh install.ps1 .openlocalai.env licenses/OpenLocalAI-Apache-2.0.txt licenses/Qwen-Apache-2.0.txt licenses/Ollama-MIT.txt licenses/Python-2.0.txt licenses/PyInstaller-GPL-2.0-or-later-with-Bootloader-exception.txt sbom.spdx.json images.tar.gz ollama-models.tar.gz >SHA256SUMS)
else
  (cd "${OUTPUT_DIR}" && shasum -a 256 BUNDLE_INFO.txt LICENSE NOTICE THIRD_PARTY_NOTICES.md OFFLINE_INSTALL.md compose.yaml compose.nvidia.yaml install.sh install.ps1 .openlocalai.env licenses/OpenLocalAI-Apache-2.0.txt licenses/Qwen-Apache-2.0.txt licenses/Ollama-MIT.txt licenses/Python-2.0.txt licenses/PyInstaller-GPL-2.0-or-later-with-Bootloader-exception.txt sbom.spdx.json images.tar.gz ollama-models.tar.gz >SHA256SUMS)
fi
tar -C "$(dirname -- "${OUTPUT_DIR}")" -czf "${ARCHIVE_PATH}" "$(basename -- "${OUTPUT_DIR}")"

printf '离线包已生成：%s\n' "${ARCHIVE_PATH}"
printf '请把该文件和单独记录的 SHA-256 摘要一起交付到离线环境。\n'
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "${ARCHIVE_PATH}"
else
  shasum -a 256 "${ARCHIVE_PATH}"
fi
