# 发行与镜像

## 可下载程序

推送版本标签后，GitHub Actions 会在原生系统上分别构建：

- Windows x86-64 ZIP
- Linux x86-64 tar.gz
- macOS Apple Silicon tar.gz
- macOS Intel tar.gz
- SHA-256 校验文件

```bash
git tag -s v0.2.0 -m "OpenLocalAI v0.2.0"
git push github v0.2.0
```

未配置 GPG 签名时不要使用 `-s`，但公共发行前应建立维护者签名和制品签名流程。Windows Authenticode 与 Apple Developer ID 需要发布组织自己的证书，开源仓库不能代替该身份背书。

## 国内镜像

源代码可以完整镜像到 Gitee/GitCode。Release 二进制和 `SHA256SUMS.txt` 应由同一流水线产出后原样上传，禁止在不同平台重新打包却沿用旧校验值。

## 模型与离线介质分发

源代码仓库和桌面发行包默认不携带模型权重。在线安装器由 Ollama 按固定模型清单下载；完全断网部署则使用 `build-offline-bundle.sh` 生成含权重的介质包。

默认 4B 模型本身约 3.4 GB，加上容器镜像后不适合作为单个 GitHub Release 附件；GitHub 目前限制每个 Release 文件小于 2 GiB。应把离线介质放在部署单位认可的国内对象存储或制品库，或者拆成每片小于 2 GiB 的编号分卷。无论使用哪种渠道，都要同时发布完整文件 SHA-256、各分卷 SHA-256、SBOM、签名和重组说明，且不得把模型介质直接提交进 Git 源码历史。

GitHub 限制说明：<https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases>。

## 版本发布门禁

1. 单元测试和安装后健康检查通过。
2. 锁定基础镜像和构建依赖版本，生成 SBOM。
3. 在目标 Windows、Linux、macOS 机器上进行冷启动验证。
4. 对可执行文件签名并发布 SHA-256。
5. 明确模型许可证、来源、哈希和适用范围。
6. 保留上一稳定版本及数据库备份说明。
