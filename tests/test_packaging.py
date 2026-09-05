import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OFFLINE_SBOM = ROOT / "scripts" / "generate_sbom.py"
DESKTOP_SBOM = ROOT / "scripts" / "generate_desktop_sbom.py"
RELEASE_CHECKER = ROOT / "scripts" / "check_release_tree.py"


class PackagingTests(unittest.TestCase):
    def run_offline_sbom(
        self, directory: Path, model: str, ollama_image: str, include_identity_key: bool = False
    ) -> subprocess.CompletedProcess:
        images = directory / "images.tar.gz"
        models = directory / "ollama-models.tar.gz"
        images.write_bytes(b"images-test\n")
        with tarfile.open(models, "w:gz") as archive:
            members = {
                "models/blobs/sha256-test": b"model-blob\n",
                "models/manifests/registry.ollama.ai/library/qwen/test": b"manifest\n",
            }
            if include_identity_key:
                members["models/id_ed25519"] = b"private-key\n"
            for name, content in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [
                sys.executable,
                str(OFFLINE_SBOM),
                "--project-root",
                str(ROOT),
                "--app-image",
                "openlocalai-workbench:local",
                "--ollama-image",
                ollama_image,
                "--model",
                model,
                "--images-archive",
                str(images),
                "--models-archive",
                str(models),
                "--output",
                str(directory / "sbom.spdx.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )

    def test_offline_sbom_contains_exact_components_and_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            result = self.run_offline_sbom(directory, "qwen3.5:4b-q4_K_M", "ollama/ollama:0.33.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            sbom = json.loads((directory / "sbom.spdx.json").read_text(encoding="utf-8"))
            self.assertEqual(sbom["spdxVersion"], "SPDX-2.3")
            packages = {package["SPDXID"]: package for package in sbom["packages"]}
            self.assertEqual(packages["SPDXRef-Package-OpenLocalAI"]["licenseDeclared"], "Apache-2.0")
            self.assertEqual(packages["SPDXRef-Package-Ollama"]["versionInfo"], "0.33.3")
            self.assertEqual(packages["SPDXRef-Package-Ollama"]["licenseDeclared"], "MIT")
            alpine = packages["SPDXRef-Package-Alpine-Restore-Image"]
            self.assertEqual(alpine["versionInfo"], "3.21.3")
            self.assertEqual(alpine["packageFileName"], "alpine:3.21.3")
            self.assertEqual(alpine["licenseDeclared"], "NOASSERTION")
            self.assertEqual(alpine["supplier"], "Organization: Alpine Linux Development Team")
            self.assertEqual(
                packages["SPDXRef-Package-QwenModel"]["packageFileName"], "qwen3.5:4b-q4_K_M"
            )
            files = {item["fileName"]: item for item in sbom["files"]}
            for name in ("images.tar.gz", "ollama-models.tar.gz"):
                expected = hashlib.sha256((directory / name).read_bytes()).hexdigest()
                checksums = {item["algorithm"]: item["checksumValue"] for item in files[f"./{name}"]["checksums"]}
                self.assertEqual(checksums["SHA256"], expected)
                self.assertIn("SHA1", checksums)

    def test_offline_sbom_rejects_unreviewed_model_before_asserting_license(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            result = self.run_offline_sbom(
                Path(raw_directory), "qwen3-embedding:0.6b", "ollama/ollama:0.33.3"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("许可证映射审核", result.stderr)

    def test_offline_sbom_rejects_unpinned_ollama(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            result = self.run_offline_sbom(
                Path(raw_directory), "qwen3.5:4b-q4_K_M", "ollama/ollama:latest"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("必须固定为 0.33.3", result.stderr)

    def test_offline_sbom_rejects_ollama_identity_key(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            result = self.run_offline_sbom(
                Path(raw_directory),
                "qwen3.5:4b-q4_K_M",
                "ollama/ollama:0.33.3",
                include_identity_key=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("身份密钥", result.stderr)

    def test_desktop_sbom_keeps_release_workflow_cli(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            binary = directory / "OpenLocalAI"
            output = directory / "OpenLocalAI.spdx.json"
            binary.write_bytes(b"desktop-test\n")
            result = subprocess.run(
                [sys.executable, str(DESKTOP_SBOM), str(binary), str(output), "--version", "0.2.0"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            sbom = json.loads(output.read_text(encoding="utf-8"))
            package = sbom["packages"][0]
            self.assertEqual(sbom["documentDescribes"], ["SPDXRef-Package-OpenLocalAI-Desktop"])
            self.assertEqual(package["versionInfo"], "0.2.0")
            self.assertTrue(package["filesAnalyzed"])
            checksums = {item["algorithm"]: item["checksumValue"] for item in sbom["files"][0]["checksums"]}
            self.assertEqual(checksums["SHA256"], hashlib.sha256(binary.read_bytes()).hexdigest())
            verification_code = hashlib.sha1(checksums["SHA1"].encode("ascii")).hexdigest()
            self.assertEqual(package["packageVerificationCode"]["packageVerificationCodeValue"], verification_code)
            package_names = {item["name"] for item in sbom["packages"]}
            self.assertIn("CPython embedded runtime", package_names)
            self.assertIn("PyInstaller bootloader", package_names)

    def test_release_tree_hygiene_accepts_clean_tracked_layout(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            for name in ("src", "scripts", "docs"):
                (directory / name).mkdir()
            (directory / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(RELEASE_CHECKER), str(directory)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_release_tree_hygiene_rejects_cache_metadata_and_build_path(self):
        forbidden_build_path = "/private/build/openlocalai"
        cases = {
            "src/openlocalai/__pycache__/module.cpython-312.pyc": b"compiled",
            "src/openlocalai_workbench.egg-info/PKG-INFO": b"metadata",
            "OpenLocalAI": f"embedded {forbidden_build_path}/src".encode(),
        }
        for relative, content in cases.items():
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as raw_directory:
                directory = Path(raw_directory)
                for name in ("src", "scripts", "docs"):
                    (directory / name).mkdir()
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(RELEASE_CHECKER),
                        str(directory),
                        "--forbid-text",
                        forbidden_build_path,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                self.assertNotEqual(result.returncode, 0)

    def test_release_workflow_exports_tracked_files_without_recursive_copy(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("git archive --format=tar HEAD", workflow)
        self.assertIn("scripts/check_release_tree.py", workflow)
        self.assertNotIn("cp -R src scripts docs", workflow)

    @unittest.skipUnless(
        os.name != "nt" and shutil.which("bash"),
        "POSIX bash is required for installer guard tests",
    )
    def test_installers_reject_embedding_model_before_docker(self):
        for script in (ROOT / "scripts" / "install.sh", ROOT / "scripts" / "build-offline-bundle.sh"):
            result = subprocess.run(
                ["bash", str(script), "--model", "qwen3-embedding:0.6b", "--no-open"]
                if script.name == "install.sh"
                else ["bash", str(script), "--model", "qwen3-embedding:0.6b"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("不在已审核", result.stderr)

    def make_offline_bundle_for_platform_test(self, directory: Path) -> Path:
        (directory / "licenses").mkdir()
        required_files = [
            "BUNDLE_INFO.txt",
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "OFFLINE_INSTALL.md",
            "compose.yaml",
            "compose.nvidia.yaml",
            "install.sh",
            "install.ps1",
            ".openlocalai.env",
            "licenses/OpenLocalAI-Apache-2.0.txt",
            "licenses/Qwen-Apache-2.0.txt",
            "licenses/Ollama-MIT.txt",
            "licenses/Python-2.0.txt",
            "licenses/PyInstaller-GPL-2.0-or-later-with-Bootloader-exception.txt",
            "sbom.spdx.json",
            "images.tar.gz",
            "ollama-models.tar.gz",
        ]
        for relative in required_files:
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == "install.sh":
                shutil.copy2(ROOT / "scripts" / "install-offline.sh", target)
            elif relative == "install.ps1":
                shutil.copy2(ROOT / "scripts" / "install-offline.ps1", target)
            elif relative == "BUNDLE_INFO.txt":
                target.write_text("OpenLocalAI offline bundle\nplatform=linux-arm64\n", encoding="utf-8")
            else:
                target.write_text(f"fixture for {relative}\n", encoding="utf-8")
        checksums = []
        for relative in required_files:
            digest = hashlib.sha256((directory / relative).read_bytes()).hexdigest()
            checksums.append(f"{digest}  {relative}\n")
        (directory / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")

        fake_bin = directory / "fake-bin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text(
            """#!/usr/bin/env bash
case "${1:-}" in
  info) exit 0 ;;
  compose) [[ "${2:-}" == version ]] && exit 0 ;;
  version) printf '%s\\n' linux-amd64; exit 0 ;;
  load|volume|run) : >"${MUTATION_MARKER:?}"; exit 0 ;;
esac
exit 1
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        docker_cmd = fake_bin / "docker.cmd"
        docker_cmd.write_text(
            """@echo off
if "%1"=="info" exit /b 0
if "%1"=="compose" if "%2"=="version" exit /b 0
if "%1"=="version" echo linux-amd64& exit /b 0
if "%1"=="load" type nul > "%MUTATION_MARKER%"& exit /b 0
if "%1"=="volume" type nul > "%MUTATION_MARKER%"& exit /b 0
if "%1"=="run" type nul > "%MUTATION_MARKER%"& exit /b 0
exit /b 1
""",
            encoding="utf-8",
        )
        return fake_bin

    @unittest.skipUnless(
        os.name != "nt" and shutil.which("bash"),
        "POSIX bash is required for offline installer tests",
    )
    def test_offline_shell_installer_rejects_platform_mismatch_before_mutation(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            fake_bin = self.make_offline_bundle_for_platform_test(directory)
            marker = directory / "mutated"
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["MUTATION_MARKER"] = str(marker)
            result = subprocess.run(
                ["bash", str(directory / "install.sh"), "--no-open"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("不匹配", result.stderr)
            self.assertFalse(marker.exists(), "平台不匹配时不得导入镜像或修改数据卷")

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required for offline installer tests")
    def test_offline_powershell_installer_rejects_platform_mismatch_before_mutation(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            fake_bin = self.make_offline_bundle_for_platform_test(directory)
            marker = directory / "mutated"
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["MUTATION_MARKER"] = str(marker)
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(directory / "install.ps1"), "-NoOpen"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("不匹配", result.stderr + result.stdout)
            self.assertFalse(marker.exists(), "平台不匹配时不得导入镜像或修改数据卷")


if __name__ == "__main__":
    unittest.main()
