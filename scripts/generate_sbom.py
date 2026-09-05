#!/usr/bin/env python3
"""Generate a deterministic-content SPDX 2.3 SBOM for an offline bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


OLLAMA_VERSION = "0.33.3"
ALPINE_IMAGE = "alpine:3.21.3"
ALPINE_VERSION = "3.21.3"
MODEL_METADATA = {
    "qwen3.5:4b-q4_K_M": {
        "name": "Qwen3.5 4B Q4_K_M for Ollama",
        "version": "4b-q4_K_M",
        "license": "Apache-2.0",
        "download": "https://ollama.com/library/qwen3.5:4b-q4_K_M",
    },
    "qwen3.5:9b-q4_K_M": {
        "name": "Qwen3.5 9B Q4_K_M for Ollama",
        "version": "9b-q4_K_M",
        "license": "Apache-2.0",
        "download": "https://ollama.com/library/qwen3.5:9b-q4_K_M",
    },
    "qwen3.5:27b-q4_K_M": {
        "name": "Qwen3.5 27B Q4_K_M for Ollama",
        "version": "27b-q4_K_M",
        "license": "Apache-2.0",
        "download": "https://ollama.com/library/qwen3.5:27b-q4_K_M",
    },
    "qwen3:4b": {
        "name": "Qwen3 4B for Ollama",
        "version": "4b",
        "license": "Apache-2.0",
        "download": "https://ollama.com/library/qwen3:4b",
    },
}
VERSION_PATTERN = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)


def artifact_hashes(path: Path) -> tuple[str, str]:
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha1.update(block)
            sha256.update(block)
    return sha1.hexdigest(), sha256.hexdigest()


def app_version(project_root: Path) -> str:
    init_file = project_root / "src" / "openlocalai" / "__init__.py"
    match = VERSION_PATTERN.search(init_file.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"无法从 {init_file} 读取应用版本")
    return match.group(1)


def validate_model_archive(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as error:
        raise ValueError(f"模型归档不是有效的 tar.gz：{error}") from error
    if not members:
        raise ValueError("模型归档为空")
    regular_files = 0
    for member in members:
        member_path = PurePosixPath(member.name)
        parts = member_path.parts
        if member_path.is_absolute() or not parts or parts[0] != "models":
            raise ValueError(f"模型归档包含 models/ 之外的路径：{member.name}")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"模型归档包含不安全路径：{member.name}")
        if any(part.startswith("id_ed25519") for part in parts):
            raise ValueError("模型归档不得包含 Ollama 身份密钥")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ValueError(f"模型归档包含不允许的特殊成员：{member.name}")
        if member.isfile():
            regular_files += 1
    if regular_files == 0:
        raise ValueError("模型归档中没有模型文件")


def package(
    spdx_id: str,
    name: str,
    version: str,
    license_id: str,
    download: str,
    supplier: str,
    package_file_name: str,
    purpose: str,
    comment: str | None = None,
) -> dict:
    result = {
        "SPDXID": spdx_id,
        "name": name,
        "versionInfo": version,
        "downloadLocation": download,
        "filesAnalyzed": False,
        "licenseConcluded": license_id,
        "licenseDeclared": license_id,
        "copyrightText": "NOASSERTION",
        "supplier": supplier,
        "packageFileName": package_file_name,
        "primaryPackagePurpose": purpose,
    }
    if comment:
        result["comment"] = comment
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="生成离线交付包 SPDX 2.3 SBOM")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--app-image", required=True)
    parser.add_argument("--ollama-image", required=True)
    parser.add_argument("--alpine-image", default=ALPINE_IMAGE)
    parser.add_argument("--model", required=True)
    parser.add_argument("--images-archive", type=Path, required=True)
    parser.add_argument("--models-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    metadata = MODEL_METADATA.get(args.model)
    if metadata is None:
        allowed = ", ".join(sorted(MODEL_METADATA))
        parser.error(f"模型没有经过许可证映射审核：{args.model}；允许值：{allowed}")
    if not args.ollama_image.rsplit("/", 1)[-1].endswith(f":{OLLAMA_VERSION}"):
        parser.error(f"Ollama 镜像必须固定为 {OLLAMA_VERSION} 标签")
    if args.alpine_image != ALPINE_IMAGE:
        parser.error(f"恢复工具镜像必须固定为 {ALPINE_IMAGE}")
    for archive in (args.images_archive, args.models_archive):
        if not archive.is_file():
            parser.error(f"交付文件不存在：{archive}")
    try:
        validate_model_archive(args.models_archive)
    except ValueError as error:
        parser.error(str(error))

    version = app_version(args.project_root.resolve())
    images_sha1, images_hash = artifact_hashes(args.images_archive)
    models_sha1, models_hash = artifact_hashes(args.models_archive)
    namespace_seed = (
        f"{version}|{args.app_image}|{args.ollama_image}|{args.alpine_image}|"
        f"{args.model}|{images_hash}|{models_hash}"
    )
    document_uuid = uuid.uuid5(uuid.NAMESPACE_URL, namespace_seed)
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    bundle_id = "SPDXRef-Package-OfflineBundle"
    app_id = "SPDXRef-Package-OpenLocalAI"
    ollama_id = "SPDXRef-Package-Ollama"
    alpine_id = "SPDXRef-Package-Alpine-Restore-Image"
    model_id = "SPDXRef-Package-QwenModel"
    images_file_id = "SPDXRef-File-ImagesArchive"
    models_file_id = "SPDXRef-File-ModelsArchive"

    document = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "name": f"OpenLocalAI offline bundle {version} ({args.model})",
        "documentNamespace": f"urn:uuid:{document_uuid}",
        "creationInfo": {
            "created": created,
            "creators": ["Tool: OpenLocalAI offline SBOM generator-1.0"],
        },
        "documentDescribes": [bundle_id],
        "comment": (
            "Delivery-level SPDX SBOM for the offline bundle. It identifies the selected application, "
            "runtime, restore image and model plus the two transport archives; it is not a transitive "
            "OS-package scan of the container images."
        ),
        "packages": [
            package(
                bundle_id,
                "OpenLocalAI offline installation bundle",
                version,
                "NOASSERTION",
                "NOASSERTION",
                "Organization: OpenLocalAI Contributors",
                args.output.parent.name,
                "ARCHIVE",
            ),
            package(
                app_id,
                "OpenLocalAI Workbench container image",
                version,
                "Apache-2.0",
                "NOASSERTION",
                "Organization: OpenLocalAI Contributors",
                args.app_image,
                "APPLICATION",
            ),
            package(
                ollama_id,
                "Ollama container image",
                OLLAMA_VERSION,
                "MIT",
                f"https://github.com/ollama/ollama/releases/tag/v{OLLAMA_VERSION}",
                "Organization: Ollama",
                args.ollama_image,
                "APPLICATION",
                "The bundle pins Ollama 0.33.3; its bundled license copy is licenses/Ollama-MIT.txt.",
            ),
            package(
                alpine_id,
                "Alpine Linux restore container image",
                ALPINE_VERSION,
                "NOASSERTION",
                "https://hub.docker.com/_/alpine",
                "Organization: Alpine Linux Development Team",
                args.alpine_image,
                "OPERATING-SYSTEM",
                (
                    "The bundle directly ships this pinned image to validate and restore the model "
                    "archive. Alpine is an aggregate distribution whose contained packages use "
                    "multiple licenses, so no single package-level license is asserted here."
                ),
            ),
            package(
                model_id,
                metadata["name"],
                metadata["version"],
                metadata["license"],
                metadata["download"],
                "Organization: Qwen",
                args.model,
                "OTHER",
                (
                    "License metadata is taken from this generator's reviewed model allowlist. "
                    "The selected quantized model is stored inside ollama-models.tar.gz."
                ),
            ),
        ],
        "files": [
            {
                "SPDXID": images_file_id,
                "fileName": f"./{args.images_archive.name}",
                "fileTypes": ["ARCHIVE"],
                "checksums": [
                    {"algorithm": "SHA1", "checksumValue": images_sha1},
                    {"algorithm": "SHA256", "checksumValue": images_hash},
                ],
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            },
            {
                "SPDXID": models_file_id,
                "fileName": f"./{args.models_archive.name}",
                "fileTypes": ["ARCHIVE"],
                "checksums": [
                    {"algorithm": "SHA1", "checksumValue": models_sha1},
                    {"algorithm": "SHA256", "checksumValue": models_hash},
                ],
                "licenseConcluded": metadata["license"],
                "copyrightText": "NOASSERTION",
            },
        ],
        "relationships": [
            {"spdxElementId": bundle_id, "relationshipType": "CONTAINS", "relatedSpdxElement": app_id},
            {"spdxElementId": bundle_id, "relationshipType": "CONTAINS", "relatedSpdxElement": ollama_id},
            {"spdxElementId": bundle_id, "relationshipType": "CONTAINS", "relatedSpdxElement": alpine_id},
            {"spdxElementId": bundle_id, "relationshipType": "CONTAINS", "relatedSpdxElement": model_id},
            {"spdxElementId": images_file_id, "relationshipType": "CONTAINS", "relatedSpdxElement": app_id},
            {
                "spdxElementId": images_file_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": ollama_id,
            },
            {
                "spdxElementId": images_file_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": alpine_id,
            },
            {
                "spdxElementId": models_file_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": model_id,
            },
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(args.output.read_text(encoding="utf-8"))
    print(f"SPDX SBOM: {args.output}")
    print(f"images.tar.gz SHA256: {images_hash}")
    print(f"ollama-models.tar.gz SHA256: {models_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
