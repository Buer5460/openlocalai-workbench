# 完全离线安装

离线部署分为两台机器：联网制包机负责下载和封装；离线目标机只负责校验、导入和启动。两台机器的 Docker CPU 架构必须一致，例如都为 `linux/amd64` 或都为 `linux/arm64`。

## 为什么封装完整 `models/` 子目录

Ollama 当前没有适合本项目交付流程的官方通用 `export/import` 命令。因此制包脚本不会伪造不存在的能力，而是：

1. 通过 Ollama 正常拉取指定模型。
2. 校验 `ollama show` 能读取该模型。
3. 在独立的临时容器和空白临时卷中，只打包 `/root/.ollama/models/` 模型子目录。
4. 同时导出固定版本的应用、Ollama 和恢复工具镜像。
5. 生成 SPDX 2.3 JSON SBOM，记录应用镜像、固定的 Ollama 0.33.3、`alpine:3.21.3` 恢复工具镜像、所选 Qwen 模型及两个大归档文件的实际 SHA-256。
6. 携带 `THIRD_PARTY_NOTICES.md` 和 `licenses/` 中的许可证副本，并为包内每个文件生成 SHA-256 校验值。

制包流程不会复用 `openlocalai-ollama-models` 或机器上已有的 Ollama 目录，因此不会把制包机上的其他模型意外带入离线包。归档明确排除 `.ollama` 根目录中的身份密钥、客户端配置及其他非模型文件。完成或中断时，脚本只清理名称带本次任务随机标识的临时容器、网络和卷。

制包器只接受已完成许可证映射的生成模型：`qwen3.5:4b-q4_K_M`、`qwen3.5:9b-q4_K_M`、`qwen3.5:27b-q4_K_M` 和兼容模型 `qwen3:4b`。任意模型名、无固定标签的模型和向量模型都会在下载前被拒绝，不会被套用错误的许可证结论。

另一种可审计做法是交付 GGUF 与 Modelfile 后在目标机执行 `ollama create`，但它需要额外维护模板、参数和各文件摘要。本版本采用完整 `models/` 子目录方案，减少现场差异，同时避免交付模型之外的 Ollama 本机状态。

## 1. 在联网制包机生成离线包

制包机需要 Docker Engine/Desktop、Compose v2，以及足够容纳镜像、模型目录和压缩包的磁盘。默认至少预留 20 GiB。

```bash
chmod +x scripts/build-offline-bundle.sh
./scripts/build-offline-bundle.sh
```

指定 16 GiB 档模型和输出目录：

```bash
./scripts/build-offline-bundle.sh \
  --model qwen3.5:9b-q4_K_M \
  --output ./offline-bundles/openlocalai-amd64-9b
```

脚本会生成目录及同名 `.tar.gz` 文件，并在终端打印外层压缩包的 SHA-256。把压缩包和该摘要通过两个独立渠道交付，避免压缩包与校验值同时被替换。

制包脚本是 Linux/macOS Bash 脚本。Windows 制包机可在 WSL2 中执行；离线 Windows 目标机不需要 WSL，可使用包内 PowerShell 安装器。

## 2. 传输与外层校验

在离线目标机先校验收到的 `.tar.gz`。Linux：

```bash
sha256sum openlocalai-offline-*.tar.gz
```

macOS：

```bash
shasum -a 256 openlocalai-offline-*.tar.gz
```

Windows PowerShell：

```powershell
Get-FileHash .\openlocalai-offline-*.tar.gz -Algorithm SHA256
```

结果必须与制包机单独记录的摘要完全一致。

## 3. 在离线目标机安装

目标机仍需预先安装 Docker Engine/Desktop 与 Compose v2。Docker 属于独立第三方产品，涉及操作系统驱动、管理员权限和各自许可，本项目离线包不会静默安装或绕过其许可。

Linux/macOS：

```bash
tar -xzf openlocalai-offline-*.tar.gz
cd openlocalai-offline-*
./install.sh
```

Windows PowerShell：

```powershell
tar -xzf .\openlocalai-offline-*.tar.gz
Set-Location .\openlocalai-offline-*
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装器会再次验证包内 `SHA256SUMS`，读取 `BUNDLE_INFO.txt`，并将制包平台与目标 Docker Server 的操作系统和 CPU 架构做精确比较。只有平台匹配后才会执行 `docker load`、创建或写入模型卷，并使用 `--pull never` 启动服务。只要包完整，该流程不需要访问镜像或模型仓库。

NVIDIA 主机可在驱动和容器工具包已经离线安装完成后添加 GPU 参数：

```bash
./install.sh --gpu nvidia
```

```powershell
.\install.ps1 -Gpu nvidia
```

## 4. 断网验收

保持目标机与外网断开，执行：

```bash
docker exec openlocalai-ollama ollama list
curl --fail http://127.0.0.1:8765/api/health
```

然后在浏览器打开 <http://127.0.0.1:8765>，完成材料导入、带出处问答、报告生成和审批流程。容器状态可检查：

```bash
docker compose --env-file .openlocalai.env ps
```

## 更新规则

- 更换模型、CPU 架构或 Ollama 版本时，必须重新制作离线包。
- 每次交付都应保存 `BUNDLE_INFO.txt`、内外两层 SHA-256、软件物料清单和验收记录。
- `sbom.spdx.json` 是交付层清单，不能替代对最终容器内所有操作系统包进行的深度漏洞与许可证扫描。
- 不要从另一套正在生产运行的 Ollama 目录直接制包；制包机应使用已审核的独立环境。
- 恢复脚本采用合并写入，不主动删除现有卷。若目标机已有同名模型卷，应先由管理员完成备份和变更审批。
- 模型权重和 Ollama 均有各自许可证。项目 Apache-2.0 许可证不会自动替代第三方许可证义务。
