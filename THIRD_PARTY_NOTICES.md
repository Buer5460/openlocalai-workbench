# 第三方组件与模型

OpenLocalAI 源码应用没有 Python 运行时第三方包依赖。桌面可执行文件和容器化部署会嵌入或使用下列第三方内容；OpenLocalAI 的 Apache-2.0 许可证不替代这些组件各自的许可证。

## 桌面可执行文件

发行工作流使用 PyInstaller 6.22.2 构建桌面可执行文件，并在产物中嵌入 CPython 运行时以及 PyInstaller bootloader 和相关加载文件。工作流只固定 Python 3.12 大版本，补丁版本为 `NOASSERTION`；每个产物使用的准确 Python 版本记录在随包 SPDX SBOM 中。

| 组件 | 版本 | 许可证 | 交付关系 | 许可证副本 |
|---|---|---|---|---|
| CPython embedded runtime | 3.12；补丁版本 `NOASSERTION` | Python-2.0 | 嵌入桌面可执行文件 | `licenses/Python-2.0.txt` |
| PyInstaller bootloader | 6.22.2 | GPL-2.0-or-later WITH Bootloader-exception | bootloader 及相关加载文件嵌入桌面可执行文件 | `licenses/PyInstaller-GPL-2.0-or-later-with-Bootloader-exception.txt` |

`licenses/Python-2.0.txt` 是 CPython 3.12 上游 `LICENSE` 的原文副本。PyInstaller 许可证文件是 v6.22.2 上游 `COPYING.txt` 的完整副本，其中还说明了运行时 hooks 的 Apache-2.0 许可及 `PyInstaller.isolated` 的附加 MIT 许可。

## 容器运行时与模型

本地模型部署是可选能力，相关组件不因安装 OpenLocalAI 而自动改变许可证：

| 组件 | 用途 | 许可证 | 交付关系 |
|---|---|---|---|
| Ollama 0.33.3 | 默认本地模型运行时 | MIT | 容器镜像；离线包可包含 |
| Qwen3.5 模型权重 | 中文生成模型 | Apache-2.0，以具体模型仓库为准 | 按用户选择下载；离线包可包含 |
| Qwen3 Embedding | 可选向量模型 | Apache-2.0，以具体模型仓库为准 | 按用户选择下载 |
| Alpine Linux `alpine:3.21.3` | 离线安装时恢复模型卷 | `NOASSERTION`；聚合镜像内的软件包适用各自许可证 | 离线镜像包包含；供应方为 Alpine Linux Development Team |

Alpine 容器镜像是由多个软件包组成的聚合交付件，因此本项目不对整个镜像作单一许可证断言。官方 `docker-alpine` 构建仓库的 MIT 许可证只覆盖该仓库内容，不能当作镜像内全部软件包的统一许可证；发行审计应结合镜像 SBOM 和 Alpine 软件包元数据逐项判断。

制作在线或离线发行包时，维护者必须锁定组件版本、保留各自许可证、生成哈希，并复核模型卡及适用限制。平台允许接入其他运行时和模型，不代表项目为其安全性、许可或合规性背书。
