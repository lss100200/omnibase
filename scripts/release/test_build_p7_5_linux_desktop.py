from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PAYLOAD = _load(
    "build_p7_5_linux_desktop_payload",
    ROOT / "scripts/release/build_p7_5_linux_desktop_payload.py",
)
ORCHESTRATOR = _load(
    "omnibase_p75_linux_desktop",
    ROOT / "scripts/release/build_p7_5_linux_desktop.py",
)
BACKEND = _load(
    "omnibase_p75_linux_backend",
    ROOT / "packaging/linux/OmniBase.DesktopBackend/build_backend.py",
)


def _write(path: Path, value: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(mode)


class LinuxBackendBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="omnibase-p75-backend-")
        self.root = Path(self._temporary.name)
        self.repository = self.root / "repo"
        _write(
            self.repository
            / "packaging/linux/OmniBase.DesktopBackend/OmniBase.Desktop.Backend.spec",
            b"spec",
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @unittest.skipIf(os.name == "nt", "Linux builder executable bits require POSIX")
    def test_builder_invokes_pinned_pyinstaller_and_validates_output(self) -> None:
        distribution = self.root / "distribution"
        work = self.root / "work"
        distribution.parent.mkdir(parents=True, exist_ok=True)
        work.parent.mkdir(parents=True, exist_ok=True)
        seen: list[list[str]] = []

        def fake_run(command, **kwargs):
            seen.append(command)
            output = Path(command[command.index("--distpath") + 1])
            publish = output / BACKEND.EXPECTED_OUTPUT_NAME
            _write(publish / BACKEND.EXPECTED_EXECUTABLE, b"ELF", 0o755)

        with (
            patch.object(BACKEND, "_verify_build_runtime"),
            patch.object(BACKEND.subprocess, "run", fake_run),
        ):
            result = BACKEND.build_backend(self.repository, distribution, work)

        self.assertEqual(result["file_count"], 1)
        self.assertEqual(
            result["entrypoint"],
            str(
                distribution
                / BACKEND.EXPECTED_OUTPUT_NAME
                / BACKEND.EXPECTED_EXECUTABLE
            ),
        )
        self.assertEqual(seen[0][1:4], ["-m", "PyInstaller", "--distpath"])
        self.assertTrue(Path(result["entrypoint"]).stat().st_mode & 0o111)

    @unittest.skipIf(os.name == "nt", "POSIX hard-link and mode checks require Linux")
    def test_backend_output_rejects_hard_links_and_missing_execute_bit(self) -> None:
        publish = self.root / "publish"
        entrypoint = publish / BACKEND.EXPECTED_EXECUTABLE
        _write(entrypoint, b"ELF", 0o755)
        hard_link = publish / "duplicate"
        os.link(entrypoint, hard_link)
        with self.assertRaisesRegex(
            BACKEND.LinuxBackendBuildError, "publish_link_forbidden"
        ):
            BACKEND._validate_output(publish)

        hard_link.unlink()
        entrypoint.chmod(0o644)
        with self.assertRaisesRegex(
            BACKEND.LinuxBackendBuildError, "entrypoint_missing"
        ):
            BACKEND._validate_output(publish)


class LinuxDesktopOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="omnibase-p75-orchestrator-"
        )
        self.root = Path(self._temporary.name)
        self.repository = self.root / "repo"
        _write(self.repository / "desktop/scripts/package-linux.mjs", b"packager")
        self.inputs = {
            "frontend_standalone_dir": self.root / "frontend-standalone",
            "frontend_static_dir": self.root / "frontend-static",
            "frontend_public_dir": self.root / "frontend-public",
            "desktop_dist_dir": self.root / "desktop-dist",
            "runtime_host_script": self.root / "runtime-host.mjs",
            "backend_executable": self.root / "backend",
            "node_executable": self.root / "node",
            "desktop_project_dir": self.root / "desktop-project",
            "component_bundle_dir": self.root / "component-bundle",
        }
        for key in (
            "frontend_standalone_dir",
            "frontend_static_dir",
            "frontend_public_dir",
            "desktop_dist_dir",
            "desktop_project_dir",
            "component_bundle_dir",
        ):
            self.inputs[key].mkdir(parents=True)
        _write(self.inputs["runtime_host_script"], b"host")
        _write(self.inputs["backend_executable"], b"backend", 0o755)
        _write(self.inputs["node_executable"], b"node", 0o755)
        self.electron_dir = self.root / "electron"
        self.electron_dir.mkdir()
        self.electron_zip = self.electron_dir / ORCHESTRATOR.ELECTRON_ZIP_NAME
        _write(self.electron_zip, b"electron")
        self.electron_sha = hashlib.sha256(self.electron_zip.read_bytes()).hexdigest()
        self.component_report = {
            "bundle_sha256": "b" * 64,
            "file_count": 31,
            "output_bytes": 4096,
            "package_count": 10,
            "tree_sha256": "c" * 64,
        }
        self.inputs["component_bundle_sha256"] = self.component_report["bundle_sha256"]
        self.inputs["component_bundle_tree_sha256"] = self.component_report[
            "tree_sha256"
        ]

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_windows_host_fails_closed_before_touching_inputs(self) -> None:
        with patch.object(ORCHESTRATOR.sys, "platform", "win32"):
            with self.assertRaisesRegex(
                ORCHESTRATOR.LinuxDesktopBuildError, "requires_linux"
            ):
                ORCHESTRATOR.build_linux_desktop(
                    repo_root=self.repository,
                    **self.inputs,
                    electron_zip_dir=self.electron_dir,
                    electron_zip_sha256=self.electron_sha,
                    payload_dir=self.root / "payload",
                    output_dir=self.root / "output",
                )

    def test_staged_build_and_package_order_and_report_contract(self) -> None:
        payload_dir = self.root / "payload"
        output_dir = self.root / "output"
        output_dir.mkdir()
        calls: list[str] = []

        def fake_payload(**kwargs):
            staged = kwargs["output_dir"] / "desktop-build/project"
            staged.mkdir(parents=True)
            (staged / "package.json").write_text("{}", encoding="utf-8")
            runtime = kwargs["output_dir"] / "runtime"
            runtime.mkdir()
            (runtime / "runtime-manifest.json").write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                output_dir=kwargs["output_dir"],
                runtime_manifest_sha256="a" * 64,
                runtime_file_count=3,
                runtime_total_bytes=12,
            )

        def fake_run(command, *, cwd, timeout, label):
            calls.append(label)
            if label == "staged_build":
                _write(payload_dir / "desktop-build/project/dist/main.js", b"compiled")
            elif label == "electron_package":
                target = output_dir / "OmniBase-linux-x64"
                _write(target / "OmniBase", b"electron", 0o755)
                _write(target / "resources/app.asar", b"asar")

        def fake_ordinary_file(path, *, code, executable=False):
            candidate = Path(path).absolute()
            if not candidate.is_file():
                raise ORCHESTRATOR.LinuxDesktopBuildError(code)
            return candidate

        with (
            patch.object(ORCHESTRATOR.sys, "platform", "linux"),
            patch.object(ORCHESTRATOR, "ELECTRON_ZIP_SHA256", self.electron_sha),
            patch.object(ORCHESTRATOR, "build_linux_payload", fake_payload),
            patch.object(
                ORCHESTRATOR,
                "validate_component_bundle",
                return_value=self.component_report,
            ),
            patch.object(ORCHESTRATOR, "_run", fake_run),
            patch.object(ORCHESTRATOR, "_ordinary_file", fake_ordinary_file),
        ):
            report = ORCHESTRATOR.build_linux_desktop(
                repo_root=self.repository,
                **self.inputs,
                electron_zip_dir=self.electron_dir,
                electron_zip_sha256=self.electron_sha,
                payload_dir=payload_dir,
                output_dir=output_dir,
            )

        self.assertEqual(calls, ["staged_install", "staged_build", "electron_package"])
        self.assertFalse(report["distribution_package"])
        self.assertFalse(report["lifecycle_accepted"])
        self.assertEqual(report["component_bundle"], self.component_report)
        self.assertTrue((self.root / "payload-report.json").is_file())

    def test_component_bundle_is_required_before_any_build_side_effect(self) -> None:
        missing = self.root / "missing-component"
        (self.root / "output").mkdir()

        def fake_ordinary_file(path, *, code, executable=False):
            candidate = Path(path).absolute()
            if not candidate.is_file():
                raise ORCHESTRATOR.LinuxDesktopBuildError(code)
            return candidate

        with (
            patch.object(ORCHESTRATOR.sys, "platform", "linux"),
            patch.object(ORCHESTRATOR, "ELECTRON_ZIP_SHA256", self.electron_sha),
            patch.object(ORCHESTRATOR, "_ordinary_file", fake_ordinary_file),
        ):
            with self.assertRaisesRegex(
                ORCHESTRATOR.LinuxDesktopBuildError,
                "linux_desktop_component_bundle_invalid",
            ):
                ORCHESTRATOR.build_linux_desktop(
                    repo_root=self.repository,
                    **{**self.inputs, "component_bundle_dir": missing},
                    electron_zip_dir=self.electron_dir,
                    electron_zip_sha256=self.electron_sha,
                    payload_dir=self.root / "payload",
                    output_dir=self.root / "output",
                )

    def test_invalid_component_bundle_is_rejected_before_payload_build(self) -> None:
        output_dir = self.root / "output"
        output_dir.mkdir()

        def fake_ordinary_file(path, *, code, executable=False):
            candidate = Path(path).absolute()
            if not candidate.is_file():
                raise ORCHESTRATOR.LinuxDesktopBuildError(code)
            return candidate

        with (
            patch.object(ORCHESTRATOR.sys, "platform", "linux"),
            patch.object(ORCHESTRATOR, "ELECTRON_ZIP_SHA256", self.electron_sha),
            patch.object(ORCHESTRATOR, "_ordinary_file", fake_ordinary_file),
            patch.object(
                ORCHESTRATOR,
                "validate_component_bundle",
                side_effect=PAYLOAD.LinuxPayloadError(
                    "linux_payload_component_bundle_invalid"
                ),
            ),
            patch.object(ORCHESTRATOR, "build_linux_payload") as payload_build,
        ):
            with self.assertRaisesRegex(
                ORCHESTRATOR.LinuxDesktopBuildError,
                "linux_desktop_component_bundle_invalid",
            ):
                ORCHESTRATOR.build_linux_desktop(
                    repo_root=self.repository,
                    **self.inputs,
                    electron_zip_dir=self.electron_dir,
                    electron_zip_sha256=self.electron_sha,
                    payload_dir=self.root / "payload",
                    output_dir=output_dir,
                )
        payload_build.assert_not_called()

    def test_component_bundle_digest_is_bound_before_payload_build(self) -> None:
        output_dir = self.root / "output"
        output_dir.mkdir()

        def fake_ordinary_file(path, *, code, executable=False):
            candidate = Path(path).absolute()
            if not candidate.is_file():
                raise ORCHESTRATOR.LinuxDesktopBuildError(code)
            return candidate

        with (
            patch.object(ORCHESTRATOR.sys, "platform", "linux"),
            patch.object(ORCHESTRATOR, "ELECTRON_ZIP_SHA256", self.electron_sha),
            patch.object(ORCHESTRATOR, "_ordinary_file", fake_ordinary_file),
            patch.object(
                ORCHESTRATOR,
                "validate_component_bundle",
                return_value=self.component_report,
            ),
            patch.object(ORCHESTRATOR, "build_linux_payload") as payload_build,
        ):
            with self.assertRaisesRegex(
                ORCHESTRATOR.LinuxDesktopBuildError,
                "linux_desktop_component_bundle_digest_mismatch",
            ):
                ORCHESTRATOR.build_linux_desktop(
                    repo_root=self.repository,
                    **{
                        **self.inputs,
                        "component_bundle_tree_sha256": "d" * 64,
                    },
                    electron_zip_dir=self.electron_dir,
                    electron_zip_sha256=self.electron_sha,
                    payload_dir=self.root / "payload",
                    output_dir=output_dir,
                )
        payload_build.assert_not_called()

    def test_outputs_inside_repository_are_rejected(self) -> None:
        with patch.object(ORCHESTRATOR.sys, "platform", "linux"):
            with self.assertRaisesRegex(
                ORCHESTRATOR.LinuxDesktopBuildError, "output_inside_repository"
            ):
                ORCHESTRATOR.build_linux_desktop(
                    repo_root=self.repository,
                    **self.inputs,
                    electron_zip_dir=self.electron_dir,
                    electron_zip_sha256=self.electron_sha,
                    payload_dir=self.repository / "payload",
                    output_dir=self.root / "output",
                )


if __name__ == "__main__":
    unittest.main()
