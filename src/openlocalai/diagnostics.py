"""Portable installation and hardware diagnostics."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from .ollama import MODEL_PROFILES, OllamaClient


def physical_memory_bytes() -> int | None:
    if sys.platform == "win32":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical)
        except (AttributeError, OSError):
            return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if isinstance(pages, int) and isinstance(page_size, int):
            return pages * page_size
    except (AttributeError, OSError, ValueError):
        pass
    if Path("/proc/meminfo").is_file():
        try:
            first = Path("/proc/meminfo").read_text(encoding="ascii").splitlines()[0]
            return int(first.split()[1]) * 1024
        except (OSError, IndexError, ValueError):
            pass
    return None


def _writable_target(path: Path) -> bool:
    target = path.resolve()
    while not target.exists() and target != target.parent:
        target = target.parent
    return target.is_dir() and os.access(target, os.W_OK)


def collect_diagnostics(data_path: Path, client: OllamaClient) -> dict:
    memory = physical_memory_bytes()
    memory_gb = round(memory / 1024**3, 1) if memory else None
    disk_target = data_path.parent
    existing = disk_target
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    disk = shutil.disk_usage(existing)
    model = client.status(refresh=True)
    checks = [
        {
            "id": "python",
            "status": "ok" if sys.version_info >= (3, 10) else "error",
            "message": f"Python {platform.python_version()}（要求 3.10+）",
        },
        {
            "id": "data_directory",
            "status": "ok" if _writable_target(data_path.parent) else "error",
            "message": f"数据目录：{data_path.parent}",
        },
        {
            "id": "disk",
            "status": "ok" if disk.free >= 10 * 1024**3 else "warn",
            "message": f"可用磁盘 {disk.free / 1024**3:.1f} GiB（建议至少 10 GiB）",
        },
        {
            "id": "ollama",
            "status": "ok" if model["available"] else "warn",
            "message": "Ollama 已连接" if model["available"] else model["error"],
        },
        {
            "id": "model",
            "status": "ok" if model["installed"] else "warn",
            "message": f"模型 {client.model} 已安装" if model["installed"] else f"模型 {client.model} 尚未安装",
        },
    ]
    if any(item["status"] == "error" for item in checks):
        overall = "error"
    elif any(item["status"] == "warn" for item in checks):
        overall = "warn"
    else:
        overall = "ok"
    suitable = [name for name, profile in MODEL_PROFILES.items() if memory_gb and memory_gb >= profile["memory_gb"]]
    return {
        "overall": overall,
        "system": {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "cpu": platform.processor() or platform.machine(),
            "memory_gb": memory_gb,
            "disk_free_gb": round(disk.free / 1024**3, 1),
        },
        "model": model,
        "suitable_models": suitable,
        "checks": checks,
    }
