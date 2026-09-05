[CmdletBinding()]
param(
    [string]$Model = $(if ($env:OPENLOCALAI_MODEL) { $env:OPENLOCALAI_MODEL } else { "qwen3.5:4b-q4_K_M" }),
    [ValidateRange(1, 65535)]
    [int]$Port = $(if ($env:OPENLOCALAI_PORT) { [int]$env:OPENLOCALAI_PORT } else { 8765 }),
    [string]$Bind = $(if ($env:OPENLOCALAI_BIND) { $env:OPENLOCALAI_BIND } else { "127.0.0.1" }),
    [ValidateSet("cpu", "nvidia")]
    [string]$Gpu = "cpu",
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RootDir ".openlocalai.env"
$AppImage = if ($env:OPENLOCALAI_IMAGE) { $env:OPENLOCALAI_IMAGE } else { "openlocalai-workbench:local" }
$OllamaImage = if ($env:OLLAMA_IMAGE) { $env:OLLAMA_IMAGE } else { "ollama/ollama:0.33.3" }

$AllowedModels = @(
    "qwen3.5:4b-q4_K_M",
    "qwen3.5:9b-q4_K_M",
    "qwen3.5:27b-q4_K_M",
    "qwen3:4b"
)
if ([string]::IsNullOrWhiteSpace($Model) -or $Model -notin $AllowedModels) {
    throw "模型不在已审核的生成模型清单中。"
}
if ($OllamaImage -notmatch ':0\.33\.3$') {
    throw "OLLAMA_IMAGE 必须固定为 0.33.3 标签。"
}
if ([string]::IsNullOrWhiteSpace($Bind) -or $Bind -notmatch '^[A-Za-z0-9.:-]+$') {
    throw "监听地址无效。"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 Docker。请先安装并启动 Docker Desktop。"
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker 服务未运行，或当前用户无权访问 Docker。"
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "缺少 Docker Compose v2。请更新 Docker Desktop 后重试。"
}
if ($Gpu -eq "nvidia" -and -not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    throw "未检测到 nvidia-smi。请安装 NVIDIA 驱动并启用 Docker Desktop GPU 支持，或使用 -Gpu cpu。"
}

$EnvText = @(
    "OPENLOCALAI_MODEL=$Model"
    "OPENLOCALAI_PORT=$Port"
    "OPENLOCALAI_BIND=$Bind"
    "OPENLOCALAI_IMAGE=$AppImage"
    "OLLAMA_IMAGE=$OllamaImage"
    "OLLAMA_CONTEXT_LENGTH=4096"
    "OLLAMA_NUM_PARALLEL=1"
    "OLLAMA_KV_CACHE_TYPE=q8_0"
) -join "`n"
[System.IO.File]::WriteAllText($EnvFile, $EnvText + "`n", [System.Text.UTF8Encoding]::new($false))

$ComposeArgs = @("compose", "--env-file", $EnvFile, "-f", (Join-Path $RootDir "compose.yaml"))
if ($Gpu -eq "nvidia") {
    $ComposeArgs += @("-f", (Join-Path $RootDir "compose.nvidia.yaml"))
}

Write-Host "正在构建工作台并安装模型 $Model。默认模型下载约 3.4 GB。"
& docker @ComposeArgs pull ollama model-init
if ($LASTEXITCODE -ne 0) {
    throw "Ollama 镜像下载失败。"
}
& docker @ComposeArgs build app
if ($LASTEXITCODE -ne 0) {
    throw "应用镜像构建失败。"
}
& docker @ComposeArgs up -d ollama
if ($LASTEXITCODE -ne 0) {
    & docker @ComposeArgs logs --tail=100 ollama
    throw "Ollama 启动失败。"
}

Write-Host "正在下载并校验本地模型；进度由 Ollama 实时显示。"
& docker @ComposeArgs run --rm model-init
if ($LASTEXITCODE -ne 0) {
    & docker @ComposeArgs logs --tail=100 ollama
    throw "模型下载失败。可直接重新运行安装脚本继续下载。"
}
& docker @ComposeArgs up -d --no-deps app
if ($LASTEXITCODE -ne 0) {
    & docker @ComposeArgs logs --tail=100 app ollama
    throw "应用容器启动失败。"
}

Write-Host -NoNewline "正在等待服务就绪"
$Deadline = (Get-Date).AddMinutes(15)
$Healthy = $false
while ((Get-Date) -lt $Deadline) {
    $AppId = (& docker @ComposeArgs ps -q app 2>$null | Select-Object -First 1)
    if ($AppId) {
        $Health = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $AppId 2>$null).Trim()
        if ($Health -eq "healthy") {
            $Healthy = $true
            break
        }
        if ($Health -in @("unhealthy", "exited", "dead")) {
            Write-Host
            & docker @ComposeArgs logs --tail=100 app ollama model-init
            throw "应用状态异常：$Health"
        }
    }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 3
}
Write-Host

if (-not $Healthy) {
    & docker @ComposeArgs logs --tail=100 app ollama model-init
    throw "等待服务健康状态超时。模型下载较慢时可稍后重新运行安装脚本。"
}

$AccessHost = $Bind
if ($AccessHost -in @("0.0.0.0", "::")) {
    $AccessHost = "127.0.0.1"
}
$Url = "http://${AccessHost}:$Port"
Write-Host "安装完成。"
Write-Host "访问地址：$Url"
Write-Host "诊断命令：python `"$(Join-Path $RootDir 'scripts/doctor.py')`""
if (-not $NoOpen) {
    Start-Process $Url
}
