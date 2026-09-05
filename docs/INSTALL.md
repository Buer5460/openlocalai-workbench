# 安装与本地模型部署

推荐使用 Docker 方式。应用、Ollama 模型服务和模型权重在同一台机器运行；默认不调用云端接口，Ollama 也通过 `OLLAMA_NO_CLOUD=1` 禁用云模型能力。

## 系统要求

- Windows 10/11、主流 Linux x86_64/ARM64、macOS 12+。
- Docker Engine 24+ 与 Docker Compose v2.20+；Windows/macOS 推荐 Docker Desktop。
- 默认模型至少需要 8 GiB 物理内存、6 GiB Docker 可用内存和 12 GiB 可用磁盘。
- 首次在线安装需要访问代码仓库、容器镜像仓库和 Ollama 模型仓库；安装完成后可断网运行。

默认模型是 `qwen3.5:4b-q4_K_M`。Ollama 官方模型页标明该量化权重约 3.4 GB、采用 Q4_K_M，并使用 Apache-2.0 许可证：<https://ollama.com/library/qwen3.5:4b-q4_K_M>。

## Linux 与 macOS

在项目根目录运行：

```bash
chmod +x scripts/install.sh
./scripts/install.sh
```

脚本会检查 Docker、构建应用镜像、启动 Ollama、下载模型、等待健康检查，并在支持的桌面环境打开浏览器。访问地址为 <http://127.0.0.1:8765>。

## Windows

安装并启动 Docker Desktop，在 PowerShell 中进入项目目录后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

该命令执行与 Linux/macOS 相同的安装流程。脚本不会修改系统执行策略，`-ExecutionPolicy Bypass` 只对本次 PowerShell 进程生效。

## 选择模型

| 典型物理内存 | 模型 | 权重下载量 | 安装命令 |
| --- | --- | ---: | --- |
| 8 GiB | `qwen3.5:4b-q4_K_M` | 约 3.4 GB | 默认，无需参数 |
| 16 GiB 及以上 | `qwen3.5:9b-q4_K_M` | 约 6.6 GB | `--model qwen3.5:9b-q4_K_M` |
| 32 GiB 及以上 | `qwen3.5:27b-q4_K_M` | 约 17 GB | `--model qwen3.5:27b-q4_K_M` |

Linux/macOS 示例：

```bash
./scripts/install.sh --model qwen3.5:9b-q4_K_M
```

Windows 示例：

```powershell
.\scripts\install.ps1 -Model "qwen3.5:9b-q4_K_M"
```

4B 是面向普及部署的默认档，不代表所有 8 GiB 设备都能流畅运行。集成显卡共享内存、Docker Desktop 内存上限、同时运行的软件都会影响是否可用。安装前后可运行：

```bash
python3 scripts/doctor.py
```

Windows 使用 `python scripts/doctor.py`。

## NVIDIA GPU

Linux 或支持 WSL2 GPU 的 Windows 主机，在已经安装 NVIDIA 驱动及 NVIDIA Container Toolkit 后可启用：

```bash
./scripts/install.sh --gpu nvidia
```

```powershell
.\scripts\install.ps1 -Gpu nvidia
```

macOS 的 Docker 容器不能直接使用 Metal，因此此 Docker 方案在 macOS 上按 CPU 推理。Apple Silicon 高性能原生 Ollama 接入属于另一种运行拓扑，不能把它描述为当前 Docker 方案已支持的 GPU 能力。

## 配置与日常操作

安装脚本把非敏感配置写入项目根目录的 `.openlocalai.env`。默认只绑定本机回环地址。

指定端口：

```bash
./scripts/install.sh --port 8876
```

仅在完成 TLS 终止、网络访问控制和组织安全验收之后，才应允许局域网访问：

```bash
./scripts/install.sh --bind 0.0.0.0
```

当前版本提供本机管理员登录，但尚不具备生产所需的多用户分权、统一身份、传输加密和密码策略，不得直接暴露到办公网、互联网或不可信网络。

查看状态和日志：

```bash
docker compose --env-file .openlocalai.env ps
docker compose --env-file .openlocalai.env logs --tail=200 app ollama
```

停止与重新启动：

```bash
docker compose --env-file .openlocalai.env stop
docker compose --env-file .openlocalai.env up -d
```

应用数据库存放在 Docker 卷 `openlocalai-app-data`，模型存放在 `openlocalai-ollama-models`。普通的 `docker compose down` 不会删除它们。不要运行 `docker compose down -v`，除非明确要永久删除所有应用数据和模型。

## 镜像仓库不可达

可以在运行安装脚本前通过 `OLLAMA_IMAGE` 环境变量指定组织内部的 Ollama 镜像地址；应用镜像由安装脚本在本机从当前源代码构建。组织若另行发布经过签名的应用镜像，可通过 `OPENLOCALAI_IMAGE` 指定镜像名，并由管理员使用 Compose 的 `--no-build` 模式启动。内部镜像应经过漏洞扫描、许可证复核和摘要锁定。公开配置固定 Ollama 版本为 `0.33.3`，没有使用 `latest`。

完全无外网环境请使用[离线安装包](OFFLINE_INSTALL.md)。
