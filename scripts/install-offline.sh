#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
GPU='cpu'
OPEN_BROWSER=1

usage() {
  printf '%s\n' \
    '用法: ./install.sh [--gpu cpu|nvidia] [--no-open]' \
    '' \
    '此脚本只能在 build-offline-bundle.sh 生成的完整离线包内运行。'
}

die() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
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

[[ "${GPU}" == 'cpu' || "${GPU}" == 'nvidia' ]] || die '--gpu 只支持 cpu 或 nvidia'
bundle_files=(
  BUNDLE_INFO.txt
  LICENSE
  NOTICE
  THIRD_PARTY_NOTICES.md
  OFFLINE_INSTALL.md
  compose.yaml
  compose.nvidia.yaml
  install.sh
  install.ps1
  .openlocalai.env
  licenses/OpenLocalAI-Apache-2.0.txt
  licenses/Qwen-Apache-2.0.txt
  licenses/Ollama-MIT.txt
  licenses/Python-2.0.txt
  licenses/PyInstaller-GPL-2.0-or-later-with-Bootloader-exception.txt
  sbom.spdx.json
  images.tar.gz
  ollama-models.tar.gz
)
for file in SHA256SUMS "${bundle_files[@]}"; do
  [[ -f "${ROOT_DIR}/${file}" ]] || die "离线包缺少 ${file}"
done
command -v docker >/dev/null 2>&1 || die '未找到 Docker。离线目标机必须预先安装 Docker Engine/Desktop 和 Compose v2。'
docker info >/dev/null 2>&1 || die 'Docker 服务未运行。'
docker compose version >/dev/null 2>&1 || die '缺少 Docker Compose v2。'

printf '1/5 校验离线包。\n'
for file in "${bundle_files[@]}"; do
  awk -v expected="${file}" '
    $2 == expected || $2 == "*" expected { found = 1 }
    END { exit(found ? 0 : 1) }
  ' "${ROOT_DIR}/SHA256SUMS" || die "SHA256SUMS 未覆盖 ${file}"
done
if command -v sha256sum >/dev/null 2>&1; then
  (cd "${ROOT_DIR}" && sha256sum -c SHA256SUMS)
elif command -v shasum >/dev/null 2>&1; then
  (cd "${ROOT_DIR}" && shasum -a 256 -c SHA256SUMS)
else
  die '系统缺少 sha256sum 或 shasum，无法验证离线包完整性。'
fi

bundle_platform=''
while IFS='=' read -r key value; do
  if [[ "${key}" == 'platform' ]]; then
    bundle_platform="${value}"
    break
  fi
done <"${ROOT_DIR}/BUNDLE_INFO.txt"
[[ "${bundle_platform}" =~ ^[A-Za-z0-9._-]+-[A-Za-z0-9._-]+$ ]] \
  || die 'BUNDLE_INFO.txt 缺少有效 platform'
target_platform="$(docker version --format '{{.Server.Os}}-{{.Server.Arch}}')" \
  || die '无法读取目标 Docker Server 平台'
[[ "${target_platform}" == "${bundle_platform}" ]] \
  || die "离线包平台 ${bundle_platform} 与目标 Docker 平台 ${target_platform} 不匹配"
printf '平台匹配：%s\n' "${target_platform}"

printf '2/5 导入容器镜像。\n'
docker load <"${ROOT_DIR}/images.tar.gz"

printf '3/5 恢复完整模型目录。\n'
docker volume create openlocalai-ollama-models >/dev/null
docker volume create openlocalai-app-data >/dev/null
docker run --rm \
  --mount type=volume,source=openlocalai-ollama-models,target=/target \
  --mount "type=bind,source=${ROOT_DIR},target=/backup,readonly" \
  alpine:3.21.3 sh -eu -c '
    entries="$(tar tzf /backup/ollama-models.tar.gz)"
    test -n "$entries"
    printf "%s\n" "$entries" | while IFS= read -r entry; do
      case "$entry" in
        models|models/) ;;
        models/*)
          case "/$entry/" in *"/../"*|*"/./"*) exit 1 ;; esac
          case "$entry" in *id_ed25519*) exit 1 ;; esac
          ;;
        *) exit 1 ;;
      esac
    done
    cd /target
    tar xzf /backup/ollama-models.tar.gz
  '

compose=(docker compose --env-file "${ROOT_DIR}/.openlocalai.env" -f "${ROOT_DIR}/compose.yaml")
if [[ "${GPU}" == 'nvidia' ]]; then
  [[ -f "${ROOT_DIR}/compose.nvidia.yaml" ]] || die '离线包缺少 compose.nvidia.yaml'
  compose+=(-f "${ROOT_DIR}/compose.nvidia.yaml")
fi

printf '4/5 启动服务；该步骤不访问镜像仓库。\n'
"${compose[@]}" up -d --no-build --pull never

printf '5/5 等待健康检查'
deadline=$((SECONDS + 300))
while ((SECONDS < deadline)); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' openlocalai-app 2>/dev/null || true)"
  if [[ "${status}" == 'healthy' ]]; then
    printf '\n离线安装完成：http://127.0.0.1:8765\n'
    if ((OPEN_BROWSER)); then
      if command -v open >/dev/null 2>&1; then
        open 'http://127.0.0.1:8765' >/dev/null 2>&1 || true
      elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open 'http://127.0.0.1:8765' >/dev/null 2>&1 || true
      fi
    fi
    exit 0
  fi
  if [[ "${status}" == 'unhealthy' || "${status}" == 'exited' || "${status}" == 'dead' ]]; then
    printf '\n' >&2
    "${compose[@]}" logs --tail=100 app ollama model-init >&2 || true
    die "应用状态异常：${status}"
  fi
  printf '.'
  sleep 3
done
printf '\n' >&2
"${compose[@]}" logs --tail=100 app ollama model-init >&2 || true
die '等待服务健康状态超时。'
