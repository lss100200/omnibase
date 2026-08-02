"""Validate and fingerprint the source-complete disposable Overlay Gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

PYTHON_IMAGE = "python@sha256:" "f9ce6fe33d9a5499e35c976df16d24ae80f6ef0a28be5433140236c2ca482686"
HEADSCALE_IMAGE = (
    "headscale/headscale@sha256:" "ea9b5ee06274d757a4d52103de56cd11a9c393acb19d9a35f4b9fe52ada410de"
)
PKI_IMAGE = (
    "alpine/openssl@sha256:" "5008e829163320a6e8166883c03e68189e8925ade68cde36584dc2a41cfa5248"
)

EXACT_SOURCE_PATHS = (
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/src/omnibase/sandbox/overlay_publication.py",
    "backend/tests/test_p34_5_overlay_adapter.py",
    "backend/tests/test_p34_5_overlay_ledger.py",
    "backend/tests/test_p34_5_overlay_disposable_gate.py",
    "deployment/overlay/Dockerfile.gate-runner",
    "deployment/overlay/Dockerfile.gate-runner.dockerignore",
    "deployment/overlay/compose.disposable.yml",
    "deployment/overlay/gate.env",
    "deployment/overlay/headscale-config.yaml",
    "scripts/overlay/node_daemon_test_double.py",
    "scripts/overlay/run_disposable_overlay_gate.ps1",
    "scripts/overlay/test_validate_disposable_gate.py",
    "scripts/overlay/validate_disposable_gate.py",
)
SOURCE_GLOBS = ("backend/src/**/*",)


class GateValidationError(RuntimeError):
    """The disposable Gate source or provenance contract is invalid."""


def _run_git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise GateValidationError(f"git {' '.join(arguments)} failed without usable provenance")
    return completed.stdout.strip()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _source_paths(root: Path) -> list[str]:
    paths = set(EXACT_SOURCE_PATHS)
    for pattern in SOURCE_GLOBS:
        paths.update(
            path.relative_to(root).as_posix() for path in root.glob(pattern) if path.is_file()
        )
    missing = [relative for relative in sorted(paths) if not (root / relative).is_file()]
    if missing:
        raise GateValidationError(f"required Gate source is missing: {missing}")
    symlinks = [relative for relative in sorted(paths) if (root / relative).is_symlink()]
    if symlinks:
        raise GateValidationError(f"Gate source symlinks are forbidden: {symlinks}")
    return sorted(paths)


def _git_path_set(root: Path, *arguments: str, paths: list[str]) -> set[str]:
    output = _run_git(root, *arguments, "--", *paths)
    return {line.strip().replace("\\", "/") for line in output.splitlines() if line}


def build_source_manifest(root: Path) -> dict[str, Any]:
    """Build a deterministic manifest for every Gate input and its Git scope."""

    paths = _source_paths(root)
    tracked = _git_path_set(root, "ls-files", paths=paths)
    dirty_tracked = _git_path_set(root, "diff", "--name-only", "HEAD", paths=paths)
    untracked = _git_path_set(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        paths=paths,
    )
    dirty_paths = sorted((dirty_tracked | untracked) & set(paths))

    files = []
    for relative in paths:
        data = (root / relative).read_bytes()
        files.append(
            {
                "path": relative,
                "sha256": _sha256_bytes(data),
                "size": len(data),
                "tracked": relative in tracked,
            }
        )

    source_tree_sha256 = _sha256_bytes(_canonical_bytes(files))
    dirty_scope = [
        {
            "path": item["path"],
            "sha256": item["sha256"],
            "size": item["size"],
            "tracked": item["tracked"],
        }
        for item in files
        if item["path"] in dirty_paths
    ]
    return {
        "schema": "omnibase.p34-5c.source-manifest.v1",
        "git": {
            "commit": _run_git(root, "rev-parse", "HEAD"),
            "tree": _run_git(root, "rev-parse", "HEAD^{tree}"),
            "dirty": bool(dirty_scope),
            "dirty_paths": dirty_paths,
            "dirty_scope_sha256": _sha256_bytes(_canonical_bytes(dirty_scope)),
        },
        "source_tree_sha256": source_tree_sha256,
        "file_count": len(files),
        "files": files,
        "upstream_images": {
            "gate_runner_base": PYTHON_IMAGE,
            "headscale": HEADSCALE_IMAGE,
            "node_daemon": PYTHON_IMAGE,
            "provider_secret_init": PYTHON_IMAGE,
            "pki": PKI_IMAGE,
        },
        "real_member_devices_registered": 0,
        "root_env_included": False,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_manifest(root: Path, path: Path) -> dict[str, Any]:
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateValidationError("source manifest is unavailable or invalid") from exc
    current = build_source_manifest(root)
    if recorded != current:
        raise GateValidationError("Gate source changed after provenance was recorded")
    return current


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GateValidationError(message)


def _validate_compose(compose_text: str) -> None:
    _require(
        "${OMNIBASE_GATE_" not in compose_text,
        "Gate configuration permits environment substitution",
    )
    _require("context: ../.." in compose_text, "Gate Runner build context is not owned")
    _require(
        "dockerfile: deployment/overlay/Dockerfile.gate-runner" in compose_text,
        "Gate Runner Dockerfile is not fixed",
    )
    _require(
        "image: omnibase-p345-overlay-gate-runner:source" in compose_text,
        "Gate Runner image name is not fixed",
    )
    _require(
        "omnibase-backend:latest" not in compose_text and "external: true" not in compose_text,
        "Gate still relies on an ambient image or external volume",
    )
    _require(
        "gate-runner-venv" not in compose_text and "../../backend/src:/app/src" not in compose_text,
        "Gate Runner still relies on host runtime volumes",
    )
    _require(f"image: {HEADSCALE_IMAGE}" in compose_text, "Headscale image digest drifted")
    _require(
        compose_text.count(f"image: {PYTHON_IMAGE}") == 2,
        "Python helper image digest drifted",
    )
    _require(f"image: {PKI_IMAGE}" in compose_text, "PKI image digest drifted")
    _require(
        "ports:" not in compose_text and "internal: true" in compose_text,
        "disposable Overlay network isolation drifted",
    )
    _require(
        "provider-secrets:/run/omnibase-provider-secrets:ro" in compose_text,
        "Node Daemon provider secret volume is not read-only",
    )
    gate_runner_block = compose_text.split("  gate-runner:", 1)[1].split("networks:", 1)[0]
    _require("provider-secrets" not in gate_runner_block, "Gate Runner received provider secrets")
    _require(
        "--headscale-api-key-file" in compose_text,
        "Node Daemon Headscale API key binding is missing",
    )


def _validate_dockerfile(dockerfile_text: str, dockerignore_text: str) -> None:
    _require(
        dockerfile_text.startswith(f"FROM {PYTHON_IMAGE}\n"),
        "Gate Runner base image is not digest-pinned",
    )
    for required in (
        "COPY backend/pyproject.toml backend/uv.lock",
        "--require-hashes",
        "--no-deps",
        "COPY backend/src /app/src",
        "COPY backend/tests/test_p34_5_overlay_disposable_gate.py",
    ):
        _require(
            required in dockerfile_text,
            f"Gate Runner deterministic build step missing: {required}",
        )
    _require(
        dockerignore_text.splitlines()[0].strip() == "**",
        "Gate Docker context must deny by default",
    )
    _require(
        "!.env" not in dockerignore_text and "!.env.example" not in dockerignore_text,
        "Gate Docker context must not include root env files",
    )


def _validate_runner(runner_text: str) -> None:
    required_fragments = {
        "'--env-file', $envFile": "Compose helper lacks the dedicated env file",
        "compose --env-file $envFile": "cleanup lacks the dedicated env file",
        "Compose-Arguments @('build', '--pull', 'gate-runner')": "source build is missing",
        "--manifest-out": "source provenance generation is missing",
        "--verify-manifest": "source provenance verification is missing",
        "Set-DisposableProviderSecret -ApiKey $apiKey": "sealed secret helper is missing",
        "provider-secret-init', 'python', '-c'": "fixed stdin secret helper is missing",
        "headscale_provider_mutation_gate": "provider mutation evidence is missing",
        "remaining_disposable_volumes": "disposable cleanup evidence is missing",
        "real_member_devices_registered = 0": "real-member negative claim is missing",
    }
    for fragment, message in required_fragments.items():
        _require(fragment in runner_text, message)
    _require(
        "docker compose -p" not in runner_text and "docker compose -f" not in runner_text,
        "an unsealed docker compose invocation remains",
    )


def validate_static_configuration(root: Path) -> None:
    compose_text = (root / "deployment/overlay/compose.disposable.yml").read_text(encoding="utf-8")
    env_lines = [
        line.strip()
        for line in (root / "deployment/overlay/gate.env").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    dockerfile_text = (root / "deployment/overlay/Dockerfile.gate-runner").read_text(
        encoding="utf-8"
    )
    dockerignore_text = (root / "deployment/overlay/Dockerfile.gate-runner.dockerignore").read_text(
        encoding="utf-8"
    )
    runner_text = (root / "scripts/overlay/run_disposable_overlay_gate.ps1").read_text(
        encoding="utf-8"
    )

    _require(not env_lines, "gate.env must remain empty except for comments")
    _validate_compose(compose_text)
    _validate_dockerfile(dockerfile_text, dockerignore_text)
    _validate_runner(runner_text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--manifest-out")
    parser.add_argument("--verify-manifest")
    args = parser.parse_args()
    root = Path(args.repo_root).resolve(strict=True)

    if args.manifest_out and args.verify_manifest:
        raise SystemExit("choose either --manifest-out or --verify-manifest")
    try:
        validate_static_configuration(root)
        if args.verify_manifest:
            manifest = verify_manifest(root, Path(args.verify_manifest).resolve(strict=True))
        else:
            manifest = build_source_manifest(root)
            if args.manifest_out:
                write_manifest(Path(args.manifest_out).resolve(), manifest)
    except (GateValidationError, OSError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        "disposable Overlay Gate source seal passed "
        f"({manifest['file_count']} files, {manifest['source_tree_sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
