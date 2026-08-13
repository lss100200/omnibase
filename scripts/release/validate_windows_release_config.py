"""Offline, fail-closed validation for Windows release image references."""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path

IMAGE_REPOSITORIES = {
    "OMNIBASE_BACKEND_IMAGE": "ghcr.io/lss100200/omnibase-backend",
    "OMNIBASE_FRONTEND_IMAGE": "ghcr.io/lss100200/omnibase-frontend",
    "OMNIBASE_POSTGRES_IMAGE": "pgvector/pgvector",
    "OMNIBASE_REDIS_IMAGE": "redis",
    "OMNIBASE_MINIO_IMAGE": "minio/minio",
    "OMNIBASE_MINIO_MC_IMAGE": "minio/mc",
}
_IMAGE = re.compile(r"^(?P<repository>[a-z0-9./_-]+)@sha256:(?P<digest>[0-9a-f]{64})$")
_COMPOSE_IMAGE = re.compile(r"^\s*image:\s*\$\{(?P<name>[A-Z0-9_]+):\?[^}]+\}\s*$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ReleaseConfigError(ValueError):
    pass


def _read_regular(path: Path) -> str:
    metadata = path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or bool(file_attributes & reparse_flag)
    ):
        raise ReleaseConfigError("release_config_not_regular")
    return path.read_text(encoding="utf-8")


def _parse_env(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(raw.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ReleaseConfigError(f"release_env_line_invalid:{line_number}")
        name, value = line.split("=", 1)
        if _ENV_NAME.fullmatch(name) is None or not value:
            raise ReleaseConfigError(f"release_env_assignment_invalid:{line_number}")
        if name in values:
            raise ReleaseConfigError(f"release_env_duplicate:{name}")
        values[name] = value
    return values


def validate_release_config(compose_path: Path, env_path: Path) -> dict[str, object]:
    compose = _read_regular(compose_path)
    env = _parse_env(_read_regular(env_path))
    if re.search(r"^\s*build\s*:", compose, flags=re.MULTILINE):
        raise ReleaseConfigError("release_compose_build_forbidden")
    referenced = [
        match.group("name")
        for line in compose.splitlines()
        if (match := _COMPOSE_IMAGE.fullmatch(line)) is not None
    ]
    if set(referenced) != set(IMAGE_REPOSITORIES) or len(referenced) != 8:
        # Backend, Redis and their init/migration services intentionally reuse images.
        raise ReleaseConfigError("release_compose_image_closed_set_drifted")
    verified: list[dict[str, str]] = []
    for name, repository in IMAGE_REPOSITORIES.items():
        value = env.get(name, "")
        match = _IMAGE.fullmatch(value)
        if match is None or match.group("repository") != repository:
            raise ReleaseConfigError(f"release_image_not_allowlisted_or_digest_pinned:{name}")
        verified.append(
            {
                "variable": name,
                "repository": repository,
                "digest": match.group("digest"),
            }
        )
    return {"valid": True, "images": verified, "network_used": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_release_config(args.compose, args.env_file)
    except (OSError, UnicodeError, ReleaseConfigError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
