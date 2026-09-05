#!/usr/bin/env python3
"""Generate an SPDX 2.3 SBOM for one packaged desktop executable."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
PYINSTALLER_FALLBACK_VERSION = "6.22.2"


def hashes(path: Path) -> tuple[str, str]:
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha1.update(block)
            sha256.update(block)
    return sha1.hexdigest(), sha256.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成桌面可执行文件 SPDX 2.3 SBOM")
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--python-version", default=platform.python_version())
    try:
        pyinstaller_version = importlib.metadata.version("pyinstaller")
    except importlib.metadata.PackageNotFoundError:
        pyinstaller_version = PYINSTALLER_FALLBACK_VERSION
    parser.add_argument("--pyinstaller-version", default=pyinstaller_version)
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"可执行文件不存在：{args.binary}")
    if not VERSION_PATTERN.fullmatch(args.version):
        parser.error("--version 必须是明确的语义化版本号")

    sha1, sha256 = hashes(args.binary)
    document_uuid = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"OpenLocalAI|{args.version}|{args.binary.name}|{sha256}",
    )
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    package_id = "SPDXRef-Package-OpenLocalAI-Desktop"
    python_id = "SPDXRef-Package-CPython-Runtime"
    bootloader_id = "SPDXRef-Package-PyInstaller-Bootloader"
    file_id = "SPDXRef-File-OpenLocalAI-Executable"
    binary_license = "Apache-2.0 AND Python-2.0 AND (GPL-2.0-or-later WITH Bootloader-exception)"
    verification_code = hashlib.sha1(sha1.lower().encode("ascii")).hexdigest()
    document = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "name": f"OpenLocalAI desktop executable {args.version}",
        "documentNamespace": f"urn:uuid:{document_uuid}",
        "creationInfo": {
            "created": created,
            "creators": ["Tool: OpenLocalAI desktop SBOM generator-1.0"],
        },
        "documentDescribes": [package_id],
        "comment": (
            "Artifact-level SBOM for the packaged executable and its directly bundled CPython runtime "
            "and PyInstaller bootloader. THIRD_PARTY_NOTICES.md is shipped beside the executable."
        ),
        "packages": [
            {
                "SPDXID": package_id,
                "name": "OpenLocalAI Workbench desktop executable",
                "versionInfo": args.version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "packageVerificationCode": {"packageVerificationCodeValue": verification_code},
                "licenseConcluded": binary_license,
                "licenseDeclared": "Apache-2.0",
                "copyrightText": "NOASSERTION",
                "supplier": "Organization: OpenLocalAI Contributors",
                "packageFileName": args.binary.name,
                "primaryPackagePurpose": "APPLICATION",
                "comment": (
                    "The executable is produced with PyInstaller. Runtime and build-tool notices are "
                    "provided in THIRD_PARTY_NOTICES.md in the same release package."
                ),
            },
            {
                "SPDXID": python_id,
                "name": "CPython embedded runtime",
                "versionInfo": args.python_version,
                "downloadLocation": "https://www.python.org/",
                "filesAnalyzed": False,
                "licenseConcluded": "Python-2.0",
                "licenseDeclared": "Python-2.0",
                "copyrightText": "Copyright (c) Python Software Foundation",
                "supplier": "Organization: Python Software Foundation",
                "primaryPackagePurpose": "LIBRARY",
            },
            {
                "SPDXID": bootloader_id,
                "name": "PyInstaller bootloader",
                "versionInfo": args.pyinstaller_version,
                "downloadLocation": "https://github.com/pyinstaller/pyinstaller",
                "filesAnalyzed": False,
                "licenseConcluded": "GPL-2.0-or-later WITH Bootloader-exception",
                "licenseDeclared": "GPL-2.0-or-later WITH Bootloader-exception",
                "copyrightText": "NOASSERTION",
                "supplier": "Organization: PyInstaller Development Team",
                "primaryPackagePurpose": "LIBRARY",
            },
        ],
        "files": [
            {
                "SPDXID": file_id,
                "fileName": f"./{args.binary.name}",
                "fileTypes": ["BINARY"],
                "checksums": [
                    {"algorithm": "SHA1", "checksumValue": sha1},
                    {"algorithm": "SHA256", "checksumValue": sha256},
                ],
                "licenseConcluded": binary_license,
                "copyrightText": "NOASSERTION",
            }
        ],
        "relationships": [
            {
                "spdxElementId": package_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": file_id,
            },
            {
                "spdxElementId": file_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": python_id,
            },
            {
                "spdxElementId": file_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": bootloader_id,
            },
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(args.output.read_text(encoding="utf-8"))
    print(f"SPDX SBOM: {args.output}")
    print(f"{args.binary.name} SHA256: {sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
