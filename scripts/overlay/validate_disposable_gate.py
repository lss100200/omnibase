"""Validate that the disposable Overlay Gate cannot inherit root Compose env."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    args = parser.parse_args()
    root = Path(args.repo_root).resolve(strict=True)
    compose = root / "deployment" / "overlay" / "compose.disposable.yml"
    env_file = root / "deployment" / "overlay" / "gate.env"
    runner = root / "scripts" / "overlay" / "run_disposable_overlay_gate.ps1"

    compose_text = compose.read_text(encoding="utf-8")
    env_lines = [
        line.strip()
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    runner_text = runner.read_text(encoding="utf-8")

    if env_lines:
        raise SystemExit("gate.env must remain empty except for comments")
    if "${OMNIBASE_GATE_" in compose_text:
        raise SystemExit(
            "Gate Runner image or volume still permits environment substitution"
        )
    if "image: omnibase-backend:latest" not in compose_text:
        raise SystemExit("Gate Runner image is not fixed")
    if "name: omnibase_backend_venv" not in compose_text:
        raise SystemExit("Gate Runner venv volume is not fixed")
    if "provider-secrets:/run/omnibase-provider-secrets:ro" not in compose_text:
        raise SystemExit("Node Daemon provider secret volume is not read-only")
    gate_runner_block = compose_text.split("  gate-runner:", 1)[1].split(
        "networks:", 1
    )[0]
    if "provider-secrets" in gate_runner_block:
        raise SystemExit("Gate Runner must not receive the provider secret volume")
    if "--headscale-api-key-file" not in compose_text:
        raise SystemExit("Node Daemon Headscale API key file binding is missing")
    if "'--env-file', $envFile" not in runner_text:
        raise SystemExit("Compose-Arguments does not pass the dedicated env file")
    if "compose --env-file $envFile" not in runner_text:
        raise SystemExit("finally cleanup does not pass the dedicated env file")
    if "docker compose -p" in runner_text or "docker compose -f" in runner_text:
        raise SystemExit("an unsealed docker compose invocation remains")
    if "Set-DisposableProviderSecret -ApiKey $apiKey" not in runner_text:
        raise SystemExit("Headscale API key is not injected through the sealed helper")
    if "provider-secret-init', 'python', '-c'" not in runner_text:
        raise SystemExit(
            "provider secret injection does not use the fixed stdin helper"
        )
    if "headscale_provider_mutation_gate" not in runner_text:
        raise SystemExit("provider mutation evidence is not sealed in the report")
    if "remaining_disposable_volumes" not in runner_text:
        raise SystemExit("disposable cleanup evidence is missing")
    print("disposable Overlay Gate configuration seal passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
