from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import unittest
from pathlib import Path


def _module():
    path = Path(__file__).with_name("build_p7_5_linux_desktop_payload.py")
    spec = importlib.util.spec_from_file_location("omnibase_p75_linux_payload", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(mode)


def _fixture(tmp_path: Path) -> dict[str, Path]:
    frontend = tmp_path / "frontend-standalone"
    static = tmp_path / "frontend-static"
    public = tmp_path / "frontend-public"
    desktop_dist = tmp_path / "desktop-dist"
    project = tmp_path / "desktop-project"
    _write(frontend / "server.js", b"frontend-server")
    _write(frontend / "node_modules/client-only/index.js", b"")
    _write(static / "chunks/app.js", b"static")
    _write(public / "brand/logo.svg", b"<svg />")
    _write(desktop_dist / "main.js", b"desktop-main")
    _write(desktop_dist / "runtime/p34-sandbox-helper.js", b"helper")
    _write(project / "package.json", b'{"name":"@omnibase/desktop"}\n')
    _write(project / "pnpm-lock.yaml", b"lockfileVersion: 9\n")
    _write(project / "tsconfig.json", b"{}\n")
    _write(project / "tsconfig.build.json", b"{}\n")
    _write(
        project / "src/runtime/trusted-manifest.ts",
        b'export const PINNED_RUNTIME_MANIFEST_SHA256 = "__OMNIBASE_RUNTIME_MANIFEST_SHA256__";\n',
    )
    backend = tmp_path / "backend"
    node = tmp_path / "node"
    host = tmp_path / "omnibase-runtime-host.mjs"
    _write(backend, b"backend", 0o755)
    _write(node, b"node", 0o755)
    _write(host, b"host")
    return {
        "frontend_standalone_dir": frontend,
        "frontend_static_dir": static,
        "frontend_public_dir": public,
        "desktop_dist_dir": desktop_dist,
        "runtime_host_script": host,
        "backend_executable": backend,
        "node_executable": node,
        "desktop_project_dir": project,
    }


def _build(module, fixture: dict[str, Path], output: Path, **overrides):
    return module.build_linux_payload(**fixture, output_dir=output, **overrides)


@unittest.skipIf(os.name == "nt", "Linux executable-bit contract requires a POSIX host")
class LinuxPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._temporary = tempfile.TemporaryDirectory(prefix="omnibase-p75-linux-")
        self.tmp_path = Path(self._temporary.name)
        self.module = _module()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_linux_payload_manifest_and_staged_trust_are_exact(self) -> None:
        fixture = _fixture(self.tmp_path)
        trusted = fixture["desktop_project_dir"] / "src/runtime/trusted-manifest.ts"
        original = trusted.read_bytes()
        output = self.tmp_path / "payload"
        result = _build(self.module, fixture, output)

        manifest_raw = (output / "runtime/runtime-manifest.json").read_bytes()
        manifest = json.loads(manifest_raw)
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(
            manifest["entrypoint"],
            {"path": "node/node", "args": ["omnibase-runtime-host.mjs"]},
        )
        self.assertEqual(
            result.runtime_manifest_sha256, hashlib.sha256(manifest_raw).hexdigest()
        )
        self.assertEqual(trusted.read_bytes(), original)
        staged = (
            output / "desktop-build/project/src/runtime/trusted-manifest.ts"
        ).read_text()
        self.assertNotIn(self.module.TRUST_TOKEN, staged)
        self.assertIn(result.runtime_manifest_sha256, staged)

        config = json.loads((output / "runtime/runtime-host.json").read_bytes())
        self.assertEqual(
            set(config),
            {
                "schema_version",
                "backend",
                "frontend",
                "node",
                "application_version",
                "backend_port",
                "frontend_port",
                "startup_timeout_seconds",
                "shutdown_timeout_seconds",
            },
        )
        self.assertEqual(config["frontend"]["path"], "frontend/server.js")
        self.assertEqual(config["node"]["path"], "node/node")
        self.assertTrue((output / "runtime/node/node").stat().st_mode & 0o111)
        self.assertFalse((output / "runtime/frontend/server.js").stat().st_mode & 0o111)

    def test_linux_payload_requires_executable_backend_and_node(self) -> None:
        fixture = _fixture(self.tmp_path)
        fixture["backend_executable"].chmod(0o644)
        with self.assertRaisesRegex(
            self.module.LinuxPayloadError, "executable_bit_missing"
        ):
            _build(self.module, fixture, self.tmp_path / "payload")

    def test_linux_payload_rejects_existing_output_without_touching_it(self) -> None:
        fixture = _fixture(self.tmp_path)
        output = self.tmp_path / "payload"
        output.mkdir()
        marker = output / "marker"
        marker.write_bytes(b"keep")
        with self.assertRaisesRegex(self.module.LinuxPayloadError, "output_exists"):
            _build(self.module, fixture, output)
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_linux_payload_rejects_sensitive_input_paths(self) -> None:
        fixture = _fixture(self.tmp_path)
        _write(fixture["frontend_standalone_dir"] / ".env.local", b"secret")
        with self.assertRaisesRegex(
            self.module.LinuxPayloadError, "sensitive_path_forbidden"
        ):
            _build(self.module, fixture, self.tmp_path / "payload")


if __name__ == "__main__":
    unittest.main()
