from __future__ import annotations

import argparse
import json
import os
import platform
import socket
from pathlib import Path

from . import __version__
from .diagnostics import collect_diagnostics
from .ollama import OllamaClient
from .server import run


def user_data_path() -> Path:
    system = platform.system()
    if system == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "OpenLocalAI" / "openlocalai.db"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "OpenLocalAI" / "openlocalai.db"
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "openlocalai" / "openlocalai.db"


def choose_port(preferred: int, attempts: int = 20) -> int:
    if preferred == 0:
        return 0
    for port in range(preferred, min(65_536, preferred + attempts)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"端口 {preferred}-{preferred + attempts - 1} 均被占用")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenLocalAI 离线 AI 工作台")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="启动本地工作台")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data", type=Path, default=Path("data/openlocalai.db"))

    desktop = subparsers.add_parser("desktop", help="桌面安装包入口：启动服务并打开浏览器")
    desktop.add_argument("--port", type=int, default=8765)
    desktop.add_argument("--data", type=Path, default=None, help="覆盖操作系统用户数据目录")

    doctor = subparsers.add_parser("doctor", help="检查硬件、数据目录、Ollama 和本地模型")
    doctor.add_argument("--data", type=Path, default=None)
    doctor.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser


def _validate_port(parser: argparse.ArgumentParser, port: int) -> None:
    if not 1 <= port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        _validate_port(parser, args.port)
        run(args.host, args.port, args.data.expanduser().resolve())
        return 0
    if args.command == "desktop":
        _validate_port(parser, args.port)
        data_path = (args.data or user_data_path()).expanduser().resolve()
        try:
            port = choose_port(args.port)
        except RuntimeError as error:
            parser.error(str(error))
        run("127.0.0.1", port, data_path, open_browser=True)
        return 0
    if args.command == "doctor":
        data_path = (args.data or user_data_path()).expanduser().resolve()
        try:
            result = collect_diagnostics(data_path, OllamaClient())
        except ValueError as error:
            parser.error(str(error))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            icons = {"ok": "[通过]", "warn": "[提醒]", "error": "[失败]"}
            print("OpenLocalAI 部署检查")
            for check in result["checks"]:
                print(f"{icons[check['status']]} {check['message']}")
            suitable = "、".join(result["suitable_models"]) or "未能根据内存推荐模型"
            print(f"适合本机：{suitable}")
        return 1 if result["overall"] == "error" else 0
    return 0


def desktop_main() -> int:
    """Entry point for PyInstaller and desktop launchers."""
    return main(["desktop"])
