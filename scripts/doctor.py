#!/usr/bin/env python3
"""OpenLocalAI deployment diagnostics; uses only the Python standard library."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(*command: str, timeout: int = 20) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    output = (result.stdout or result.stderr).strip()
    return result.returncode == 0, output


def physical_memory() -> int | None:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_phys)
        return None
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def gib(value: int | None) -> str:
    return "未知" if value is None else f"{value / 1024**3:.1f} GiB"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 OpenLocalAI 本地部署状态")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".openlocalai.env")
    parser.add_argument("--url", help="覆盖健康检查地址")
    args = parser.parse_args()

    env = read_env(args.env_file)
    host = env.get("OPENLOCALAI_BIND", "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = env.get("OPENLOCALAI_PORT", "8765")
    url = args.url or f"http://{host}:{port}"
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        parser.error("--url 必须是有效的 http(s) 地址")
    connect_host = parsed_url.hostname or host
    try:
        connect_port = parsed_url.port or (443 if parsed_url.scheme == "https" else int(port))
    except ValueError as error:
        parser.error(f"端口无效：{error}")
    model = env.get("OPENLOCALAI_MODEL", "qwen3.5:4b-q4_K_M")

    failures: list[str] = []
    warnings: list[str] = []
    print(f"系统：{platform.system()} {platform.release()} / {platform.machine()}")
    memory = physical_memory()
    print(f"物理内存：{gib(memory)}")
    if memory is not None and memory < 8 * 1024**3:
        warnings.append("物理内存少于 8 GiB，默认 4B 模型可能无法稳定运行。")

    disk = shutil.disk_usage(ROOT)
    print(f"项目磁盘剩余：{gib(disk.free)}")
    if disk.free < 12 * 1024**3:
        warnings.append("磁盘剩余少于 12 GiB，模型下载或升级可能失败。")

    docker_path = shutil.which("docker")
    print(f"Docker 命令：{docker_path or '未找到'}")
    if not docker_path:
        failures.append("未安装 Docker。")
    else:
        ok, output = run("docker", "compose", "version")
        print(f"Compose：{output or '不可用'}")
        if not ok:
            failures.append("缺少 Docker Compose v2。")

        ok, output = run("docker", "info", "--format", "{{json .}}")
        if not ok:
            failures.append("Docker 服务未运行或当前用户无权访问。")
        else:
            try:
                info = json.loads(output)
                print(f"Docker Engine：{info.get('ServerVersion', '未知')}")
                docker_memory = info.get("MemTotal")
                if isinstance(docker_memory, int):
                    print(f"Docker 可用内存：{gib(docker_memory)}")
                    if docker_memory < 6 * 1024**3:
                        warnings.append("Docker 可用内存少于 6 GiB，请调高 Docker Desktop 内存或改用更小模型。")
            except json.JSONDecodeError:
                warnings.append("无法解析 Docker 资源信息。")

        for container in ("openlocalai-app", "openlocalai-ollama"):
            ok, output = run(
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                container,
            )
            print(f"容器 {container}：{output or '不存在'}")
            if not ok:
                failures.append(f"容器 {container} 不存在或无法读取。")
            elif "unhealthy" in output or not output.startswith("running"):
                failures.append(f"容器 {container} 状态异常：{output}")

        ok, output = run("docker", "exec", "openlocalai-ollama", "ollama", "list", timeout=30)
        if ok:
            installed = model.split(":", 1)[0] in output and model.split(":", 1)[-1].lower() in output.lower()
            print(f"模型 {model}：{'已安装' if installed else '未在列表中'}")
            if not installed:
                failures.append(f"模型 {model} 未安装。")
        else:
            failures.append("无法读取 Ollama 模型列表。")

    try:
        no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with no_proxy.open(f"{url.rstrip('/')}/api/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            print(f"应用健康接口：HTTP {response.status} / {payload.get('status', '未知')}")
            if response.status != 200 or payload.get("status") != "ok":
                failures.append("应用健康接口返回异常。")
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        failures.append(f"无法访问 {url}/api/health：{error}")

    try:
        with socket.create_connection((connect_host, connect_port), timeout=3):
            print(f"端口 {connect_host}:{connect_port}：可连接")
    except (OSError, ValueError) as error:
        failures.append(f"端口 {connect_host}:{connect_port} 不可连接：{error}")

    for warning in warnings:
        print(f"警告：{warning}")
    for failure in failures:
        print(f"失败：{failure}")

    if failures:
        print(f"结论：发现 {len(failures)} 个阻断问题。")
        return 1
    print("结论：部署检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
