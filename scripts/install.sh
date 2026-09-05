#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.openlocalai.env"
MODEL="${OPENLOCALAI_MODEL:-qwen3.5:4b-q4_K_M}"
PORT="${OPENLOCALAI_PORT:-8765}"
BIND="${OPENLOCALAI_BIND:-127.0.0.1}"
GPU="cpu"
OPEN_BROWSER=1
APP_IMAGE="${OPENLOCALAI_IMAGE:-openlocalai-workbench:local}"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-ollama/ollama:0.33.3}"

usage() {
  printf '%s\n' \
    '用法: ./scripts/install.sh [选项]' \
    '' \
    '  --model NAME       Ollama 模型，默认 qwen3.5:4b-q4_K_M' \
    '  --port PORT        浏览器访问端口，默认 8765' \
    '  --bind ADDRESS     监听地址，默认 127.0.0.1' \
    '  --gpu cpu|nvidia   推理设备，默认 cpu' \
    '  --no-open          启动后不自动打开浏览器' \
    '  -h, --help         显示帮助'
}

die() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --model)
      (($# >= 2)) || die '--model 缺少值'
      MODEL="$2"
      shift 2
      ;;
    --port)
      (($# >= 2)) || die '--port 缺少值'
      PORT="$2"
      shift 2
      ;;
    --bind)
      (($# >= 2)) || die '--bind 缺少值'
      BIND="$2"
      shift 2
      ;;
    --gpu)
      (($# >= 2)) || die '--gpu 缺少值'
      GPU="$2"
      shift 2
      ;;
    --no-open)
      OPEN_BROWSER=0
      shift
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

[[ "${PORT}" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) \
  || die '端口必须是 1 到 65535 之间的整数'
[[ "${BIND}" =~ ^[A-Za-z0-9.:-]+$ ]] || die '监听地址无效'
case "${MODEL}" in
  qwen3.5:4b-q4_K_M|qwen3.5:9b-q4_K_M|qwen3.5:27b-q4_K_M|qwen3:4b) ;;
  *) die '模型不在已审核的生成模型清单中' ;;
esac
[[ "${OLLAMA_IMAGE##*/}" == *':0.33.3' ]] || die 'OLLAMA_IMAGE 必须固定为 0.33.3 标签'
[[ "${GPU}" == 'cpu' || "${GPU}" == 'nvidia' ]] || die '--gpu 只支持 cpu 或 nvidia'

command -v docker >/dev/null 2>&1 \
  || die '未找到 Docker。请先安装并启动 Docker Desktop 或 Docker Engine。'
docker info >/dev/null 2>&1 \
  || die 'Docker 服务未运行，或当前用户无权访问 Docker。'
docker compose version >/dev/null 2>&1 \
  || die '缺少 Docker Compose v2。请安装 compose 插件后重试。'

if [[ "${GPU}" == 'nvidia' ]] && ! command -v nvidia-smi >/dev/null 2>&1; then
  die '未检测到 nvidia-smi。请先安装 NVIDIA 驱动和 NVIDIA Container Toolkit，或改用 --gpu cpu。'
fi

umask 077
{
  printf 'OPENLOCALAI_MODEL=%s\n' "${MODEL}"
  printf 'OPENLOCALAI_PORT=%s\n' "${PORT}"
  printf 'OPENLOCALAI_BIND=%s\n' "${BIND}"
  printf 'OPENLOCALAI_IMAGE=%s\n' "${APP_IMAGE}"
  printf 'OLLAMA_IMAGE=%s\n' "${OLLAMA_IMAGE}"
  printf 'OLLAMA_CONTEXT_LENGTH=%s\n' "${OLLAMA_CONTEXT_LENGTH:-4096}"
  printf 'OLLAMA_NUM_PARALLEL=%s\n' "${OLLAMA_NUM_PARALLEL:-1}"
  printf 'OLLAMA_KV_CACHE_TYPE=%s\n' "${OLLAMA_KV_CACHE_TYPE:-q8_0}"
} >"${ENV_FILE}"

compose=(docker compose --env-file "${ENV_FILE}" -f "${ROOT_DIR}/compose.yaml")
if [[ "${GPU}" == 'nvidia' ]]; then
  compose+=(-f "${ROOT_DIR}/compose.nvidia.yaml")
fi

printf '正在构建工作台并安装模型 %s。默认模型下载约 3.4 GB。\n' "${MODEL}"
if ! "${compose[@]}" pull ollama model-init \
  || ! "${compose[@]}" build app \
  || ! "${compose[@]}" up -d ollama; then
  "${compose[@]}" logs --tail=100 app ollama model-init >&2 || true
  die '镜像准备或 Ollama 启动失败；上方是最近日志。'
fi

printf '正在下载并校验本地模型；进度由 Ollama 实时显示。\n'
if ! "${compose[@]}" run --rm model-init; then
  "${compose[@]}" logs --tail=100 ollama >&2 || true
  die '模型下载失败。可直接重新运行安装脚本继续下载。'
fi
if ! "${compose[@]}" up -d --no-deps app; then
  "${compose[@]}" logs --tail=100 app ollama >&2 || true
  die '应用容器启动失败；上方是最近日志。'
fi

printf '正在等待服务就绪'
deadline=$((SECONDS + 900))
while ((SECONDS < deadline)); do
  app_id="$("${compose[@]}" ps -q app 2>/dev/null || true)"
  if [[ -n "${app_id}" ]]; then
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${app_id}" 2>/dev/null || true)"
    if [[ "${health}" == 'healthy' ]]; then
      printf '\n安装完成。\n'
      break
    fi
    if [[ "${health}" == 'unhealthy' || "${health}" == 'exited' || "${health}" == 'dead' ]]; then
      printf '\n' >&2
      "${compose[@]}" logs --tail=100 app ollama model-init >&2 || true
      die "应用状态异常：${health}"
    fi
  fi
  printf '.'
  sleep 3
done

app_id="$("${compose[@]}" ps -q app 2>/dev/null || true)"
health='missing'
if [[ -n "${app_id}" ]]; then
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${app_id}" 2>/dev/null || true)"
fi
if [[ "${health}" != 'healthy' ]]; then
  printf '\n' >&2
  "${compose[@]}" logs --tail=100 app ollama model-init >&2 || true
  die '等待服务健康状态超时。模型下载较慢时可稍后重新运行安装脚本。'
fi

ACCESS_HOST="${BIND}"
if [[ "${ACCESS_HOST}" == '0.0.0.0' || "${ACCESS_HOST}" == '::' ]]; then
  ACCESS_HOST='127.0.0.1'
fi
URL="http://${ACCESS_HOST}:${PORT}"
printf '访问地址：%s\n' "${URL}"
printf '诊断命令：python3 "%s/scripts/doctor.py"\n' "${ROOT_DIR}"

if ((OPEN_BROWSER)); then
  if command -v open >/dev/null 2>&1; then
    open "${URL}" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${URL}" >/dev/null 2>&1 || true
  fi
fi
