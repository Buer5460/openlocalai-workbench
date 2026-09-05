[CmdletBinding()]
param(
    [ValidateSet("cpu", "nvidia")]
    [string]$Gpu = "cpu",
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$RootDir = $PSScriptRoot
$RequiredFiles = @(
    "SHA256SUMS",
    "BUNDLE_INFO.txt",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "OFFLINE_INSTALL.md",
    "compose.nvidia.yaml",
    "install.sh",
    "install.ps1",
    "licenses/OpenLocalAI-Apache-2.0.txt",
    "licenses/Qwen-Apache-2.0.txt",
    "licenses/Ollama-MIT.txt",
    "licenses/Python-2.0.txt",
    "licenses/PyInstaller-GPL-2.0-or-later-with-Bootloader-exception.txt",
    "sbom.spdx.json",
    "images.tar.gz",
    "ollama-models.tar.gz",
    "compose.yaml",
    ".openlocalai.env"
)
foreach ($File in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $RootDir $File) -PathType Leaf)) {
        throw "离线包缺少 $File。"
    }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 Docker。离线目标机必须预先安装 Docker Desktop 和 Compose v2。"
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker 服务未运行。"
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "缺少 Docker Compose v2。"
}

Write-Host "1/5 校验离线包。"
$ChecksumLines = Get-Content -LiteralPath (Join-Path $RootDir "SHA256SUMS") -Encoding UTF8
$CoveredFiles = @()
foreach ($Line in $ChecksumLines) {
    if ($Line -notmatch '^([0-9a-fA-F]{64})\s+\*?(.+)$') {
        throw "SHA256SUMS 存在无法识别的行：$Line"
    }
    $Expected = $Matches[1].ToLowerInvariant()
    $RelativePath = $Matches[2]
    $CoveredFiles += $RelativePath
    $Target = Join-Path $RootDir $RelativePath
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) {
        throw "校验文件缺少：$RelativePath"
    }
    $Actual = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "SHA-256 校验失败：$RelativePath"
    }
    Write-Host "  $RelativePath OK"
}
foreach ($File in $RequiredFiles) {
    if ($File -ne "SHA256SUMS" -and -not ($CoveredFiles -ccontains $File)) {
        throw "SHA256SUMS 未覆盖 $File。"
    }
}

$PlatformLine = Get-Content -LiteralPath (Join-Path $RootDir "BUNDLE_INFO.txt") -Encoding UTF8 |
    Where-Object { $_ -match '^platform=' } |
    Select-Object -First 1
if (-not $PlatformLine) {
    throw "BUNDLE_INFO.txt 缺少 platform。"
}
$BundlePlatform = $PlatformLine.Substring("platform=".Length).Trim()
if ($BundlePlatform -notmatch '^[A-Za-z0-9._-]+-[A-Za-z0-9._-]+$') {
    throw "BUNDLE_INFO.txt 包含无效 platform。"
}
$TargetPlatformOutput = & docker version --format '{{.Server.Os}}-{{.Server.Arch}}'
if ($LASTEXITCODE -ne 0 -or -not $TargetPlatformOutput) {
    throw "无法读取目标 Docker Server 平台。"
}
$TargetPlatform = ($TargetPlatformOutput | Select-Object -First 1).Trim()
if ([string]::IsNullOrWhiteSpace($TargetPlatform)) {
    throw "无法读取目标 Docker Server 平台。"
}
if ($BundlePlatform -ne $TargetPlatform) {
    throw "离线包平台 $BundlePlatform 与目标 Docker 平台 $TargetPlatform 不匹配。"
}
Write-Host "平台匹配：$TargetPlatform"

Write-Host "2/5 导入容器镜像。"
$ImagesArchive = Join-Path $RootDir "images.tar.gz"
& docker load --input $ImagesArchive
if ($LASTEXITCODE -ne 0) {
    throw "容器镜像导入失败。"
}

Write-Host "3/5 恢复完整模型目录。"
& docker volume create openlocalai-ollama-models *> $null
if ($LASTEXITCODE -ne 0) { throw "无法创建模型数据卷。" }
& docker volume create openlocalai-app-data *> $null
if ($LASTEXITCODE -ne 0) { throw "无法创建应用数据卷。" }
& docker run --rm `
    --mount "type=volume,source=openlocalai-ollama-models,target=/target" `
    --mount "type=bind,source=$RootDir,target=/backup,readonly" `
    alpine:3.21.3 sh -eu -c @'
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
'@
if ($LASTEXITCODE -ne 0) {
    throw "模型目录恢复失败。"
}

$ComposeArgs = @(
    "compose",
    "--env-file", (Join-Path $RootDir ".openlocalai.env"),
    "-f", (Join-Path $RootDir "compose.yaml")
)
if ($Gpu -eq "nvidia") {
    $GpuFile = Join-Path $RootDir "compose.nvidia.yaml"
    if (-not (Test-Path -LiteralPath $GpuFile -PathType Leaf)) {
        throw "离线包缺少 compose.nvidia.yaml。"
    }
    $ComposeArgs += @("-f", $GpuFile)
}

Write-Host "4/5 启动服务；该步骤不访问镜像仓库。"
& docker @ComposeArgs up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) {
    & docker @ComposeArgs logs --tail=100 app ollama model-init
    throw "离线容器启动失败。"
}

Write-Host -NoNewline "5/5 等待健康检查"
$Deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $Deadline) {
    $Status = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' openlocalai-app 2>$null)
    if ($Status) { $Status = $Status.Trim() }
    if ($Status -eq "healthy") {
        Write-Host
        Write-Host "离线安装完成：http://127.0.0.1:8765"
        if (-not $NoOpen) {
            Start-Process "http://127.0.0.1:8765"
        }
        exit 0
    }
    if ($Status -in @("unhealthy", "exited", "dead")) {
        Write-Host
        & docker @ComposeArgs logs --tail=100 app ollama model-init
        throw "应用状态异常：$Status"
    }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 3
}

Write-Host
& docker @ComposeArgs logs --tail=100 app ollama model-init
throw "等待服务健康状态超时。"
