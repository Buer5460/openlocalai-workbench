# OpenLocalAI Workbench / 开源离线 AI 工作台

面向政府、国有企业、高校及其他重视数据边界组织的开放式离线 AI 工作平台。它把材料导入、证据检索、本地模型问答、报告生成、人工审批和审计留痕组成一条可复核工作链。

> 项目不是任何政府机构的官方产品或认证产品。v0.2.0 是可下载安装、可部署本地模型的公开 Alpha；生产使用仍需由部署单位完成权限、密码、备份、等保/密评和数据合规验收。

## 项目定位

这是可运行、可审计、可二次开发的参考实现，不是一台固定配置的“AI 工作站”，也不绑定开发者的电脑。使用单位根据并发、材料规模、模型参数量和信创目录自行选择服务器、CPU/GPU、操作系统与存储。

当前提供 Ollama + Qwen 作为最低门槛的默认参考栈，目的是让社区能先下载、运行和验证完整流程；它不是唯一技术路线。身份、证据、审批、审计和模型适配层彼此解耦，可继续接入国产 CPU/GPU 环境及 MindIE、vLLM、SGLang、LMDeploy 等本地运行时。项目希望为公共部门和高校提供一个透明起点，而不是替代各单位的安全评估与采购验收。

## 直接部署应用与本地模型

已安装并启动 Docker Desktop/Engine 的 Windows、Linux 或 macOS 用户，下载源码后执行一次安装脚本即可启动应用、Ollama 和固定版本的 Qwen 模型。

Linux / macOS：

~~~bash
git clone https://github.com/Buer5460/openlocalai-workbench.git
cd openlocalai-workbench
./scripts/install.sh
~~~

Windows PowerShell：

~~~powershell
git clone https://github.com/Buer5460/openlocalai-workbench.git
Set-Location openlocalai-workbench
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
~~~

安装完成后访问 <http://127.0.0.1:8765>，首次打开创建本机管理员。默认下载约 3.4GB 的 qwen3.5:4b-q4_K_M；16GB 及以上机器可选择 9B 档。完整要求和 NVIDIA 配置见 [安装指南](docs/INSTALL.md)。

完全无外网环境可在联网制包机生成包含容器镜像、模型目录、许可证和 SHA-256 的离线介质包，再在目标机恢复，见 [离线安装](docs/OFFLINE_INSTALL.md)。

## 下载安装包

版本标签会自动构建 Windows x86-64、Linux x86-64、macOS Apple Silicon 和 macOS Intel 的独立程序，并附许可证、安装脚本、部署配置和 SPDX SBOM。双击/运行 OpenLocalAI 可启动工作台；若需要模型推理，应安装 Ollama，或使用上述 Docker 完整安装路径。

发行和国内镜像规则见 [发行指南](docs/DISTRIBUTION.md)。

## 当前能力

- 本地管理员初始化、登录、会话、CSRF 与登录限速。
- 文本、Markdown、CSV、JSON 材料切分和中英文证据检索。
- Ollama 本地模型自动检测、固定模型清单、异步下载和进度显示。
- 本地模型回答必须包含有效引用，否则自动退回证据摘录。
- 待审简报、登录身份绑定审批、审计记录。
- 无模型时仍能完成证据检索、简报和审批闭环。
- 默认只监听 127.0.0.1，无遥测；Ollama 配置为禁止云能力。
- 模型端点仅允许本机回环地址或容器内 `ollama` 服务，避免材料被误发到远程接口。
- Docker 在线安装、NVIDIA 配置和跨平台完全离线介质包。
- Apache-2.0 开源，可同步到 GitHub、Gitee 和 GitCode。

## 模型档位

| 物理内存 | 默认生成模型 | 近似下载量 |
|---|---|---:|
| 8GB | qwen3.5:4b-q4_K_M | 3.4GB |
| 16GB | qwen3.5:9b-q4_K_M | 6.6GB |
| 32GB+ | qwen3.5:27b-q4_K_M | 17GB |

内存只是最低筛选条件，不代表性能承诺。上下文、并发、系统占用和 GPU 类型都会影响速度。国产 GPU 不应伪装成 Ollama 已支持；后续通过运行时适配器接入 MindIE、vLLM、SGLang、LMDeploy 等环境。

## 纯 Python 开发运行

不安装模型也能运行证据抽取模式：

~~~bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
openlocalai desktop
~~~

诊断环境：

~~~bash
openlocalai doctor
python3 -m unittest discover -s tests -v
~~~

## 安全与产品边界

当前已有单管理员保护，但尚未包含多部门 RBAC、统一身份、PDF/OFD/OCR、数据库加密、国密组件、日志防篡改、电子签章和高可用。因此可用于公开、合成、脱敏数据的试点和二次开发，不得直接处理国家秘密或作为未经验收的生产政务系统。

参见 [安全政策](SECURITY.md)、[安全边界](docs/SECURITY.md) 和 [路线图](docs/ROADMAP.md)。

## 发布到多个开源平台

先创建空仓库，再配置镜像：

~~~bash
git remote add github https://github.com/Buer5460/openlocalai-workbench.git
git remote add gitee git@gitee.com:YOUR_ORG/openlocalai-workbench.git
git remote add gitcode git@gitcode.com:YOUR_ORG/openlocalai-workbench.git
git push github main
git push gitee main
git push gitcode main
~~~

把 YOUR_ORG 替换为实际组织。不得把内部材料、数据库、离线模型包或密钥提交到公共源代码仓库。

## 参与贡献

先阅读 [贡献指南](CONTRIBUTING.md)、[治理规则](GOVERNANCE.md)、[第三方组件说明](THIRD_PARTY_NOTICES.md) 和 [安全政策](SECURITY.md)。
