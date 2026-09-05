#!/usr/bin/env python3
"""Reject build caches, metadata and local build paths in a staged release tree."""

from __future__ import annotations

import argparse
from pathlib import Path


FORBIDDEN_COMPONENTS = {".git", ".pytest_cache", ".venv", "__pycache__"}
FORBIDDEN_SUFFIXES = {".egg-info"}
FORBIDDEN_FILE_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_FILE_NAMES = {".DS_Store"}
REQUIRED_DIRECTORIES = {"src", "scripts", "docs"}


def forbidden_path_reason(path: Path, root: Path) -> str | None:
    relative = path.relative_to(root)
    for part in relative.parts:
        if part in FORBIDDEN_COMPONENTS:
            return f"forbidden path component {part}"
        if any(part.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            return f"forbidden metadata directory {part}"
    if path.is_file() and path.suffix.lower() in FORBIDDEN_FILE_SUFFIXES:
        return f"forbidden compiled file {path.name}"
    if path.is_file() and path.name in FORBIDDEN_FILE_NAMES:
        return f"forbidden platform metadata {path.name}"
    return None


def text_variants(value: str) -> set[bytes]:
    stripped = value.strip().rstrip("/\\")
    if not stripped:
        return set()
    candidates = {stripped, stripped.replace("\\", "/"), stripped.replace("/", "\\")}
    return {candidate.encode("utf-8") for candidate in candidates if candidate}


def contains_any(path: Path, needles: set[bytes]) -> bytes | None:
    if not needles:
        return None
    overlap = max(len(needle) for needle in needles) - 1
    tail = b""
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            content = tail + block
            for needle in needles:
                if needle in content:
                    return needle
            tail = content[-overlap:] if overlap > 0 else b""
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="检查桌面发行暂存目录是否干净")
    parser.add_argument("release_tree", type=Path)
    parser.add_argument(
        "--forbid-text",
        action="append",
        default=[],
        help="不得出现在任何发行文件中的本机构建路径；可重复指定",
    )
    args = parser.parse_args()

    root = args.release_tree.resolve()
    if not root.is_dir():
        parser.error(f"发行目录不存在：{root}")
    missing = sorted(name for name in REQUIRED_DIRECTORIES if not (root / name).is_dir())
    if missing:
        parser.error(f"发行目录缺少必要源码目录：{', '.join(missing)}")

    failures: list[str] = []
    needles: set[bytes] = set()
    for value in args.forbid_text:
        needles.update(text_variants(value))

    for path in sorted(root.rglob("*")):
        reason = forbidden_path_reason(path, root)
        if reason:
            failures.append(f"{path.relative_to(root)}: {reason}")
            continue
        if path.is_file():
            matched = contains_any(path, needles)
            if matched is not None:
                failures.append(
                    f"{path.relative_to(root)}: contains forbidden local path "
                    f"{matched.decode('utf-8', errors='replace')}"
                )

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1
    print(f"Release tree hygiene OK: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
